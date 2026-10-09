"""模板匹配（``_screenshot.imagematch``）测试。

手工构造像素图，覆盖：精确命中（位置 / 得分）、容差过滤、多尺度搜索、
非极大值抑制、参数校验（模板过大 / 过小 / 容差越界 / 缩放非法）。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot.imagematch import (
    ImageMatchError,
    TemplateMatch,
    match_template,
    scale_list,
)
from src.tools._screenshot.png_decode import DecodedImage


def _image(width, height, fill=(0, 0, 0)):
    pixels = bytearray()
    for _ in range(width * height):
        pixels.extend(fill)
    return DecodedImage(width, height, bytes(pixels))


def _paint(image: DecodedImage, x, y, w, h, color):
    data = bytearray(image.rgb)
    for row in range(y, y + h):
        for col in range(x, x + w):
            base = (row * image.width + col) * 3
            data[base:base + 3] = bytes(color)
    return DecodedImage(image.width, image.height, bytes(data))


def test_exact_match_returns_position_and_score():
    haystack = _image(40, 40, (10, 20, 30))
    haystack = _paint(haystack, 12, 7, 8, 8, (200, 100, 50))
    template = _image(8, 8, (200, 100, 50))
    matches = match_template(haystack, template)
    assert matches
    top = matches[0]
    assert (top.x, top.y) == (12, 7)
    assert top.width == 8 and top.height == 8
    assert top.score == pytest.approx(1.0)
    assert top.center_x == 16 and top.center_y == 11
    assert top.to_dict()["center_x"] == 16


def test_noise_template_returns_empty():
    haystack = _image(30, 30, (0, 0, 0))
    # 棋盘模板，与全黑画面不匹配
    template = bytearray()
    for row in range(6):
        for col in range(6):
            value = 255 if (row + col) % 2 else 0
            template.extend((value, value, value))
    matches = match_template(haystack, DecodedImage(6, 6, bytes(template)),
                             tolerance=5)
    assert matches == []


def test_tolerance_allows_minor_difference():
    haystack = _paint(_image(20, 20), 5, 5, 6, 6, (100, 100, 100))
    template = _image(6, 6, (110, 110, 110))  # 每通道差 10
    assert match_template(haystack, template, tolerance=5) == []
    matches = match_template(haystack, template, tolerance=20)
    assert matches and (matches[0].x, matches[0].y) == (5, 5)


def _unique(size):
    """构造每个像素颜色都不同的图案（避免周期性重复造成歧义匹配）。"""
    data = bytearray()
    for row in range(size):
        for col in range(size):
            data.extend(((row * 20 + 10) % 256, (col * 20 + 30) % 256,
                         (row * 7 + col * 11 + 5) % 256))
    return DecodedImage(size, size, bytes(data))


def _upscale(image, factor):
    """最近邻放大（每像素 factor x factor）。"""
    data = bytearray()
    for row in range(image.height):
        line = bytearray()
        for col in range(image.width):
            base = (row * image.width + col) * 3
            line.extend(image.rgb[base:base + 3] * factor)
        data.extend(bytes(line) * factor)
    return DecodedImage(image.width * factor, image.height * factor, bytes(data))


def test_multi_scale_search_finds_scaled_template():
    template = _unique(4)
    block = _upscale(template, 2)
    haystack = _image(60, 60)
    base = bytearray(haystack.rgb)
    for row in range(block.height):
        for col in range(block.width):
            src = (row * block.width + col) * 3
            dst = ((20 + row) * 60 + (20 + col)) * 3
            base[dst:dst + 3] = block.rgb[src:src + 3]
    haystack = DecodedImage(60, 60, bytes(base))
    matches = match_template(haystack, template, tolerance=1,
                             min_scale=1.0, max_scale=3.0, scale_steps=5)
    assert matches
    best = matches[0]
    assert (best.x, best.y) == (20, 20)
    assert best.width == 8 and best.height == 8
    assert best.scale == pytest.approx(2.0)


def test_non_max_suppression_dedupes_overlapping_hits():
    haystack = _paint(_image(30, 30), 10, 10, 8, 8, (200, 200, 200))
    template = _image(8, 8, (200, 200, 200))
    matches = match_template(haystack, template, max_results=10)
    # 同一目标附近的多个候选应被抑制成一条
    assert len([m for m in matches if abs(m.x - 10) <= 2 and abs(m.y - 10) <= 2]) == 1


def test_scale_list_validation_and_values():
    assert scale_list(1.0, 1.0, 1) == [1.0]
    assert scale_list(0.5, 1.0, 3) == pytest.approx([0.5, 0.75, 1.0])
    with pytest.raises(ImageMatchError):
        scale_list(2.0, 1.0, 3)
    with pytest.raises(ImageMatchError):
        scale_list(0.0, 1.0, 1)
    with pytest.raises(ImageMatchError):
        scale_list(1.0, 1.0, 0)


def test_parameter_validation():
    big = _image(50, 50)
    small = _image(5, 5)
    with pytest.raises(ImageMatchError):
        match_template(small, big)  # 模板大于图像
    with pytest.raises(ImageMatchError):
        match_template(big, _image(2, 2))  # 模板过小
    with pytest.raises(ImageMatchError):
        match_template(big, small, tolerance=999)
    with pytest.raises(ImageMatchError):
        match_template(big, small, max_results=0)


def test_match_results_are_sorted_by_score():
    haystack = _paint(_image(30, 30), 2, 2, 6, 6, (200, 0, 0))
    # 另一处颜色略有偏差
    haystack = _paint(haystack, 18, 18, 6, 6, (190, 5, 5))
    template = _image(6, 6, (200, 0, 0))
    matches = match_template(haystack, template, tolerance=40, max_results=5)
    assert matches[0].score >= matches[-1].score
    assert matches[0].x >= 0


def test_template_match_dataclass_defaults():
    match = TemplateMatch(x=1, y=2, width=4, height=6, score=0.9)
    assert match.scale == 1.0
