"""窗口截图能力模块（src/tools/_screenshot）单元测试。

覆盖：
  - PNG 编码（零依赖编码器：签名/IHDR/IDAT/CRC、BGRA→RGB、输入校验）
  - PNG 尺寸读取与空白图判断
  - 进程树收集（含真实子进程）
  - 后端注册表（内置后端、注册/撤销、平台解析）
  - Windows 后端纯逻辑（窗口选择、PID 解析）
  - 真实窗口截图（当前桌面有窗口时；无窗口环境自动跳过）
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import time
import zlib

import pytest

from src.tools._screenshot import (
    available_backends,
    capture_process_window,
    register_backend,
    resolve_backend,
)
from src.tools._screenshot import png as png_mod
from src.tools._screenshot import proctree, winapi
from src.tools._screenshot.result import CaptureResult, NoWindowError, ScreenshotError
from src.tools._screenshot.transform import CropError, CropRegion


# ── 工具函数 ─────────────────────────────────────────────

def _extract_chunk(data: bytes, tag: bytes) -> bytes:
    """从 PNG 字节流中取出指定 tag 的 chunk 数据（测试用最简实现）。"""
    offset = len(png_mod.PNG_SIGNATURE)
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_tag = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if chunk_tag == tag:
            return payload
        offset += 12 + length
    raise AssertionError(f"未找到 chunk: {tag!r}")


def _decode_raw(data: bytes, width: int, height: int) -> bytes:
    """解压 IDAT 并校验每行 filter=0，返回去除 filter 字节的像素数据。"""
    raw = zlib.decompress(_extract_chunk(data, b"IDAT"))
    stride = width * 3
    assert len(raw) == (stride + 1) * height
    for row in range(height):
        assert raw[row * (stride + 1)] == 0
    return b"".join(
        raw[row * (stride + 1) + 1:(row + 1) * (stride + 1)]
        for row in range(height)
    )


# ── PNG 编码 ─────────────────────────────────────────────

def test_encode_png_rgb_structure_and_pixels():
    """IHDR 尺寸/颜色类型正确，IDAT 解压后像素一致，末尾有 IEND。"""
    pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    data = png_mod.encode_png_rgb(2, 2, pixels)
    assert data[:8] == png_mod.PNG_SIGNATURE
    width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
    assert (width, height, depth, color_type) == (2, 2, 8, png_mod.COLOR_TYPE_RGB)
    assert _decode_raw(data, 2, 2) == pixels
    assert data.endswith(b"IEND\xae\x42\x60\x82")


def test_encode_png_chunk_crc_is_valid():
    """每个 chunk 的 CRC32 与内容匹配（Pillow 之外的独立校验）。"""
    data = png_mod.encode_png_rgb(1, 1, b"\x01\x02\x03")
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF
        offset += 12 + length


def test_encode_png_bgra_drops_alpha():
    """BGRA 输入按 B/G/R 顺序转 RGB，alpha 通道丢弃。"""
    bgra = bytes([1, 2, 3, 255, 4, 5, 6, 0])
    data = png_mod.encode_png_bgra(2, 1, bgra)
    assert _decode_raw(data, 2, 1) == bytes([3, 2, 1, 6, 5, 4])


def test_encode_png_rejects_invalid_input():
    """非法尺寸 / 长度不匹配一律 ValueError（防御异常窗口数据）。"""
    with pytest.raises(ValueError):
        png_mod.encode_png_rgb(0, 1, b"")
    with pytest.raises(ValueError):
        png_mod.encode_png_rgb(-1, 1, b"")
    with pytest.raises(ValueError):
        png_mod.encode_png_rgb(2, 1, b"\x00" * 3)
    with pytest.raises(ValueError):
        png_mod.encode_png_rgb(png_mod.MAX_DIMENSION + 1, 1, b"\x00" * 3)
    with pytest.raises(ValueError):
        png_mod.encode_png_bgra(1, 1, b"\x00" * 3)


def test_encode_png_pillow_can_decode(tmp_path):
    """Pillow 可解码（若环境安装了 Pillow）——端到端格式正确性。"""
    pillow = pytest.importorskip("PIL.Image")
    pixels = bytes([10, 20, 30, 200, 100, 50, 0, 0, 0, 255, 255, 255])
    target = tmp_path / "roundtrip.png"
    target.write_bytes(png_mod.encode_png_rgb(2, 2, pixels))
    with pillow.open(target) as image:
        assert image.size == (2, 2)
        assert image.mode == "RGB"
        assert list(image.getdata()) == [(10, 20, 30), (200, 100, 50), (0, 0, 0), (255, 255, 255)]


def test_read_png_size_roundtrip_and_invalid(tmp_path):
    """read_png_size 读回编码尺寸；非 PNG 文件抛 ValueError。"""
    target = tmp_path / "size.png"
    target.write_bytes(png_mod.encode_png_rgb(7, 3, b"\x00" * (7 * 3 * 3)))
    assert png_mod.read_png_size(str(target)) == (7, 3)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png at all")
    with pytest.raises(ValueError):
        png_mod.read_png_size(str(bad))


def test_is_blank_rgb_detects_uniform_images():
    """全同色（纯黑/纯白）判为空白；有任何差异即非空白。"""
    assert png_mod.is_blank_rgb(b"\x00" * 3000) is True
    assert png_mod.is_blank_rgb(b"\xff" * 3000) is True
    assert png_mod.is_blank_rgb(b"\x00" * 1497 + b"\x01\x02\x03") is False
    assert png_mod.is_blank_rgb(b"") is True


def test_looks_blank_supports_bgra_and_rejects_bad_stride():
    """BGRA（4 字节/像素）数据同样可判断；非法 stride 抛 ValueError。"""
    assert png_mod.looks_blank(b"\x11\x22\x33\xff" * 100, 4) is True
    changed = bytearray(b"\x11\x22\x33\xff" * 100)
    changed[5] = 0x99
    assert png_mod.looks_blank(bytes(changed), 4) is False
    assert png_mod.looks_blank(b"", 4) is True
    with pytest.raises(ValueError):
        png_mod.looks_blank(b"\x00" * 12, 0)


def test_read_png_size_or_falls_back(tmp_path):
    """尺寸读取失败时回退给定几何尺寸（各后端统一路径）。"""
    good = tmp_path / "good.png"
    good.write_bytes(png_mod.encode_png_rgb(4, 2, b"\x00" * (4 * 2 * 3)))
    assert png_mod.read_png_size_or(str(good), (9, 9)) == (4, 2)
    assert png_mod.read_png_size_or(str(tmp_path / "missing.png"), (9, 9)) == (9, 9)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"nope")
    assert png_mod.read_png_size_or(str(broken), (3, 5)) == (3, 5)


# ── 进程树 ───────────────────────────────────────────────

def test_collect_process_tree_includes_self():
    tree = proctree.collect_process_tree(os.getpid())
    assert os.getpid() in tree


def test_collect_process_tree_rejects_invalid_pid():
    assert proctree.collect_process_tree(0) == []
    assert proctree.collect_process_tree(-1) == []
    assert proctree.collect_process_tree(None) == []  # type: ignore[arg-type]
    assert proctree.collect_process_tree(True) == []  # type: ignore[arg-type]


def test_collect_process_tree_finds_real_child():
    """真实子进程出现在进程树中（/proc 与 Toolhelp32 两条路径都覆盖）。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if child.pid in proctree.collect_process_tree(os.getpid()):
                break
            time.sleep(0.1)
        assert child.pid in proctree.collect_process_tree(os.getpid())
    finally:
        child.kill()
        child.wait()


def test_read_winpid_only_on_cygwin_like():
    """read_winpid：Cygwin 环境返回整数，其它平台返回 None 或不报错。"""
    value = proctree.read_winpid(os.getpid())
    if winapi.is_cygwin_like():
        assert isinstance(value, int) and value > 0
    else:
        assert value is None or isinstance(value, int)


# ── 后端注册表 ───────────────────────────────────────────

def test_builtin_backends_registered_in_probe_order():
    names = [backend.name for backend in available_backends()]
    assert names[:3] == ["windows", "macos", "x11"]


def test_resolve_backend_matches_platform():
    backend = resolve_backend()
    assert backend is not None
    if winapi.is_windows_platform():
        assert backend.name == "windows"
    elif sys.platform == "darwin":
        assert backend.name == "macos"
    else:
        assert backend.name == "x11"


def test_register_backend_prepend_and_undo():
    """注册的后端可优先命中，撤销幂等且不影响内置后端。"""

    class _DummyBackend:
        name = "dummy"

        def supports(self) -> bool:
            return True

        def capture(self, pid, path):  # pragma: no cover - 不实际调用
            raise AssertionError("不应调用占位后端")

    undo = register_backend(_DummyBackend(), prepend=True)
    try:
        assert resolve_backend().name == "dummy"
        assert [b.name for b in available_backends()][0] == "dummy"
    finally:
        undo()
        undo()
    assert all(backend.name != "dummy" for backend in available_backends())
    assert resolve_backend().name != "dummy"


def test_capture_process_window_rejects_invalid_pid(tmp_path):
    for bad in (0, -3, None, "abc"):
        with pytest.raises(ScreenshotError):
            capture_process_window(bad, str(tmp_path / "x.png"))  # type: ignore[arg-type]


# ── Windows 后端纯逻辑 ───────────────────────────────────

def test_windows_select_window_prefers_titled_then_area():
    from src.tools._screenshot.win import WindowCandidate, select_window

    small_titled = WindowCandidate(handle=1, pid=10, title="game", class_name="C", width=100, height=100)
    big_untitled = WindowCandidate(handle=2, pid=11, title="", class_name="C", width=900, height=900)
    tool_titled = WindowCandidate(
        handle=3, pid=12, title="tool", class_name="C", width=500, height=500, tool_window=True
    )
    assert select_window([big_untitled, small_titled, tool_titled]) is small_titled
    assert select_window([tool_titled, big_untitled]) is big_untitled
    assert select_window([]) is None


def test_windows_select_window_larger_area_wins_among_titled():
    from src.tools._screenshot.win import WindowCandidate, select_window

    first = WindowCandidate(handle=1, pid=10, title="a", class_name="C", width=100, height=100)
    second = WindowCandidate(handle=2, pid=11, title="b", class_name="C", width=400, height=400)
    assert select_window([first, second]) is second


def test_windows_select_window_prefers_restored_over_minimized():
    """最小化窗口排后（避免截到无法渲染的窗口）。"""
    from src.tools._screenshot.win import WindowCandidate, select_window

    minimized = WindowCandidate(
        handle=1, pid=10, title="game", class_name="C",
        width=1600, height=900, minimized=True,
    )
    restored = WindowCandidate(
        handle=2, pid=11, title="game", class_name="C",
        width=800, height=600,
    )
    assert select_window([minimized, restored]) is restored
    assert select_window([minimized]) is minimized


@pytest.mark.skipif(not winapi.is_windows_platform(), reason="仅 Windows/Cygwin")
def test_resolve_window_pids_maps_cygwin_pid_to_winpid():
    """Cygwin 下 POSIX PID 被映射为 WINPID（截图窗口归属进程的关键前提）。"""
    from src.tools._screenshot.win import resolve_window_pids

    expected = proctree.read_winpid(os.getpid())
    resolved = resolve_window_pids(os.getpid())
    assert resolved
    if expected is not None:
        assert expected in resolved


@pytest.mark.skipif(not winapi.is_windows_platform(), reason="仅 Windows/Cygwin")
def test_resolve_window_pids_includes_child_process():
    """子进程（含其窗口）被纳入候选集合——后台 shell 启动 GUI 子进程的关键。"""
    from src.tools._screenshot.win import resolve_window_pids

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    try:
        expected = proctree.read_winpid(child.pid) or child.pid
        deadline = time.monotonic() + 5
        resolved: set = set()
        while time.monotonic() < deadline:
            resolved = resolve_window_pids(os.getpid())
            if expected in resolved:
                break
            time.sleep(0.1)
        assert expected in resolved
    finally:
        child.kill()
        child.wait()


# ── 真实窗口截图（桌面环境） ─────────────────────────────

def test_capture_real_window_end_to_end(tmp_path, real_window_pids):
    """真实截图：抓取当前桌面一个可见窗口并校验产物是有效 PNG。"""
    if not real_window_pids:
        pytest.skip("当前环境无可见窗口（无桌面会话或非 Windows）")
    target = tmp_path / "real_shot.png"
    result = None
    for pid in real_window_pids:
        try:
            result = capture_process_window(pid, str(target))
            break
        except NoWindowError:
            continue
    if result is None:
        pytest.skip("候选窗口均无法定位（进程树不匹配）")
    assert isinstance(result, CaptureResult)
    assert target.exists() and target.stat().st_size > 0
    width, height = png_mod.read_png_size(str(target))
    assert (width, height) == (result.width, result.height)
    assert width > 0 and height > 0
    assert result.backend == "windows"


def test_capture_reports_no_window_for_headless_process(tmp_path):
    """无窗口进程（本测试进程自身）截图时给出可读错误而非崩溃。"""
    if not winapi.is_windows_platform():
        pytest.skip("仅 Windows/Cygwin")
    target = tmp_path / "no_window.png"
    try:
        capture_process_window(os.getpid(), str(target))
    except NoWindowError as exc:
        assert "窗口" in str(exc)
    except ScreenshotError as exc:  # 进程树恰好命中容器窗口时也允许
        assert str(exc)
    else:  # pragma: no cover - 环境差异（如测试宿主自身带窗口）
        assert target.exists()


# ── 裁剪：后端契约与入口透传 ─────────────────────────────

def test_builtin_backends_accept_crop_keyword():
    """内置后端的 capture 均接受 crop 关键字（契约一致性）。"""
    import inspect

    for backend in available_backends():
        assert "crop" in inspect.signature(backend.capture).parameters, backend.name


def test_capture_process_window_forwards_crop_to_backend(tmp_path):
    """capture_process_window 把 crop 原样交给后端；省略时传 None。"""
    seen = []

    class _CropProbeBackend:
        name = "crop-probe"

        def supports(self) -> bool:
            return True

        def capture(self, pid, path, crop=None):
            seen.append(crop)
            with open(path, "wb") as handle:
                handle.write(png_mod.encode_png_rgb(2, 2, b"\x00" * 12))
            return CaptureResult(path=path, width=2, height=2, window_pid=pid,
                                 window_title="", backend=self.name)

    undo = register_backend(_CropProbeBackend(), prepend=True)
    try:
        region = CropRegion(0, 0, 1, 1)
        capture_process_window(1234, str(tmp_path / "a.png"), region)
        capture_process_window(1234, str(tmp_path / "b.png"))
    finally:
        undo()
    assert seen == [region, None]


def test_capture_process_window_lets_crop_error_through(tmp_path):
    """后端抛出的 CropError 原样透出（不被包装为通用截图失败）。"""
    class _CropErrorBackend:
        name = "crop-error"

        def supports(self) -> bool:
            return True

        def capture(self, pid, path, crop=None):
            raise CropError("裁剪区域超出截图范围: 当前窗口截图为 2x2")

    undo = register_backend(_CropErrorBackend(), prepend=True)
    try:
        with pytest.raises(CropError) as excinfo:
            capture_process_window(1234, str(tmp_path / "c.png"),
                                   CropRegion(0, 0, 5, 5))
    finally:
        undo()
    assert "超出截图范围" in str(excinfo.value)
