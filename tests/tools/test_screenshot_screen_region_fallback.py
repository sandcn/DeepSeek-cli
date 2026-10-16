"""截图「屏幕区域回退」测试（DirectX 独占全屏游戏的兜底路径）。

覆盖：窗口矩形与虚拟桌面的交集计算（裁剪 / 越界 / 不可见 / 最小化），
以及 ``capture_window_pixels`` 在 PrintWindow 与窗口 DC BitBlt 都拿不到内容时
改从桌面屏幕 DC 拷贝窗口区域、并正确回退 / 报错。
"""

from __future__ import annotations

import ctypes

import pytest

from src.tools._screenshot import png as png_mod
from src.tools._screenshot import win as win_mod
from src.tools._screenshot.result import ScreenshotError
from src.tools._screenshot.windows import WindowInfo


def _candidate(**overrides):
    base = dict(handle=0x101, pid=99, title="Game", class_name="GameClass",
                width=800, height=600, left=100, top=50)
    base.update(overrides)
    return WindowInfo(**base)


# ── 交集计算 ─────────────────────────────────────────────

def test_region_intersection_inside_virtual_screen(monkeypatch):
    monkeypatch.setattr(win_mod.winapi, "virtual_screen_rect",
                        lambda: (0, 0, 1920, 1080))
    assert win_mod.screen_region_intersection(_candidate(), 800, 600) == (0, 0, 800, 600)


def test_region_intersection_clips_to_virtual_screen(monkeypatch):
    monkeypatch.setattr(win_mod.winapi, "virtual_screen_rect",
                        lambda: (0, 0, 1920, 1080))
    # 窗口右上角超出屏幕右边界 100 像素
    region = win_mod.screen_region_intersection(
        _candidate(left=1200, top=900), 800, 600)
    assert region == (0, 0, 720, 180)


def test_region_intersection_off_screen_returns_none(monkeypatch):
    monkeypatch.setattr(win_mod.winapi, "virtual_screen_rect",
                        lambda: (0, 0, 1920, 1080))
    assert win_mod.screen_region_intersection(
        _candidate(left=3000, top=3000), 800, 600) is None


def test_region_intersection_skips_minimized_and_hidden(monkeypatch):
    monkeypatch.setattr(win_mod.winapi, "virtual_screen_rect",
                        lambda: (0, 0, 1920, 1080))
    assert win_mod.screen_region_intersection(
        _candidate(minimized=True), 800, 600) is None
    assert win_mod.screen_region_intersection(
        _candidate(visible=False), 800, 600) is None


# ── capture_window_pixels 三条路径 ───────────────────────

class _FakeGdi:
    """极简 GDI 替身：BitBlt 后返回预置像素。"""

    def __init__(self, fill: bytes):
        self.fill = fill
        self.bitblt = None
        self._buffer = None

    def CreateCompatibleDC(self, source):
        return 100

    def CreateDIBSection(self, source, info, fmt, bits_ref, section, offset):
        header = info._obj.bmiHeader
        size = header.biWidth * abs(header.biHeight) * 4
        byte = self.fill[:1] or b"\x11"
        self._buffer = ctypes.create_string_buffer(byte * size, size)
        address = ctypes.addressof(self._buffer)
        ctypes.cast(bits_ref, ctypes.POINTER(ctypes.c_void_p))[0] = address
        return 200

    def SelectObject(self, dc, bitmap):
        return 300

    def BitBlt(self, dst, x, y, w, h, src, sx, sy, rop):
        self.bitblt = (x, y, w, h, sx, sy)
        return 1

    def DeleteObject(self, obj):
        return 1

    def DeleteDC(self, dc):
        return 1


def _blank(width, height):
    return b"\x00" * (width * height * 4)


def test_capture_falls_back_to_screen_region(monkeypatch):
    """PrintWindow 与窗口 BitBlt 都全黑时，从屏幕 DC 拷贝窗口区域。"""
    candidate = _candidate()
    width, height = candidate.width, candidate.height
    monkeypatch.setattr(win_mod, "_capture_window",
                        lambda *args, **kwargs: _blank(width, height))
    monkeypatch.setattr(win_mod.winapi, "raise_window", lambda handle: True)
    monkeypatch.setattr(win_mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(win_mod.winapi, "ensure_process_dpi_aware", lambda: None)
    monkeypatch.setattr(win_mod.winapi, "virtual_screen_rect",
                        lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(win_mod.winapi, "screen_dc", lambda: 1)
    monkeypatch.setattr(win_mod.winapi, "release_dc", lambda dc: None)
    gdi = _FakeGdi(b"\x22")
    monkeypatch.setattr(win_mod.winapi, "gdi32", lambda: gdi)

    data, got_w, got_h = win_mod.capture_window_pixels(candidate)
    assert (got_w, got_h) == (width, height)
    assert data[:4] == b"\x22\x22\x22\x22"
    # 从窗口在屏幕上的位置拷贝整窗区域
    assert gdi.bitblt == (0, 0, width, height, candidate.left, candidate.top)


def test_capture_raises_when_all_paths_unavailable(monkeypatch):
    candidate = _candidate()
    monkeypatch.setattr(win_mod, "_capture_window",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(win_mod, "_capture_window_from_screen",
                        lambda cand, w, h: None)
    monkeypatch.setattr(win_mod.winapi, "raise_window", lambda handle: False)
    monkeypatch.setattr(win_mod.time, "sleep", lambda seconds: None)
    with pytest.raises(ScreenshotError):
        win_mod.capture_window_pixels(candidate)


def test_capture_returns_blank_data_when_screen_unavailable(monkeypatch):
    """屏幕也拿不到（如返回 None）时，返回有数据的那次结果而不是报错。"""
    candidate = _candidate()
    width, height = candidate.width, candidate.height
    monkeypatch.setattr(win_mod, "_capture_window",
                        lambda *args, **kwargs: _blank(width, height))
    monkeypatch.setattr(win_mod, "_capture_window_from_screen",
                        lambda cand, w, h: None)
    monkeypatch.setattr(win_mod.winapi, "raise_window", lambda handle: False)
    monkeypatch.setattr(win_mod.time, "sleep", lambda seconds: None)
    data, got_w, got_h = win_mod.capture_window_pixels(candidate)
    assert png_mod.looks_blank(data, 4)
    assert (got_w, got_h) == (width, height)
