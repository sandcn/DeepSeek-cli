"""截图文字识别（OCR）——把画面里的文字变成可点击的坐标。

自绘界面（游戏、canvas、图片按钮）没有可枚举的控件，模板匹配又需要先有
模板；OCR 直接「认字」，把识别出的每个词连同屏幕矩形返回，模型即可按文字
定位（``find_text``）后交给输入 op 点击。

后端采用**注册表 + 择优探测**（与截图 / 输入后端同一套扩展范式）：

  - ``windows-ocr``：Windows 10+ 系统自带 OCR（``Windows.Media.Ocr``），
    经 PowerShell 调 WinRT，**零安装依赖**；
  - ``tesseract``：跨平台的 Tesseract CLI（``tesseract image stdout tsv``）；
  - ``macos-vision``：macOS 的 Vision 框架（需 pyobjc），归一化坐标换算为像素。

新增后端只需实现 ``name`` / ``available()`` / ``recognize(image_path, lang)``
并 :func:`register_ocr_backend`。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

from . import png

logger = logging.getLogger(__name__)

#: OCR 子进程超时（秒）
_COMMAND_TIMEOUT = 60.0

#: 单次识别返回的文本框上限（防御超大画面输出爆炸）
MAX_BOXES = 5000


class OcrError(RuntimeError):
    """OCR 失败（无可用后端 / 识别命令失败 / 结果无法解析）。"""


@dataclass(frozen=True)
class TextBox:
    """一个识别出的文本块（左上角为图像原点）。"""

    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float = 0.0

    @property
    def center_x(self) -> int:
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        return self.top + self.height // 2

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "x": self.left,
            "y": self.top,
            "width": self.width,
            "height": self.height,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "confidence": round(self.confidence, 3),
        }


# ── 后端注册表 ──────────────────────────────────────────

_BACKENDS: list = []
_LOCK = threading.RLock()
_BUILTINS_LOADED = False


def register_ocr_backend(backend, *, prepend: bool = False) -> Callable[[], None]:
    """注册 OCR 后端，返回幂等撤销函数。"""
    with _LOCK:
        if prepend:
            _BACKENDS.insert(0, backend)
        else:
            _BACKENDS.append(backend)

        def _undo() -> None:
            with _LOCK:
                if backend in _BACKENDS:
                    _BACKENDS.remove(backend)

        return _undo


def available_backends() -> list:
    """当前已注册的 OCR 后端（含不可用的，按探测顺序）。"""
    _ensure_builtins()
    with _LOCK:
        return list(_BACKENDS)


def resolve_backend():
    """返回第一个可用的 OCR 后端（无则 None）。"""
    for backend in available_backends():
        try:
            if backend.available():
                return backend
        except Exception:  # pragma: no cover - 探测异常不应中断
            logger.debug("OCR 后端 %s 探测失败", getattr(backend, "name", backend),
                         exc_info=True)
    return None


def available() -> bool:
    """当前环境是否有可用的 OCR 后端。"""
    return resolve_backend() is not None


def backend_hint() -> str:
    """无可用后端时的安装提示。"""
    return (
        "当前环境没有可用的 OCR 后端：Windows 10+ 自带 OCR（无需安装）；"
        "其它平台可安装 Tesseract（Debian/Ubuntu: apt install tesseract-ocr，"
        "macOS: brew install tesseract，并带语言包）"
    )


def recognize(image_path: str, *, lang: str | None = None) -> list[TextBox]:
    """对图片做 OCR，返回文本框列表（左上角为图像原点）。

    Raises:
        OcrError: 没有可用后端，或识别失败。
    """
    if not os.path.isfile(image_path):
        raise OcrError(f"待识别图片不存在: {image_path}")
    backend = resolve_backend()
    if backend is None:
        raise OcrError(backend_hint())
    try:
        boxes = list(backend.recognize(image_path, lang))
    except OcrError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise OcrError(
            f"OCR 识别失败（后端 {getattr(backend, 'name', '?')}）: {exc}"
        ) from exc
    return _sanitize(boxes)


def _sanitize(boxes: Sequence[TextBox]) -> list[TextBox]:
    """过滤空文本块并限制条数。"""
    cleaned = [box for box in boxes if box.text.strip()]
    return cleaned[:MAX_BOXES]


# ── 文本查找 ────────────────────────────────────────────

def find_text(boxes: Sequence[TextBox], needle: str, *,
              case_sensitive: bool = False) -> list[TextBox]:
    """按文本查找文本框（支持跨词短语：同一行相邻词合并后匹配）。

    先做「单框包含」匹配；没有命中时按行把相邻词拼成短语再匹配（OCR 常把
    一句话拆成多个词，如把 ``保存 文件`` 拆成两框）。
    """
    query = str(needle or "")
    if not query.strip():
        raise OcrError("find_text 需要非空的查找文本")
    target = query if case_sensitive else query.casefold()
    direct: list[TextBox] = []
    for box in boxes:
        haystack = box.text if case_sensitive else box.text.casefold()
        if target in haystack:
            direct.append(box)
    if direct:
        return direct
    return _suppress_contained(_phrase_matches(boxes, target, case_sensitive))


def _suppress_contained(boxes: Sequence[TextBox]) -> list[TextBox]:
    """去掉被其它匹配框完全包含的结果（跨词匹配会产生多个嵌套包围盒）。"""
    kept: list[TextBox] = []
    for box in boxes:
        if any(other is not box and _contains(other, box) for other in boxes):
            continue
        kept.append(box)
    return kept


def _contains(outer: TextBox, inner: TextBox) -> bool:
    return (outer.left <= inner.left and outer.top <= inner.top
            and outer.left + outer.width >= inner.left + inner.width
            and outer.top + outer.height >= inner.top + inner.height)


def _phrase_matches(boxes: Sequence[TextBox], target: str,
                    case_sensitive: bool) -> list[TextBox]:
    """在同一行内把相邻词拼接，匹配跨词短语，返回合并后的包围盒。"""
    matches: list[TextBox] = []
    for line in _group_lines(boxes):
        ordered = sorted(line, key=lambda item: item.left)
        count = len(ordered)
        for start in range(count):
            buffer = ""
            for end in range(start, count):
                word = ordered[end].text
                buffer = (buffer + word) if not buffer else (buffer + " " + word)
                haystack = buffer if case_sensitive else buffer.casefold()
                if target in haystack:
                    matches.append(_union(ordered[start:end + 1]))
                    break
                if len(haystack) > len(target) + 40:
                    break
    return matches


def _group_lines(boxes: Sequence[TextBox]) -> list[list[TextBox]]:
    """把文本框按纵向重叠分组成行。"""
    lines: list[list[TextBox]] = []
    for box in sorted(boxes, key=lambda item: (item.top, item.left)):
        placed = False
        for line in lines:
            reference = line[0]
            overlap = (min(reference.top + reference.height, box.top + box.height)
                       - max(reference.top, box.top))
            if overlap > min(reference.height, box.height) * 0.5:
                line.append(box)
                placed = True
                break
        if not placed:
            lines.append([box])
    return lines


def _union(boxes: Sequence[TextBox]) -> TextBox:
    """把相邻文本框合并为一个包围盒（文本用空格连接）。"""
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.left + box.width for box in boxes)
    bottom = max(box.top + box.height for box in boxes)
    confidence = min((box.confidence for box in boxes), default=0.0)
    return TextBox(
        text=" ".join(box.text for box in boxes),
        left=left, top=top, width=right - left, height=bottom - top,
        confidence=confidence,
    )


# ── Tesseract 后端 ──────────────────────────────────────

class TesseractBackend:
    """Tesseract CLI 后端（跨平台，需自行安装）。"""

    name = "tesseract"

    def __init__(self, path: str | None = None):
        self._path = path

    def available(self) -> bool:
        return bool(self._path or shutil.which("tesseract"))

    def recognize(self, image_path: str, lang: str | None = None) -> list[TextBox]:
        binary = self._path or shutil.which("tesseract")
        if not binary:
            raise OcrError("未找到 tesseract 可执行文件")
        command = [binary, image_path, "stdout", "-l", lang or "eng", "tsv"]
        completed = _run(command)
        if completed is None:
            raise OcrError("tesseract 执行超时或不可用")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            raise OcrError(
                f"tesseract 识别失败: {detail[-1] if detail else completed.returncode}"
            )
        return parse_tesseract_tsv(completed.stdout or "")


def parse_tesseract_tsv(text: str) -> list[TextBox]:
    """解析 tesseract 的 TSV 输出（取 level=5 的词级结果）。"""
    boxes: list[TextBox] = []
    lines = text.splitlines()
    for line in lines[1:]:  # 跳过表头
        columns = line.split("\t")
        if len(columns) < 12:
            continue
        try:
            level = int(columns[0])
            left, top, width, height = (int(columns[6]), int(columns[7]),
                                        int(columns[8]), int(columns[9]))
            confidence = float(columns[10])
        except (ValueError, IndexError):
            continue
        word = columns[11].strip()
        if level != 5 or not word or confidence < 0:
            continue
        boxes.append(TextBox(word, left, top, width, height, confidence / 100.0))
    return boxes


# ── Windows 系统 OCR 后端 ───────────────────────────────

#: 经 PowerShell 调 WinRT OCR 的脚本（$Path 由命令行注入，$Lang 可为空）
_WINDOWS_OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($op, $t) {
    $m = $asTaskGeneric.MakeGenericMethod($t)
    $task = $m.Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:BASHOPT_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
if ($env:BASHOPT_OCR_LANG) {
    $language = New-Object Windows.Globalization.Language($env:BASHOPT_OCR_LANG)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
} else {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($engine -eq $null) { Write-Output 'null'; exit 3 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$out = @()
foreach ($line in $result.Lines) {
    foreach ($word in $line.Words) {
        $r = $word.BoundingRect
        $out += [pscustomobject]@{
            text = $word.Text
            x = [int]$r.X; y = [int]$r.Y
            w = [int]$r.Width; h = [int]$r.Height
        }
    }
}
$json = ConvertTo-Json -InputObject @($out) -Compress
Write-Output $json
"""


class WindowsOcrBackend:
    """Windows 10+ 系统 OCR（``Windows.Media.Ocr``，经 PowerShell WinRT）。"""

    name = "windows-ocr"

    def __init__(self, powershell: str | None = None):
        self._powershell = powershell

    def available(self) -> bool:
        return sys.platform.startswith(("win", "cygwin", "msys")) and bool(
            self._powershell or _find_powershell())

    def recognize(self, image_path: str, lang: str | None = None) -> list[TextBox]:
        binary = self._powershell or _find_powershell()
        if not binary:
            raise OcrError("未找到 powershell 可执行文件")
        env = dict(os.environ)
        env["BASHOPT_OCR_IMAGE"] = _windows_path(image_path)
        env["BASHOPT_OCR_LANG"] = lang or ""
        completed = _run(
            [binary, "-NoProfile", "-NonInteractive", "-Command",
             _WINDOWS_OCR_SCRIPT],
            env=env, timeout=_COMMAND_TIMEOUT,
        )
        if completed is None:
            raise OcrError("PowerShell OCR 执行超时或不可用")
        output = (completed.stdout or "").strip()
        if completed.returncode == 3 or output == "null":
            raise OcrError(
                "系统 OCR 引擎不可用（未安装任何 OCR 语言包）：请在"
                "「设置 → 时间和语言 → 语言」添加语言包，或改用 tesseract"
            )
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            raise OcrError(
                f"PowerShell OCR 失败: {detail[-1] if detail else completed.returncode}"
            )
        return parse_windows_ocr_json(output)


def _windows_path(path: str) -> str:
    """把路径转成 Windows API 可用的形式。

    Cygwin/MSYS2 下 ``/tmp/x.png`` 这类 POSIX 路径 Windows 原生 API 无法打开，
    需经 ``cygpath -w`` 映射为 ``E:\\tmp\\x.png``；原生 Windows 直接用绝对路径。
    """
    absolute = os.path.abspath(path)
    if not sys.platform.startswith(("cygwin", "msys")):
        return absolute
    converter = shutil.which("cygpath")
    if not converter:
        return absolute
    completed = _run([converter, "-w", absolute])
    if completed is None or completed.returncode != 0:
        return absolute
    converted = (completed.stdout or "").strip()
    return converted or absolute


def _find_powershell() -> str | None:
    for name in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def parse_windows_ocr_json(text: str) -> list[TextBox]:
    """解析 Windows OCR 输出的 JSON（单对象或数组）。"""
    raw = text.strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OcrError(f"OCR 结果不是合法 JSON: {exc}") from exc
    if payload is None:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    boxes: list[TextBox] = []
    for item in payload:
        try:
            boxes.append(TextBox(
                text=str(item.get("text", "")),
                left=int(item.get("x", 0)), top=int(item.get("y", 0)),
                width=int(item.get("w", 0)), height=int(item.get("h", 0)),
            ))
        except (TypeError, ValueError):
            continue
    return boxes


# ── macOS Vision 后端 ───────────────────────────────────

class MacVisionBackend:
    """macOS Vision 框架 OCR（需 pyobjc；坐标由归一化换算为像素）。"""

    name = "macos-vision"

    def available(self) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            import Vision  # noqa: F401
            import Quartz  # noqa: F401
        except ImportError:
            return False
        return True

    def recognize(self, image_path: str, lang: str | None = None) -> list[TextBox]:
        import Quartz
        import Vision

        url = Quartz.CFURLCreateFromFileSystemRepresentation(
            None, os.path.abspath(image_path).encode("utf-8"), len(os.path.abspath(image_path)), False)
        source = Quartz.CGImageSourceCreateWithURL(url, None)
        if source is None:
            raise OcrError(f"无法读取图片: {image_path}")
        image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
        if image is None:
            raise OcrError(f"无法解码图片: {image_path}")
        width, height = _pixel_size(image_path)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(1)  # VNRequestTextRecognitionLevelAccurate
        if lang:
            request.setRecognitionLanguages_([lang])
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
        handler.performRequests_error_([request], None)
        boxes: list[TextBox] = []
        for observation in request.results() or []:
            candidate = observation.topCandidates_(1)
            if not candidate:
                continue
            text = candidate[0].string()
            rect = observation.boundingBox()
            x = float(rect.origin.x) * width
            w = float(rect.size.width) * width
            h = float(rect.size.height) * height
            # Vision 原点在左下，转成左上原点
            y = (1.0 - float(rect.origin.y) - float(rect.size.height)) * height
            boxes.append(TextBox(text, int(x), int(y), int(w), int(h),
                                 float(candidate[0].confidence())))
        return boxes


def _pixel_size(path: str) -> tuple[int, int]:
    """读取 PNG 像素尺寸（失败时回退 1，使归一化坐标不放大）。"""
    try:
        return png.read_png_size(path)
    except (OSError, ValueError):
        return 1, 1


# ── 子进程执行 ──────────────────────────────────────────

def _run(command: list[str], *, env=None, timeout: float = _COMMAND_TIMEOUT):
    """执行子进程（失败返回 None，不抛异常）。"""
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("OCR 命令执行失败 %s: %s", command[0], exc)
        return None


def _ensure_builtins() -> None:
    """幂等注册内置后端（延迟导入避免循环依赖）。"""
    global _BUILTINS_LOADED
    with _LOCK:
        if _BUILTINS_LOADED:
            return
        _BUILTINS_LOADED = True
    for backend in (WindowsOcrBackend(), TesseractBackend(), MacVisionBackend()):
        register_ocr_backend(backend)


__all__ = [
    "MAX_BOXES",
    "MacVisionBackend",
    "OcrError",
    "TextBox",
    "TesseractBackend",
    "WindowsOcrBackend",
    "available",
    "available_backends",
    "backend_hint",
    "find_text",
    "parse_tesseract_tsv",
    "parse_windows_ocr_json",
    "recognize",
    "register_ocr_backend",
    "resolve_backend",
]
