"""DPI 虚拟化窗口的截图坐标一致性单元测试。

缺陷背景：目标进程**自身非 DPI 感知**时（150% 缩放显示器上），系统会对其
窗口做位图拉伸——窗口仍按逻辑像素渲染（``PrintWindow`` / 窗口 DC 输出的就是
这部分内容），而 ``GetWindowRect`` 在 DPI 感知的调用方下返回物理像素矩形。
于是截图只在位图左上角有内容（尺寸 ≈ 物理尺寸 / 缩放比），模型按截图像素
给出的输入坐标会整体偏一个缩放比（实测 150% 下偏 1.5 倍：点击 (256,111)
落空、点击 (384,166) 命中）；同一份坐标系里 ``op=elements`` 的 UIA 坐标却是
物理像素，两套尺度并存。

修复：截图后端用 ``winapi.window_virtualization_scale`` 探测虚拟化比例，把
窗口 DC 路径拿到的逻辑内容放大回物理像素（GDI ``StretchBlt`` 优先，纯 Python
最近邻兜底），使截图像素、``window_x/window_y`` 原点与输入注入坐标同源。

覆盖：
  - ``transform.scale_bgra``：最近邻缩放的边界映射与参数校验
  - ``winapi.window_virtualization_scale``：比例计算、上下文恢复、不可用回退
  - ``win._devirtualize``：GDI 优先、纯 Python 兜底、未虚拟化时零开销
  - ``win.capture_window_pixels``：窗口 DC 路径换算、屏幕拷贝路径不换算
"""

from __future__ import annotations

import ctypes

import pytest

from src.tools._screenshot import win as win_mod
from src.tools._screenshot import winapi
from src.tools._screenshot.transform import CropError, CropRegion, scale_bgra
from src.tools._screenshot.windows import WindowInfo


# ── 测试数据构造 ─────────────────────────────────────────

def _bgra(width: int, height: int, color: bytes = b"\x00\x00\x00\x00") -> bytes:
    """构造 ``width x height`` 的 BGRA 纯色数据。"""
    return bytes(color) * (width * height)


def _with_block(width: int, height: int, block_width: int, block_height: int,
                color: bytes) -> bytes:
    """构造「左上 ``block_width x block_height`` 为 color、其余为黑」的 BGRA 数据。"""
    data = bytearray(_bgra(width, height))
    for y in range(min(block_height, height)):
        for x in range(min(block_width, width)):
            offset = (y * width + x) * 4
            data[offset:offset + 4] = color
    return bytes(data)


def _pixel(data: bytes, width: int, x: int, y: int) -> bytes:
    offset = (y * width + x) * 4
    return data[offset:offset + 4]


def _candidate(width: int = 8, height: int = 8) -> WindowInfo:
    return WindowInfo(handle=0x1234, pid=4321, title="probe",
                      class_name="ProbeWindow", width=width, height=height)


# ── transform.scale_bgra ─────────────────────────────────

def test_scale_bgra_upscales_with_nearest_neighbor():
    """2x2 → 4x4：每个源像素扩展为 2x2 的目标块（最近邻）。"""
    source = (
        b"\x01\x01\x01\xff" b"\x02\x02\x02\xff"
        b"\x03\x03\x03\xff" b"\x04\x04\x04\xff"
    )
    out = scale_bgra(source, 2, 2, CropRegion(0, 0, 2, 2), 4, 4)
    assert len(out) == 4 * 4 * 4
    assert _pixel(out, 4, 0, 0) == b"\x01\x01\x01\xff"
    assert _pixel(out, 4, 1, 1) == b"\x01\x01\x01\xff"
    assert _pixel(out, 4, 3, 0) == b"\x02\x02\x02\xff"
    assert _pixel(out, 4, 0, 3) == b"\x03\x03\x03\xff"
    assert _pixel(out, 4, 3, 3) == b"\x04\x04\x04\xff"


def test_scale_bgra_downscales_sampling():
    """4x4 → 2x2：取每个目标格左上角的源像素。"""
    data = bytearray(_bgra(4, 4))
    for index in range(16):
        data[index * 4:index * 4 + 4] = bytes([index, index, index, 255])
    out = scale_bgra(bytes(data), 4, 4, CropRegion(0, 0, 4, 4), 2, 2)
    assert _pixel(out, 2, 0, 0) == b"\x00\x00\x00\xff"
    assert _pixel(out, 2, 1, 0) == b"\x02\x02\x02\xff"
    assert _pixel(out, 2, 0, 1) == b"\x08\x08\x08\xff"
    assert _pixel(out, 2, 1, 1) == b"\x0a\x0a\x0a\xff"


def test_scale_bgra_same_size_equals_crop():
    """目标尺寸等于源区域尺寸时退化为裁剪（保持像素不变）。"""
    data = _bgra(4, 4, b"\x09\x08\x07\xff")
    out = scale_bgra(data, 4, 4, CropRegion(1, 1, 2, 2), 2, 2)
    assert out == _bgra(2, 2, b"\x09\x08\x07\xff")


def test_scale_bgra_rejects_invalid_destination():
    """目标尺寸非正时报错（避免静默产出空图）。"""
    with pytest.raises(CropError):
        scale_bgra(_bgra(2, 2), 2, 2, CropRegion(0, 0, 2, 2), 0, 3)


def test_scale_bgra_rejects_length_mismatch():
    """像素数据长度与声明的图像尺寸不符时报错。"""
    with pytest.raises(CropError):
        scale_bgra(_bgra(2, 2), 3, 3, CropRegion(0, 0, 2, 2), 4, 4)


def test_scale_bgra_rejects_out_of_range_region():
    """源区域越界时报错（沿用裁剪的区域校验）。"""
    with pytest.raises(CropError):
        scale_bgra(_bgra(2, 2), 2, 2, CropRegion(0, 0, 3, 3), 4, 4)


# ── winapi.window_virtualization_scale ───────────────────

def test_virtualization_scale_non_windows_returns_one(monkeypatch):
    """非 Windows 平台不做探测，比例恒为 1。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: False)
    assert winapi.window_virtualization_scale(0x10) == (1.0, 1.0)


def test_virtualization_scale_without_thread_api_returns_one(monkeypatch):
    """旧系统缺少 SetThreadDpiAwarenessContext 时回退为「未虚拟化」。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "_SET_THREAD_DPI_AWARENESS_CONTEXT", None)
    monkeypatch.setattr(winapi, "user32", lambda: None)
    assert winapi.window_virtualization_scale(0x10) == (1.0, 1.0)


def test_virtualization_scale_requires_process_dpi_aware(monkeypatch):
    """进程未能感知 DPI 时不探测（此时物理坐标本身就不可靠）。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: False)
    monkeypatch.setattr(winapi, "_SET_THREAD_DPI_AWARENESS_CONTEXT", lambda ctx: 0)
    assert winapi.window_virtualization_scale(0x10) == (1.0, 1.0)


def test_virtualization_scale_detects_150_percent(monkeypatch):
    """物理 810x510 / 逻辑 540x340（150% 缩放）→ 比例 1.5，且上下文被恢复。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: True)
    contexts: list = []

    def fake_setter(context):
        contexts.append(context)
        return 7  # 模拟 SetThreadDpiAwarenessContext 返回的旧上下文句柄

    monkeypatch.setattr(winapi, "_SET_THREAD_DPI_AWARENESS_CONTEXT", fake_setter)

    def fake_rect(_hwnd):
        # 第一次读（感知上下文）→ 物理矩形；切到非感知后 → 逻辑矩形
        if not contexts:
            return (875, 429, 1685, 939)
        return (583, 286, 1123, 626)

    monkeypatch.setattr(winapi, "window_rect", fake_rect)
    assert winapi.window_virtualization_scale(0x10) == (1.5, 1.5)
    unaware_value = ctypes.c_size_t(winapi.DPI_AWARENESS_CONTEXT_UNAWARE).value
    assert len(contexts) == 2, "探测后必须恢复线程原先的 DPI 上下文"
    assert contexts[0].value == unaware_value
    assert contexts[1].value == 7


def test_virtualization_scale_aware_window_returns_one(monkeypatch):
    """目标进程本身 DPI 感知时两个上下文读到同一矩形 → 比例 1。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: True)
    monkeypatch.setattr(winapi, "_SET_THREAD_DPI_AWARENESS_CONTEXT", lambda ctx: 0)
    monkeypatch.setattr(winapi, "window_rect", lambda _hwnd: (0, 0, 810, 510))
    assert winapi.window_virtualization_scale(0x10) == (1.0, 1.0)


def test_virtualization_scale_restores_context_when_probe_fails(monkeypatch):
    """逻辑矩形读取失败时回退为 1，且线程上下文仍被恢复。"""
    monkeypatch.setattr(winapi, "is_windows_platform", lambda: True)
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: True)
    contexts: list = []

    def fake_setter(context):
        contexts.append(context)
        return 0

    monkeypatch.setattr(winapi, "_SET_THREAD_DPI_AWARENESS_CONTEXT", fake_setter)

    calls = {"count": 0}

    def flaky_rect(_hwnd):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise OSError("GetWindowRect failed")
        return (0, 0, 810, 510)

    monkeypatch.setattr(winapi, "window_rect", flaky_rect)
    assert winapi.window_virtualization_scale(0x10) == (1.0, 1.0)
    assert len(contexts) == 2


def test_virtualization_scale_from_rects_rejects_invalid_dimensions():
    """尺寸非法（空矩形）时回退为 1，避免除零或荒谬比例。"""
    assert winapi._virtualization_scale_from_rects((10, 10, 10, 10), (1, 1, 60, 40)) == (1.0, 1.0)
    assert winapi._virtualization_scale_from_rects((0, 0, 80, 40), (5, 5, 5, 5)) == (1.0, 1.0)


# ── win._devirtualize ────────────────────────────────────

def test_devirtualize_passthrough_when_not_virtualized():
    """未虚拟化（比例 ≤ 1）时零开销原样返回。"""
    data = _bgra(4, 4, b"\x10\x20\x30\xff")
    assert win_mod._devirtualize(data, 4, 4, 1.0, 1.0) is data


def test_devirtualize_stretches_top_left_content(monkeypatch):
    """GDI 不可用时走纯 Python 兜底：左上逻辑内容被拉伸铺满整幅位图。"""
    width, height = 8, 8
    source = _with_block(width, height, 4, 4, b"\xff\xff\xff\xff")
    monkeypatch.setattr(win_mod, "_stretch_bgra_gdi", lambda *args, **kwargs: None)
    out = win_mod._devirtualize(source, width, height, 2.0, 2.0)
    assert len(out) == width * height * 4
    assert _pixel(out, width, 0, 0) == b"\xff\xff\xff\xff"
    assert _pixel(out, width, width - 1, height - 1) == b"\xff\xff\xff\xff"


def test_devirtualize_prefers_gdi(monkeypatch):
    """GDI 可用时优先走 StretchBlt，源区域取左上角的逻辑尺寸。"""
    captured: dict = {}

    def fake_gdi(bgra, width, height, region, dst_width, dst_height):
        captured["region"] = (region.x, region.y, region.width, region.height)
        captured["dst"] = (dst_width, dst_height)
        return b"stretched"

    monkeypatch.setattr(win_mod, "_stretch_bgra_gdi", fake_gdi)
    out = win_mod._devirtualize(_bgra(10, 10), 10, 10, 1.5, 1.5)
    assert out == b"stretched"
    assert captured["region"] == (0, 0, 7, 7)
    assert captured["dst"] == (10, 10)


# ── win.capture_window_pixels ────────────────────────────

def test_capture_window_pixels_devirtualizes_window_dc_path(monkeypatch):
    """窗口 DC 路径（PrintWindow）的位图按虚拟化比例放大后才返回。"""
    width, height = 8, 6
    raw = _with_block(width, height, 5, 4, b"\x00\x00\xff\xff")
    monkeypatch.setattr(winapi, "window_virtualization_scale", lambda _hwnd: (1.5, 1.5))
    monkeypatch.setattr(win_mod, "_capture_window", lambda *args, **kwargs: raw)
    monkeypatch.setattr(win_mod, "_stretch_bgra_gdi", lambda *args, **kwargs: None)
    data, out_width, out_height = win_mod.capture_window_pixels(_candidate(width, height))
    assert (out_width, out_height) == (width, height)
    assert _pixel(data, width, width - 1, height - 1) == b"\x00\x00\xff\xff"


def test_capture_window_pixels_leaves_screen_copy_untouched(monkeypatch):
    """屏幕 DC 拷贝路径本就拿到物理像素，不再重复换算。"""
    width, height = 8, 6
    screen_pixels = _with_block(width, height, 4, 4, b"\x00\xff\x00\xff")
    monkeypatch.setattr(winapi, "window_virtualization_scale", lambda _hwnd: (1.5, 1.5))
    monkeypatch.setattr(win_mod, "_capture_window", lambda *args, **kwargs: None)
    monkeypatch.setattr(winapi, "raise_window", lambda _hwnd: False)
    monkeypatch.setattr(win_mod, "_capture_window_from_screen",
                        lambda *args, **kwargs: screen_pixels)
    data, out_width, out_height = win_mod.capture_window_pixels(_candidate(width, height))
    assert (out_width, out_height) == (width, height)
    assert data == screen_pixels


def test_capture_window_pixels_virtualizes_blank_fallback(monkeypatch):
    """三条路径都空白时返回的窗口 DC 数据同样做虚拟化换算。"""
    width, height = 6, 6
    blank = _bgra(width, height, b"\x00\x00\x00\x00")
    monkeypatch.setattr(winapi, "window_virtualization_scale", lambda _hwnd: (2.0, 2.0))
    monkeypatch.setattr(win_mod, "_capture_window", lambda *args, **kwargs: blank)
    monkeypatch.setattr(winapi, "raise_window", lambda _hwnd: False)
    monkeypatch.setattr(win_mod, "_capture_window_from_screen",
                        lambda *args, **kwargs: None)
    calls: list = []

    def fake_gdi(bgra, w, h, region, dst_w, dst_h):
        calls.append((region.width, region.height, dst_w, dst_h))
        return b"fallback"

    monkeypatch.setattr(win_mod, "_stretch_bgra_gdi", fake_gdi)
    data, out_width, out_height = win_mod.capture_window_pixels(_candidate(width, height))
    assert (out_width, out_height) == (width, height)
    assert data == b"fallback"
    assert calls == [(3, 3, 6, 6)]
