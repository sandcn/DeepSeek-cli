"""截图取色与颜色检测（_screenshot/color.py）单元测试。

覆盖：颜色解析（十六进制 / rgb() / 三元组 / 颜色名）、通道容差匹配、点取色
越界、区域统计（均值 / 极值 / 主色）、颜色查找（连通块 / 容差 / 区域偏移 /
最小像素数过滤）。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot.color import (
    ColorError,
    ColorRegion,
    RGB,
    find_color_regions,
    parse_color,
    pixel_at,
    region_stats,
    summarize,
)
from src.tools._screenshot.png_decode import DecodedImage
from src.tools._screenshot.transform import CropError, CropRegion


def _image(width: int, height: int, fill=(0, 0, 0), blocks=()) -> DecodedImage:
    pixels = bytearray(bytes(fill) * (width * height))
    for bx, by, bw, bh, color in blocks:
        for row in range(by, by + bh):
            for col in range(bx, bx + bw):
                offset = (row * width + col) * 3
                pixels[offset:offset + 3] = bytes(color)
    return DecodedImage(width, height, bytes(pixels))


# ── 颜色解析 ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("#FF0000", (255, 0, 0)),
    ("ff0000", (255, 0, 0)),
    ("0x00FF00", (0, 255, 0)),
    ("#0f0", (0, 255, 0)),
    ("rgb(0, 0, 255)", (0, 0, 255)),
    ("rgba(1, 2, 3, 0.5)", (1, 2, 3)),
    ("10, 20, 30", (10, 20, 30)),
    ("10 20 30", (10, 20, 30)),
    ("red", (255, 0, 0)),
    ("White", (255, 255, 255)),
])
def test_parse_color_accepts_common_forms(text, expected):
    assert (parse_color(text).r, parse_color(text).g, parse_color(text).b) == expected


def test_parse_color_accepts_sequence_and_rgb():
    assert parse_color([1, 2, 3]) == RGB(1, 2, 3)
    assert parse_color(RGB(4, 5, 6)) == RGB(4, 5, 6)


@pytest.mark.parametrize("bad", ["", "notacolor", "#12", "rgb(1,2)", "300,0,0"])
def test_parse_color_rejects_invalid(bad):
    with pytest.raises(ColorError):
        parse_color(bad)


def test_rgb_channel_validation():
    with pytest.raises(ColorError):
        RGB(300, 0, 0)
    with pytest.raises(ColorError):
        RGB(True, 0, 0)  # type: ignore[arg-type]


def test_rgb_hex_gray_and_match():
    assert RGB(255, 0, 0).hex == "#FF0000"
    assert RGB(255, 255, 255).gray == 255
    assert RGB(0, 0, 0).gray == 0
    assert RGB(255, 0, 0).matches(RGB(255, 3, 1), tolerance=2)
    assert not RGB(255, 0, 0).matches(RGB(0, 0, 255))


# ── 点取色 ──────────────────────────────────────────────

def test_pixel_at_reads_color():
    image = _image(10, 10, blocks=[(3, 4, 2, 2, (12, 34, 56))])
    assert pixel_at(image, 3, 4) == RGB(12, 34, 56)
    assert pixel_at(image, 4, 5) == RGB(12, 34, 56)
    assert pixel_at(image, 0, 0) == RGB(0, 0, 0)


def test_pixel_at_out_of_bounds():
    image = _image(5, 5)
    with pytest.raises(ColorError):
        pixel_at(image, 5, 0)
    with pytest.raises(ColorError):
        pixel_at(image, 0, -1)


# ── 区域统计 ────────────────────────────────────────────

def test_region_stats_summary():
    image = _image(10, 10, fill=(10, 20, 30),
                   blocks=[(0, 0, 2, 2, (200, 100, 50))])
    stats = region_stats(image, CropRegion(0, 0, 2, 2))
    assert stats["pixels"] == 4
    assert stats["average"] == {"r": 200, "g": 100, "b": 50, "hex": "#C86432"}
    assert stats["min"]["r"] == 200 and stats["max"]["b"] == 50
    assert stats["dominant"]["hex"] == "#C86432"


def test_region_stats_whole_image_and_bounds():
    image = _image(4, 4, fill=(1, 2, 3))
    stats = region_stats(image)
    assert stats["pixels"] == 16
    assert stats["average"]["hex"] == "#010203"
    with pytest.raises(CropError):
        region_stats(image, CropRegion(0, 0, 10, 10))


# ── 颜色查找 ────────────────────────────────────────────

def test_find_color_regions_single_cluster():
    image = _image(20, 20, blocks=[(5, 6, 3, 2, (255, 0, 0))])
    regions = find_color_regions(image, RGB(255, 0, 0), tolerance=0)
    assert len(regions) == 1
    region = regions[0]
    assert (region.left, region.top, region.width, region.height) == (5, 6, 3, 2)
    assert region.pixels == 6
    assert (region.center_x, region.center_y) == (6, 7)


def test_find_color_regions_multiple_clusters_sorted_by_size():
    image = _image(30, 10, blocks=[
        (0, 0, 2, 2, (0, 255, 0)),
        (10, 0, 4, 4, (0, 255, 0)),
    ])
    regions = find_color_regions(image, RGB(0, 255, 0), tolerance=0)
    assert [region.pixels for region in regions] == [16, 4]


def test_find_color_regions_respects_tolerance_and_min_pixels():
    image = _image(6, 6, blocks=[(0, 0, 1, 1, (250, 0, 0)),
                                 (3, 3, 1, 1, (240, 0, 0))])
    # 容差 12：两个点都算匹配
    assert len(find_color_regions(image, RGB(255, 0, 0), tolerance=12)) == 2
    # 容差 0：都不匹配
    assert find_color_regions(image, RGB(255, 0, 0), tolerance=0) == []
    # min_pixels=2 过滤掉单像素块
    assert find_color_regions(image, RGB(255, 0, 0), tolerance=12,
                              min_pixels=2) == []


def test_find_color_regions_region_offset_and_limit():
    image = _image(30, 30, blocks=[(2, 2, 2, 2, (9, 9, 9)),
                                   (20, 20, 2, 2, (9, 9, 9))])
    regions = find_color_regions(image, RGB(9, 9, 9), tolerance=0,
                                 region=CropRegion(10, 10, 15, 15))
    assert len(regions) == 1
    assert (regions[0].left, regions[0].top) == (20, 20)

    # max_regions 限制返回条数
    both = find_color_regions(image, RGB(9, 9, 9), tolerance=0, max_regions=1)
    assert len(both) == 1


def test_color_region_center_and_dict():
    region = ColorRegion(5, 6, 3, 2, 6)
    payload = region.to_dict()
    assert payload["x"] == 5 and payload["center_x"] == 6
    assert payload["center_y"] == 7


def test_summarize_includes_target_and_match():
    payload = summarize(RGB(255, 0, 0), RGB(250, 0, 0), tolerance=12)
    assert payload["match"] is True
    assert payload["target"]["hex"] == "#FA0000"
    assert payload["delta"] == 5
