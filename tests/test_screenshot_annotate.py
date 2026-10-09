"""截图标注（_screenshot/annotate.py）单元测试。

覆盖：矩形 / 点解析、矩形与十字绘制、5x7 文本绘制、annotate_png_file 的
boxes / points / labels / grid / output 行为、参数错误与元素超限。
"""

from __future__ import annotations

import pytest

from src.tools._screenshot import png
from src.tools._screenshot.annotate import (
    MAX_MARKS,
    AnnotateError,
    Box,
    Marker,
    annotate_png_file,
    draw_cross_rgb,
    draw_rect_rgb,
    draw_text_rgb,
    parse_box,
    parse_point,
)
from src.tools._screenshot.color import RGB, pixel_at
from src.tools._screenshot.png_decode import DecodedImage, decode_png_file


def _write_png(path, width=40, height=30, fill=(255, 255, 255)):
    path.write_bytes(png.encode_png_rgb(width, height,
                                        bytes(fill) * (width * height)))
    return str(path)


# ── 解析 ────────────────────────────────────────────────

def test_parse_box_forms():
    assert parse_box("5,6,7,8") == Box(5, 6, 7, 8)
    assert parse_box([1, 2, 3, 4]) == Box(1, 2, 3, 4)
    assert parse_box({"x": 1, "y": 2, "width": 3, "height": 4}) == Box(1, 2, 3, 4)
    assert parse_box({"x": 1, "y": 2, "w": 3, "h": 4}) == Box(1, 2, 3, 4)


def test_parse_box_invalid():
    with pytest.raises(AnnotateError):
        parse_box("1,2,3")
    with pytest.raises(AnnotateError):
        parse_box({"x": 1})


def test_parse_point_forms():
    assert parse_point("5,6") == Marker(5, 6)
    assert parse_point([7, 8]) == Marker(7, 8)
    assert parse_point({"x": 9, "y": 10}) == Marker(9, 10)


def test_parse_point_invalid():
    with pytest.raises(AnnotateError):
        parse_point("1")
    with pytest.raises(AnnotateError):
        parse_point("a,b")


# ── 绘制原语 ────────────────────────────────────────────

def test_draw_rect_border_only():
    pixels = bytearray(bytes((255, 255, 255)) * (10 * 10))
    draw_rect_rgb(pixels, 10, 10, Box(2, 2, 4, 4), RGB(255, 0, 0), thickness=1)
    image = DecodedImage(10, 10, bytes(pixels))
    assert pixel_at(image, 2, 2) == RGB(255, 0, 0)   # 边框
    assert pixel_at(image, 5, 5) == RGB(255, 0, 0)   # 边框（右下角）
    assert pixel_at(image, 4, 4) == RGB(255, 255, 255)  # 内部不填充


def test_draw_rect_clips_out_of_bounds():
    pixels = bytearray(bytes((0, 0, 0)) * (5 * 5))
    draw_rect_rgb(pixels, 5, 5, Box(-3, -3, 4, 4), RGB(1, 2, 3), thickness=1)
    image = DecodedImage(5, 5, bytes(pixels))
    assert pixel_at(image, 0, 0) == RGB(1, 2, 3)


def test_draw_cross_marks_center():
    pixels = bytearray(bytes((0, 0, 0)) * (20 * 20))
    draw_cross_rgb(pixels, 20, 20, Marker(10, 10), RGB(0, 255, 0), size=3)
    image = DecodedImage(20, 20, bytes(pixels))
    assert pixel_at(image, 10, 10) == RGB(0, 255, 0)
    assert pixel_at(image, 10, 7) == RGB(0, 255, 0)
    assert pixel_at(image, 7, 10) == RGB(0, 255, 0)


def test_draw_text_rgb_occupies_space():
    pixels = bytearray(bytes((0, 0, 0)) * (40 * 20))
    size = draw_text_rgb(pixels, 40, 20, 2, 2, "12", RGB(255, 255, 255), scale=2)
    assert size[0] > 0 and size[1] == 7 * 2
    assert any(pixels)  # 至少画了像素


# ── annotate_png_file ───────────────────────────────────

def test_annotate_draws_boxes_points_labels(tmp_path):
    source = _write_png(tmp_path / "a.png")
    out = str(tmp_path / "out.png")
    result = annotate_png_file(
        source,
        boxes=["5,5,10,8", {"x": 20, "y": 10, "width": 8, "height": 8}],
        points=[(30, 20)],
        labels=["A", "B", "C"],
        color="#FF0000",
        output=out,
    )
    assert result.path == out
    assert (result.boxes, result.points, result.labels) == (2, 1, 3)
    image = decode_png_file(out)
    assert pixel_at(image, 5, 5) == RGB(255, 0, 0)


def test_annotate_overwrites_in_place(tmp_path):
    source = _write_png(tmp_path / "a.png")
    result = annotate_png_file(source, points=["3,3"], color="blue")
    assert result.path == source
    image = decode_png_file(source)
    assert pixel_at(image, 3, 3) == RGB(0, 0, 255)


def test_annotate_with_grid_and_no_marks(tmp_path):
    source = _write_png(tmp_path / "a.png", width=20, height=20)
    result = annotate_png_file(source, grid=0)
    assert result.grid == 0
    assert result.boxes == 0 and result.points == 0


def test_annotate_requires_marks_or_grid(tmp_path):
    source = _write_png(tmp_path / "a.png")
    with pytest.raises(AnnotateError):
        annotate_png_file(source)


def test_annotate_rejects_too_many_marks(tmp_path):
    source = _write_png(tmp_path / "a.png")
    with pytest.raises(AnnotateError):
        annotate_png_file(source, points=["0,0"] * (MAX_MARKS + 1))


def test_annotate_missing_file():
    with pytest.raises(AnnotateError):
        annotate_png_file("/no/such/file.png", points=["1,1"])
