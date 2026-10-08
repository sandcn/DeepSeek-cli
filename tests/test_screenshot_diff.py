"""截图差异比较（``_screenshot.diff``）测试。

覆盖：完全相同 / 部分变化（含变化区域 bounding box）/ 尺寸不同 / 容差 /
PNG 文件比较（真实编码落盘后解码比较）。
"""

from __future__ import annotations

from src.tools._screenshot import png
from src.tools._screenshot.diff import (
    compare_images,
    compare_png_files,
    images_equal,
)
from src.tools._screenshot.png_decode import DecodedImage


def _image(width: int, height: int, rgb: bytes) -> DecodedImage:
    return DecodedImage(width=width, height=height, rgb=rgb)


def _solid(width: int, height: int, color=(0, 0, 0)) -> bytes:
    return bytes(color) * (width * height)


def test_identical_images_report_no_change():
    before = _image(4, 3, _solid(4, 3, (10, 20, 30)))
    after = _image(4, 3, _solid(4, 3, (10, 20, 30)))
    result = compare_images(before, after)
    assert result.changed is False
    assert result.changed_pixels == 0 and result.changed_ratio == 0.0
    assert result.region is None
    assert "未发生变化" in result.summary()


def test_partial_change_reports_region_and_ratio():
    before = _image(4, 2, _solid(4, 2, (0, 0, 0)))
    pixels = bytearray(before.rgb)
    # 第 2 行第 3 列（索引 (1, 2)）变白
    offset = (1 * 4 + 2) * 3
    pixels[offset:offset + 3] = b"\xff\xff\xff"
    after = _image(4, 2, bytes(pixels))
    result = compare_images(before, after)
    assert result.changed is True
    assert result.changed_pixels == 1
    assert result.total_pixels == 8
    assert result.region == {"x": 2, "y": 1, "width": 1, "height": 1}
    assert "已变化" in result.summary()


def test_tolerance_ignores_tiny_differences():
    before = _image(2, 1, bytes([100, 100, 100, 100, 100, 100]))
    after = _image(2, 1, bytes([104, 104, 104, 100, 100, 100]))
    assert compare_images(before, after, tolerance=0).changed is True
    assert compare_images(before, after, tolerance=8).changed is False


def test_size_change_is_treated_as_changed():
    before = _image(2, 2, _solid(2, 2))
    after = _image(3, 2, _solid(3, 2))
    result = compare_images(before, after, tolerance=0)
    assert result.changed is True and result.size_changed is True
    assert result.to_dict()["size_changed"] is True


def test_compare_png_files_roundtrip(tmp_path):
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    width, height = 3, 2
    bgra_red = bytes([0, 0, 255, 255]) * (width * height)     # BGRA 红
    bgra_green = bytes([0, 255, 0, 255]) * (width * height)   # BGRA 绿
    path_a.write_bytes(png.encode_png_bgra(width, height, bgra_red))
    path_b.write_bytes(png.encode_png_bgra(width, height, bgra_red))
    assert compare_png_files(str(path_a), str(path_b)).changed is False
    path_b.write_bytes(png.encode_png_bgra(width, height, bgra_green))
    assert compare_png_files(str(path_a), str(path_b)).changed is True
    assert images_equal(
        _image(1, 1, b"\x00\x00\x00"), _image(1, 1, b"\x00\x00\x00")) is True
