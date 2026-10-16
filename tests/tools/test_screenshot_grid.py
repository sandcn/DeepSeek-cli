"""截图坐标网格（``_screenshot.grid``）单元测试。

覆盖：自动步长（按画面短边切格）、步长归一化（0 = 自动、过小报错、上限截断）、
RGB / BGRA 两种像素布局下的参考线绘制（长度不变、主线混合颜色、次线存在、
alpha 通道保留）、像素数据校验、PNG 文件就地绘制（解码 → 绘制 → 重编码）。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import grid, png
from src.tools._screenshot.result import ScreenshotError


def _pixel_rgb(data: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(data[offset:offset + 3])  # type: ignore[return-value]


def _pixel_bgra(data: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    offset = (y * width + x) * 4
    return tuple(data[offset:offset + 4])  # type: ignore[return-value]


# ── 步长 ─────────────────────────────────────────────────

def test_auto_step_targets_about_ten_lines():
    assert grid.auto_step(1000, 800) == 80
    assert grid.auto_step(200, 200) == 20
    # 极小画面仍给出正步长（不小于下限）
    assert grid.auto_step(12, 12) >= grid.MIN_STEP


def test_resolve_step_zero_means_auto():
    assert grid.resolve_step(1000, 800, 0) == grid.auto_step(1000, 800)
    assert grid.resolve_step(1000, 800, None) == grid.auto_step(1000, 800)


def test_resolve_step_manual_and_clamped():
    assert grid.resolve_step(1000, 800, 50) == 50
    assert grid.resolve_step(1000, 800, 10 ** 6) == grid.MAX_STEP
    with pytest.raises(ScreenshotError):
        grid.resolve_step(1000, 800, 1)


# ── RGB 绘制 ─────────────────────────────────────────────

def test_draw_grid_rgb_keeps_size_and_paints_major_lines():
    width = height = 40
    pixels = bytes([0, 0, 0]) * (width * height)
    painted = grid.draw_grid_rgb(pixels, width, height, 20)
    assert len(painted) == len(pixels)
    # 主线在 x=0 / x=20：偏红（R 明显高于 G/B）
    r, g, b = _pixel_rgb(painted, width, 0, 5)
    assert r > g and r > b and r > 100
    # 非线像素保持不变（x=10 为次线位置，step=20 时次线步长 10）
    r2, g2, b2 = _pixel_rgb(painted, width, 5, 5)
    assert (r2, g2, b2) == (0, 0, 0) or g2 > 0


def test_draw_grid_rgb_minor_lines_appear_for_large_steps():
    width = height = 200
    pixels = bytes([0, 0, 0]) * (width * height)
    painted = grid.draw_grid_rgb(pixels, width, height, 100)
    # 次线在 x=50（step/2）
    r_minor, g_minor, _b = _pixel_rgb(painted, width, 50, 5)
    r_major, _, _ = _pixel_rgb(painted, width, 100, 5)
    assert r_minor > 0
    assert r_major > r_minor  # 主线比次线更亮


def test_draw_grid_bgra_preserves_alpha_and_uses_bgra_order():
    width = height = 30
    pixels = bytes([10, 20, 30, 200]) * (width * height)
    painted = grid.draw_grid_bgra(pixels, width, height, 15)
    b, g, r, a = _pixel_bgra(painted, width, 0, 0)
    assert a == 200          # alpha 不受影响
    assert r > b and r > g   # 参考线偏向红色（BGRA 中红色在第 3 字节）


def test_draw_rejects_bad_pixel_buffer():
    with pytest.raises(ScreenshotError):
        grid.draw_grid_rgb(b"\x00" * 10, 4, 4, 10)
    with pytest.raises(ScreenshotError):
        grid.draw_grid_bgra(b"\x00" * 10, 0, 4, 0)


# ── PNG 文件就地绘制 ─────────────────────────────────────

def test_paint_grid_on_png_file_keeps_size_and_changes_pixels(tmp_path):
    width, height = 60, 40
    source = bytes([5, 5, 5]) * (width * height)
    target = tmp_path / "shot.png"
    target.write_bytes(png.encode_png_rgb(width, height, source))

    out_width, out_height, step = grid.paint_grid_on_png_file(str(target), 30)
    assert (out_width, out_height) == (width, height)
    assert step == 30
    assert png.read_png_size(str(target)) == (width, height)

    from src.tools._screenshot import png_decode

    image = png_decode.decode_png_file(str(target))
    r, g, b = _pixel_rgb(image.rgb, width, 0, 3)
    assert r > g and r > b


def test_paint_grid_on_png_file_auto_step(tmp_path):
    width, height = 120, 90
    target = tmp_path / "auto.png"
    target.write_bytes(png.encode_png_rgb(width, height, bytes([0, 0, 0]) * (width * height)))
    _w, _h, step = grid.paint_grid_on_png_file(str(target), 0)
    assert step == grid.auto_step(width, height)


def test_paint_grid_on_png_file_rejects_non_png(tmp_path):
    target = tmp_path / "bad.png"
    target.write_bytes(b"not a png")
    with pytest.raises(ScreenshotError):
        grid.paint_grid_on_png_file(str(target), 10)
