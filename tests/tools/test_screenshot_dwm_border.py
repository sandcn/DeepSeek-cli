"""Windows 截图 DWM 不可见边框（黑边）修复的单元测试。

缺陷背景：``GetWindowRect`` 在 Win10 上包含系统为阴影/窗口缩放预留的
**不可见边框**（非最大化窗口通常在左/上/右/下各约 8 个逻辑像素），
``PrintWindow`` / ``BitBlt`` 在该区域无内容可渲染，整窗截图四边因此出现黑边。

修复：按 ``DWMWA_EXTENDED_FRAME_BOUNDS``（:func:`win.visible_region`）在内存
BGRA 上裁掉黑边后再输出。

覆盖：
  - ``winapi.extended_frame_bounds`` 解析与失败回退
  - ``visible_region`` 偏移/尺寸计算与异常回退
  - 后端截图：先去黑边、再应用 crop，像素位置正确
  - 真实窗口：可见区域落在窗口矩形内
"""

from __future__ import annotations

import ctypes

import pytest

from src.tools._screenshot import png as png_mod
from src.tools._screenshot import png_decode
from src.tools._screenshot import winapi
from src.tools._screenshot import win as win_mod
from src.tools._screenshot.transform import CropRegion


# ── 辅助 ─────────────────────────────────────────────────

def _install_dwm(monkeypatch, *, hr=0, rect=None, exc=None):
    """替换 ``winapi.dwmapi`` 为可预期的桩（写入真实 RECT 结构）。"""

    class _Stub:
        def DwmGetWindowAttribute(self, hwnd, attribute, ptr, size):
            if exc is not None:
                raise exc
            if hr == 0 and rect is not None:
                target = ctypes.cast(ptr, ctypes.POINTER(winapi.RECT)).contents
                target.left, target.top, target.right, target.bottom = rect
            return hr

    monkeypatch.setattr(winapi, "dwmapi", lambda: _Stub())


def _bgra_pattern(width: int, height: int) -> bytes:
    """可校验位置的 BGRA 像素：B=x%256, G=y%256, R=17, A=255。"""
    buf = bytearray(width * height * 4)
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            i = row + x * 4
            buf[i] = x % 256
            buf[i + 1] = y % 256
            buf[i + 2] = 17
            buf[i + 3] = 255
    return bytes(buf)


def _candidate(width=100, height=100, left=0, top=0):
    return win_mod.WindowCandidate(
        handle=1, pid=42, title="t", class_name="C",
        width=width, height=height, left=left, top=top,
    )


def _prepare_capture(monkeypatch, candidate, *, bounds, pixels=None):
    monkeypatch.setattr(winapi, "ensure_process_dpi_aware", lambda: True)
    monkeypatch.setattr(win_mod, "resolve_window_pids", lambda pid: {pid})
    monkeypatch.setattr(win_mod, "enumerate_candidates", lambda pids: [candidate])
    data = pixels if pixels is not None else _bgra_pattern(candidate.width, candidate.height)
    monkeypatch.setattr(
        win_mod, "capture_window_pixels",
        lambda c: (data, candidate.width, candidate.height),
    )
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: bounds)


# ── extended_frame_bounds ────────────────────────────────

def test_extended_frame_bounds_parses_rect(monkeypatch):
    """DWM 返回 0 时解析出可见边界四元组。"""
    _install_dwm(monkeypatch, hr=0, rect=(9, 5, 109, 85))
    assert winapi.extended_frame_bounds(1) == (9, 5, 109, 85)


def test_extended_frame_bounds_none_on_nonzero_hr(monkeypatch):
    """DWM 返回非 0（非合成窗口等）时回退为 None。"""
    _install_dwm(monkeypatch, hr=-2147024891)
    assert winapi.extended_frame_bounds(1) is None


def test_extended_frame_bounds_none_when_dwm_unavailable(monkeypatch):
    """dwmapi 加载失败（旧系统）时返回 None，不向上抛异常。"""
    def _boom():
        raise OSError("dwmapi.dll not found")

    monkeypatch.setattr(winapi, "dwmapi", _boom)
    assert winapi.extended_frame_bounds(1) is None


def test_extended_frame_bounds_uses_extended_frame_attribute(monkeypatch):
    """查询的 DWM 属性必须是 DWMWA_EXTENDED_FRAME_BOUNDS（值 9）。"""
    seen = {}

    class _Stub:
        def DwmGetWindowAttribute(self, hwnd, attribute, ptr, size):
            seen["attribute"] = attribute
            return -1

    monkeypatch.setattr(winapi, "dwmapi", lambda: _Stub())
    winapi.extended_frame_bounds(7)
    assert seen["attribute"] == winapi.DWMWA_EXTENDED_FRAME_BOUNDS == 9


# ── visible_region ───────────────────────────────────────

def test_visible_region_computes_trim_offsets(monkeypatch):
    """由窗口矩形与 DWM 边界之差得到裁边区域。"""
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: (11, 11, 89, 89))
    assert win_mod.visible_region(_candidate()) == CropRegion(11, 11, 78, 78)


def test_visible_region_none_when_no_border(monkeypatch):
    """DWM 边界等于窗口矩形（无不可见边框）时不裁剪。"""
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: (0, 0, 100, 100))
    assert win_mod.visible_region(_candidate()) is None


def test_visible_region_none_when_bounds_unavailable(monkeypatch):
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: None)
    assert win_mod.visible_region(_candidate()) is None


def test_visible_region_none_when_bounds_exceed_window(monkeypatch):
    """边界超出窗口矩形（异常数据）时回退整窗，绝不放大裁剪。"""
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: (0, 0, 120, 120))
    assert win_mod.visible_region(_candidate()) is None


def test_visible_region_none_on_negative_offset(monkeypatch):
    """边界左上角跑到窗口矩形之外（负偏移）时回退整窗。"""
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: (95, 0, 105, 100))
    assert win_mod.visible_region(_candidate()) is None


def test_visible_region_none_on_degenerate_size(monkeypatch):
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: (10, 10, 10, 10))
    assert win_mod.visible_region(_candidate()) is None


def test_visible_region_handles_window_offset_on_screen(monkeypatch):
    """窗口位于负屏幕坐标时仍按相对偏移裁剪（窗口矩形含屏幕外边框）。"""
    monkeypatch.setattr(winapi, "extended_frame_bounds", lambda hwnd: (0, 0, 78, 89))
    region = win_mod.visible_region(_candidate(left=-11, top=-11))
    assert region == CropRegion(11, 11, 78, 89)


# ── 后端接线：先裁黑边再裁 crop ──────────────────────────

def test_capture_trims_dwm_border(monkeypatch, tmp_path):
    """整窗截图先裁掉不可见边框，输出尺寸与像素位置均正确。"""
    candidate = _candidate()
    _prepare_capture(monkeypatch, candidate, bounds=(11, 0, 89, 89))
    target = tmp_path / "shot.png"
    result = win_mod.WindowsBackend().capture(42, str(target))
    assert (result.width, result.height) == (78, 89)
    assert png_mod.read_png_size(str(target)) == (78, 89)
    image = png_decode.decode_png_file(str(target))
    # 裁掉左 11 后，首像素对应原图 (11, 0)：R=17, G=0, B=11
    assert image.rgb[0:3] == bytes((17, 0, 11))


def test_capture_without_border_keeps_full_window(monkeypatch, tmp_path):
    """DWM 边界等于窗口矩形时输出整窗像素。"""
    candidate = _candidate()
    _prepare_capture(monkeypatch, candidate, bounds=(0, 0, 100, 100))
    result = win_mod.WindowsBackend().capture(42, str(tmp_path / "full.png"))
    assert (result.width, result.height) == (100, 100)


def test_capture_applies_crop_after_dwm_trim(monkeypatch, tmp_path):
    """crop 坐标以去黑边后的截图为原点（裁黑边 → 再裁 crop）。"""
    candidate = _candidate()
    _prepare_capture(monkeypatch, candidate, bounds=(11, 0, 89, 89))
    target = tmp_path / "crop.png"
    result = win_mod.WindowsBackend().capture(42, str(target), CropRegion(5, 5, 10, 10))
    assert (result.width, result.height) == (10, 10)
    image = png_decode.decode_png_file(str(target))
    # 原图坐标 = 黑边偏移 (11, 0) + crop 偏移 (5, 5) = (16, 5)
    assert image.rgb[0:3] == bytes((17, 5, 16))


def test_capture_crop_outside_trimmed_area_reports_error(monkeypatch, tmp_path):
    """crop 按去黑边后的尺寸校验，越界抛 CropError。"""
    candidate = _candidate()
    _prepare_capture(monkeypatch, candidate, bounds=(11, 0, 89, 89))
    with pytest.raises(win_mod.CropError):
        win_mod.WindowsBackend().capture(42, str(tmp_path / "bad.png"), CropRegion(0, 0, 90, 90))


# ── 真实窗口 ─────────────────────────────────────────────

@pytest.mark.skipif(not winapi.is_windows_platform(), reason="仅 Windows/Cygwin")
def test_visible_region_real_window_stays_inside_bounds(real_window_pids):
    """真实窗口的裁边区域必须落在窗口矩形内（无越界/负偏移）。"""
    if not real_window_pids:
        pytest.skip("当前环境无可见窗口")
    checked = 0
    for pid in real_window_pids:
        target = win_mod.select_window(win_mod.enumerate_candidates({pid}))
        if target is None:
            continue
        checked += 1
        region = win_mod.visible_region(target)
        if region is None:
            continue
        assert region.x >= 0 and region.y >= 0
        assert region.x + region.width <= target.width
        assert region.y + region.height <= target.height
    if checked == 0:
        pytest.skip("未能定位窗口")


@pytest.mark.skipif(not winapi.is_windows_platform(), reason="仅 Windows/Cygwin")
def test_capture_real_window_trimmed_to_visible_bounds(monkeypatch, tmp_path, real_window_pids):
    """真实截图产物尺寸必须等于 DWM 可见区域（不可见边框已裁掉）。"""
    if not real_window_pids:
        pytest.skip("当前环境无可见窗口")
    winapi.ensure_process_dpi_aware()
    for pid in real_window_pids:
        target = win_mod.select_window(win_mod.enumerate_candidates({pid}))
        if target is None:
            continue
        region = win_mod.visible_region(target)
        if region is None:
            continue
        output = tmp_path / f"trimmed_{pid}.png"
        monkeypatch.setattr(win_mod, "resolve_window_pids", lambda p, _p=pid: {_p})
        monkeypatch.setattr(win_mod, "enumerate_candidates", lambda pids: [target])
        result = win_mod.WindowsBackend().capture(pid, str(output))
        assert (result.width, result.height) == (region.width, region.height)
        assert png_mod.read_png_size(str(output)) == (region.width, region.height)
        return
    pytest.skip("当前环境没有带 DWM 不可见边框的窗口")
