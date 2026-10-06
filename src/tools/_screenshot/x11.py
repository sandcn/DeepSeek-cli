"""Linux / POSIX 图形会话截图后端（X11 优先，Wayland 会话回退）。

窗口定位（按可用工具择优）：
  1. ``xdotool search --onlyvisible --pid``（按进程树逐个 PID 搜索）
  2. ``wmctrl -lp``（窗口列表含 PID）

像素获取：
  1. ImageMagick ``import -window <id>``（或新版 ``magick import``）
  2. ``xwd -id <id>`` + ``convert``（ImageMagick 转换）
  3. ``gnome-screenshot -w``（Wayland/无窗口 ID 时的活动窗口兜底）

裁剪：``crop`` 指定时对上述命令产出的 PNG 解码裁剪后原子重写。

本后端不主动安装任何工具；工具缺失时抛出带安装提示的错误，由使用方决定。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from . import png, proctree, transform
from .grid import paint_grid_on_png_file
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropRegion
from .windows import (
    DEFAULT_SELECTOR,
    WindowControlRequest,
    WindowInfo,
    mark_main,
    pick_window,
)

logger = logging.getLogger(__name__)

#: 单条外部命令超时（秒）
_COMMAND_TIMEOUT = 30.0
#: 窗口搜索命令超时（秒）
_SEARCH_TIMEOUT = 15.0
#: 窗口控制（移动 / 缩放 / 最大化等）后等待状态生效的时间（秒）
_CONTROL_SETTLE_SECONDS = 0.15


@dataclass
class _X11Window:
    """X11 窗口条目（``x`` / ``y`` 为窗口左上角的屏幕坐标）。"""

    window_id: str
    pid: int
    title: str
    width: int
    height: int
    x: int = 0
    y: int = 0
    class_name: str = ""

    @property
    def area(self) -> int:
        return self.width * self.height


def to_window_info(window: _X11Window, order: int = 0) -> WindowInfo:
    """X11 窗口条目 → 通用窗口描述（句柄为十进制窗口 ID）。"""
    return WindowInfo(
        handle=window_id_int(window.window_id),
        pid=window.pid,
        title=window.title,
        class_name=window.class_name,
        width=window.width,
        height=window.height,
        left=window.x,
        top=window.y,
        order=order,
    )


def window_id_int(window_id: str) -> int:
    """把 xdotool / wmctrl 的窗口 ID 文本解析为整数（十六进制或十进制）。"""
    text = str(window_id).strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        return 0


class X11Backend:
    """X11/Wayland 会话截图后端。"""

    name = "x11"

    def supports(self) -> bool:
        return (
            not sys.platform.startswith(("win", "cygwin", "msys"))
            and sys.platform != "darwin"
        )

    def capture(self, pid: int, path: str,
                crop: CropRegion | None = None,
                window: str | None = None,
                grid: int | None = None) -> CaptureResult:
        """截取 ``pid``（及其子进程）的窗口到 ``path``（PNG）。

        Args:
            pid: 目标进程 PID（含子进程）。
            path: 输出 PNG 路径。
            crop: 可选裁剪区域（对截图工具产出的 PNG 解码裁剪）；越界抛
                ``CropError``。
            window: 可选窗口选择器（缺省 = 主窗口）；无匹配抛 ``SelectorError``。
            grid: 可选坐标网格步长（像素，``0`` = 自动）。
        """
        pids = proctree.collect_process_tree(pid)
        if not pids:
            raise ScreenshotError(f"进程号非法，无法截图: {pid}")
        windows = self._find_windows(pids)
        if not windows:
            raise NoWindowError(self._no_window_hint(pid))
        infos = mark_main([to_window_info(item, index)
                           for index, item in enumerate(windows)])
        target_info = pick_window(infos, window)
        target = _find_by_info(windows, target_info)
        self._grab(target.window_id, path)
        width, height = self._read_size(path, target)
        if crop is not None:
            width, height = transform.apply_crop_to_png_file(path, crop)
        if grid is not None:
            width, height, _step = paint_grid_on_png_file(path, int(grid))
        return CaptureResult(
            path=path,
            width=width,
            height=height,
            window_pid=target.pid,
            window_title=target.title,
            backend=self.name,
            window_handle=target_info.handle,
            windows_total=len(windows),
            window_selector=window or DEFAULT_SELECTOR,
            window_summary=target_info.summary(),
        )

    def list_windows(self, pid: int) -> list[WindowInfo]:
        """枚举该进程树的全部可操作窗口（``op=windows`` 数据源）。"""
        pids = proctree.collect_process_tree(pid)
        if not pids:
            return []
        windows = self._find_windows(pids)
        return mark_main([to_window_info(item, index)
                          for index, item in enumerate(windows)])

    def control(self, pid: int, request: WindowControlRequest) -> dict:
        """对被选窗口执行激活 / 最大化 / 最小化 / 还原 / 关闭 / 移动 / 缩放。"""
        return control_window(pid, request, runner=self._run)

    # ── 窗口发现 ─────────────────────────────────────────

    def _find_windows(self, pids: list[int]) -> list[_X11Window]:
        windows = _xdotool_windows(pids)
        if windows:
            return windows
        return _wmctrl_windows(pids)

    def _no_window_hint(self, pid: int) -> str:
        missing = [
            name for name in ("xdotool", "wmctrl", "import", "xwd")
            if shutil.which(name) is None
        ]
        hint = ""
        if missing:
            hint = f"（当前会话缺少工具: {', '.join(missing)}，可安装 xdotool / wmctrl / imagemagick / x11-apps）"
        session = os.environ.get("XDG_SESSION_TYPE", "")
        wayland_note = "；Wayland 原生窗口无法按 PID 定位，可在 XWayland 下运行程序" if session == "wayland" else ""
        return (
            f"未找到进程 {pid} 及其子进程的可见窗口{hint}{wayland_note}。"
            f"纯命令行进程没有图形窗口"
        )

    # ── 像素获取 ─────────────────────────────────────────

    def _grab(self, window_id: str, path: str) -> None:
        import_bin = shutil.which("import")
        if import_bin:
            _run_checked([import_bin, "-window", window_id, path], "ImageMagick import")
            return
        magick_bin = shutil.which("magick")
        if magick_bin:
            _run_checked([magick_bin, "import", "-window", window_id, path], "ImageMagick magick import")
            return
        xwd_bin = shutil.which("xwd")
        convert_bin = shutil.which("convert")
        if xwd_bin and convert_bin:
            _xwd_grab(xwd_bin, convert_bin, window_id, path)
            return
        gnome = shutil.which("gnome-screenshot")
        if gnome:
            _run_checked([gnome, "-w", "-f", path], "gnome-screenshot")
            return
        raise ScreenshotError(
            "当前会话无可用截图工具：请安装 ImageMagick（import）或 x11-apps（xwd）+ convert，"
            "或 gnome-screenshot"
        )

    @staticmethod
    def _read_size(path: str, target: _X11Window) -> tuple[int, int]:
        """读取产物尺寸；文件解析失败时回退窗口几何尺寸。"""
        return png.read_png_size_or(path, (target.width, target.height))


def _xdotool_windows(pids: list[int]) -> list[_X11Window]:
    """用 xdotool 按 PID 搜索窗口（工具不可用返回空表）。"""
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return []
    windows: list[_X11Window] = []
    seen: set[str] = set()
    for pid in pids:
        completed = _run([xdotool, "search", "--onlyvisible", "--pid", str(pid)], _SEARCH_TIMEOUT)
        if completed is None or completed.returncode != 0:
            continue
        for window_id in completed.stdout.split():
            window_id = window_id.strip()
            if not window_id or window_id in seen:
                continue
            seen.add(window_id)
            x, y, width, height = _window_geometry(xdotool, window_id)
            if width <= 0 or height <= 0:
                continue
            windows.append(_X11Window(
                window_id=window_id,
                pid=pid,
                title=_window_title(xdotool, window_id),
                width=width,
                height=height,
                x=x,
                y=y,
                class_name=_window_class(window_id),
            ))
    return windows


def _wmctrl_windows(pids: list[int]) -> list[_X11Window]:
    """用 wmctrl 列出窗口并按 PID 过滤（工具不可用返回空表）。"""
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return []
    completed = _run([wmctrl, "-lp"], _SEARCH_TIMEOUT)
    if completed is None or completed.returncode != 0:
        return []
    xdotool = shutil.which("xdotool")
    windows: list[_X11Window] = []
    for line in completed.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 3:
            continue
        window_id, _desktop, pid_text = parts[0], parts[1], parts[2]
        try:
            window_pid = int(pid_text)
        except ValueError:
            continue
        if window_pid not in pids:
            continue
        title = parts[4] if len(parts) > 4 else ""
        x, y, width, height = _window_geometry(xdotool, window_id)
        if width <= 0 or height <= 0:
            continue
        windows.append(_X11Window(
            window_id=window_id,
            pid=window_pid,
            title=title,
            width=width,
            height=height,
            x=x,
            y=y,
            class_name=_window_class(window_id),
        ))
    return windows


def _window_geometry(xdotool: str | None, window_id: str) -> tuple[int, int, int, int]:
    """读取窗口几何 ``(x, y, width, height)``（屏幕坐标）。

    xdotool 优先，回退 xwininfo；都不可用返回 ``(0, 0, 0, 0)``。
    """
    if xdotool:
        completed = _run([xdotool, "getwindowgeometry", "--shell", window_id], _SEARCH_TIMEOUT)
        if completed is not None and completed.returncode == 0:
            x = y = width = height = 0
            for token in completed.stdout.splitlines():
                if token.startswith("X="):
                    x = _safe_int(token[2:])
                elif token.startswith("Y="):
                    y = _safe_int(token[2:])
                elif token.startswith("WIDTH="):
                    width = _safe_int(token[6:])
                elif token.startswith("HEIGHT="):
                    height = _safe_int(token[7:])
            if width > 0 and height > 0:
                return x, y, width, height
    xwininfo = shutil.which("xwininfo")
    if xwininfo:
        completed = _run([xwininfo, "-id", window_id], _SEARCH_TIMEOUT)
        if completed is not None and completed.returncode == 0:
            x = y = width = height = 0
            for token in completed.stdout.splitlines():
                stripped = token.strip()
                if stripped.startswith("Absolute upper-left X:"):
                    x = _safe_int(stripped.split(":", 1)[1])
                elif stripped.startswith("Absolute upper-left Y:"):
                    y = _safe_int(stripped.split(":", 1)[1])
                elif stripped.startswith("Width:"):
                    width = _safe_int(stripped.split(":", 1)[1])
                elif stripped.startswith("Height:"):
                    height = _safe_int(stripped.split(":", 1)[1])
            return x, y, width, height
    return 0, 0, 0, 0


def find_process_windows(pid: int) -> list[_X11Window]:
    """返回 ``pid`` 及其后代进程的可见窗口（窗口输入 / 截图共用）。

    纯查询、无副作用；xdotool 与 wmctrl 都不可用时返回空表。
    """
    pids = proctree.collect_process_tree(pid)
    if not pids:
        return []
    windows = _xdotool_windows(pids)
    if windows:
        return windows
    return _wmctrl_windows(pids)


def list_windows(pid: int) -> list[WindowInfo]:
    """返回 ``pid`` 及其后代进程的可见窗口（通用描述，按 Z 序中的枚举顺序）。

    纯查询、无副作用；xdotool 与 wmctrl 都不可用时返回空表。
    """
    windows = find_process_windows(pid)
    if not windows:
        return []
    return mark_main([to_window_info(item, index)
                      for index, item in enumerate(windows)])


def control_window(pid: int, request: WindowControlRequest, *,
                   runner=None) -> dict:
    """对 ``pid`` 进程树中被选窗口执行状态 / 几何控制（xdotool / wmctrl）。

    Raises:
        NoWindowError: 进程树内没有可操作窗口。
        SelectorError: 窗口选择器非法或没有匹配窗口。
        ScreenshotError: 窗口控制命令不可用（缺少 xdotool / wmctrl）。
    """
    run = runner or _run
    windows = find_process_windows(pid)
    if not windows:
        raise NoWindowError(
            f"进程 {pid} 及其子进程没有可操作窗口"
            f"（纯命令行进程没有图形窗口；Wayland 原生窗口无法按 PID 定位）"
        )
    infos = mark_main([to_window_info(item, index)
                       for index, item in enumerate(windows)])
    target_info = pick_window(infos, request.selector)
    target = _find_by_info(windows, target_info)
    window_id = target.window_id
    action = request.action
    before = _window_state(window_id, target)
    _apply_control(run, window_id, action, request)
    time.sleep(_CONTROL_SETTLE_SECONDS)
    after = _window_state(window_id, target)
    return {
        "window_action": action,
        "handle": target_info.handle,
        "handle_hex": target_info.handle_hex,
        "window_title": target_info.title,
        "before": before,
        "after": after,
    }


def _apply_control(run, window_id: str, action: str,
                   request: WindowControlRequest) -> None:
    """按动作调用对应的 X11 工具命令（缺工具时给出可执行提示）。"""
    hex_id = _hex_window_id(window_id)
    if action == "activate":
        if not _try(run, ["wmctrl", "-i", "-a", hex_id]):
            _require(run, ["xdotool", "windowactivate", "--sync", window_id], "激活窗口")
    elif action == "minimize":
        _require(run, ["xdotool", "windowminimize", window_id], "最小化窗口")
    elif action == "maximize":
        if not _try(run, ["wmctrl", "-i", "-r", hex_id, "-b",
                          "add,maximized_vert,maximized_horz"]):
            _require(run, ["xdotool", "windowsize", window_id, "100%", "100%"],
                     "最大化窗口")
    elif action == "restore":
        _try(run, ["wmctrl", "-i", "-r", hex_id, "-b",
                   "remove,maximized_vert,maximized_horz"])
        _require(run, ["xdotool", "windowactivate", "--sync", window_id], "还原窗口")
    elif action == "close":
        if not _try(run, ["wmctrl", "-i", "-c", hex_id]):
            _require(run, ["xdotool", "windowclose", window_id], "关闭窗口")
    elif action == "move":
        _require(run, ["xdotool", "windowmove", window_id,
                       str(request.x), str(request.y)], "移动窗口")
    elif action == "resize":
        _require(run, ["xdotool", "windowsize", window_id,
                       str(request.width), str(request.height)], "缩放窗口")
    elif action == "fit":  # pragma: no branch - 动作集合由上层校验
        _require(run, ["xdotool", "windowmove", window_id,
                       str(request.x), str(request.y)], "移动窗口")
        _require(run, ["xdotool", "windowsize", window_id,
                       str(request.width), str(request.height)], "缩放窗口")


def _try(run, command: list[str]) -> bool:
    """执行可选命令（工具缺失或失败返回 False，不抛异常）。"""
    if shutil.which(command[0]) is None:
        return False
    completed = run(command)
    return completed is not None and completed.returncode == 0


def _require(run, command: list[str], label: str) -> None:
    """执行必需命令，工具缺失或失败抛 ScreenshotError。"""
    if shutil.which(command[0]) is None:
        raise ScreenshotError(
            f"{label}失败：缺少工具 {command[0]}（安装 xdotool / wmctrl 后重试）"
        )
    completed = run(command)
    if completed is None or completed.returncode != 0:
        detail = ""
        if completed is not None:
            text = (completed.stderr or completed.stdout or "").strip().splitlines()
            detail = f": {text[-1]}" if text else f": 退出码 {completed.returncode}"
        raise ScreenshotError(f"{label}失败{detail}")


def _hex_window_id(window_id: str) -> str:
    """xdotool / wmctrl 的窗口 ID → ``0x`` 十六进制（wmctrl 要求该格式）。"""
    text = str(window_id).strip()
    if text.lower().startswith("0x"):
        return text
    try:
        return hex(int(text, 10))
    except ValueError:
        return text


def _window_state(window_id: str, fallback: _X11Window) -> dict:
    """读取窗口当前几何（失败时回退枚举到的几何）。"""
    xdotool = shutil.which("xdotool")
    x, y, width, height = _window_geometry(xdotool, window_id)
    if width <= 0 or height <= 0:
        x, y, width, height = fallback.x, fallback.y, fallback.width, fallback.height
    return {"x": x, "y": y, "width": width, "height": height}


def _find_by_info(windows: list[_X11Window], info: WindowInfo) -> _X11Window:
    """按通用描述（句柄）回查平台窗口对象。"""
    return find_platform_window(windows, info)


def find_platform_window(windows: list, info: WindowInfo):
    """按通用描述（句柄）回查平台窗口对象（句柄不匹配时取第一个）。"""
    for window in windows:
        if window_id_int(window.window_id) == info.handle:
            return window
    return windows[0]  # pragma: no cover - 句柄来自同一份枚举结果


def _window_class(window_id: str) -> str:
    """读取窗口类名（``xprop WM_CLASS`` 优先，xdotool 回退，都不可用返回空串）。"""
    xprop = shutil.which("xprop")
    if xprop:
        completed = _run([xprop, "-id", window_id, "WM_CLASS"], _SEARCH_TIMEOUT)
        if completed is not None and completed.returncode == 0:
            parsed = _parse_wm_class(completed.stdout)
            if parsed:
                return parsed
    xdotool = shutil.which("xdotool")
    if xdotool:
        completed = _run([xdotool, "getwindowclassname", window_id], _SEARCH_TIMEOUT)
        if completed is not None and completed.returncode == 0:
            return completed.stdout.strip()
    return ""


def _parse_wm_class(text: str) -> str:
    """从 ``xprop`` 输出解析 ``WM_CLASS``（取第一个引号内的实例名）。"""
    _, _, rest = str(text).partition("=")
    quoted = re.findall(r'"([^"]*)"', rest)
    return quoted[0] if quoted else ""


def _window_title(xdotool: str, window_id: str) -> str:
    completed = _run([xdotool, "getwindowname", window_id], _SEARCH_TIMEOUT)
    if completed is None or completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _xwd_grab(xwd_bin: str, convert_bin: str, window_id: str, path: str) -> None:
    """``xwd`` 导出窗口位图后由 ``convert`` 转 PNG（临时文件即用即删）。"""
    handle, temp_path = tempfile.mkstemp(suffix=".xwd", prefix="shot-")
    os.close(handle)
    try:
        _run_checked([xwd_bin, "-id", window_id, "-silent", "-out", temp_path], "xwd")
        _run_checked([convert_bin, temp_path, path], "convert")
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _run(command: list[str], timeout: float = _COMMAND_TIMEOUT):
    """执行外部命令（不抛异常，失败返回 None）。"""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("命令执行失败 %s: %s", command[0], exc)
        return None


def _run_checked(command: list[str], label: str) -> None:
    """执行外部命令，失败时抛出带 stderr 摘要的 ScreenshotError。"""
    completed = _run(command)
    if completed is None:
        raise ScreenshotError(f"{label} 执行失败（命令不可用或超时）: {' '.join(command)}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        summary = detail[-1] if detail else f"退出码 {completed.returncode}"
        raise ScreenshotError(f"{label} 截图失败: {summary}")


def _safe_int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        return 0


__all__ = ["X11Backend", "control_window", "find_platform_window",
           "find_process_windows", "list_windows", "to_window_info",
           "window_id_int"]
