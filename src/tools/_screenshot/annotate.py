"""截图标注（``bash_opt`` 的 ``op=annotate`` 实现层）。

用途：把识别结果（``op=locate`` 的匹配框、控件中心、任意坐标点）画回截图上，
产出一张「带标注的画面」，便于人工 / 读图核对「找到的位置到底对不对」：

  - **矩形框**：标注一个区域（图标 / 控件 / 匹配框）；
  - **十字与圆点**：标注一个点（中心坐标）；
  - **编号标签**：给每个标注配一个短文本（数字 / 字母），便于与清单逐条对照。

实现为纯像素绘制（5x7 点阵字模），零第三方依赖；坐标为图像左上角原点，
与 ``op=screenshot`` / 输入 op 同源。产物可另存（``output``）或就地覆盖，
写入走原子替换避免半成品。

扩展方式：新增图元（圆、箭头、直方图）在此模块增加绘制函数与解析分支，
工具层无需改动。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Sequence

from . import png, png_decode
from .color import ColorError, RGB, parse_color
from .grid import draw_grid_rgb
from .transform import write_png_atomic

logger = logging.getLogger(__name__)

#: 默认标注颜色（醒目红）
DEFAULT_ANNOTATE_COLOR: tuple[int, int, int] = (255, 0, 0)

#: 默认边框线宽（像素）
DEFAULT_THICKNESS = 2

#: 标签文字放大倍率（像素），1 = 5x7 原始尺寸
DEFAULT_TEXT_SCALE = 2

#: 单个标注允许的最大绘制元素数（防御误传超长列表）
MAX_MARKS = 500

#: 5x7 点阵字模（行像素用 '0'/'1' 表示，'.' 视作 0）。
FONT_5X7: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "01100", "00100", "01000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "(": ("00110", "01000", "10000", "10000", "10000", "01000", "00110"),
    ")": ("01100", "00010", "00001", "00001", "00001", "00010", "01100"),
    "=": ("00000", "00000", "11111", "00000", "11111", "00000", "00000"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}

#: 字模字符宽度 / 高度 / 字符间距（像素）
_GLYPH_WIDTH = 5
_GLYPH_HEIGHT = 7
_GLYPH_GAP = 1


class AnnotateError(ValueError):
    """标注参数非法（坐标无法解析 / 图像无法解码 / 标记超限）。"""


@dataclass(frozen=True)
class AnnotationResult:
    """一次标注的结果。"""

    path: str
    width: int
    height: int
    boxes: int
    points: int
    labels: int
    grid: int | None = None

    def to_dict(self) -> dict:
        payload = {
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "boxes": self.boxes,
            "points": self.points,
            "labels": self.labels,
        }
        if self.grid is not None:
            payload["grid"] = self.grid
        return payload


@dataclass(frozen=True)
class Box:
    """一个矩形标记（图像像素坐标）。"""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True)
class Marker:
    """一个点标记。"""

    x: int
    y: int


def parse_box(item) -> Box:
    """把 ``'x,y,w,h'`` / 序列 / ``{'x','y','width','height'}`` 解析为 :class:`Box`。"""
    if isinstance(item, Box):
        return item
    if isinstance(item, dict):
        width = item.get("width", item.get("w"))
        height = item.get("height", item.get("h"))
        if width is None or height is None:
            raise AnnotateError(
                f"矩形标注需要 x/y/width/height 字段，当前: {item!r}"
            )
        try:
            return Box(int(item["x"]), int(item["y"]), int(width), int(height))
        except (KeyError, TypeError, ValueError):
            raise AnnotateError(
                f"矩形标注需要 x/y/width/height 字段，当前: {item!r}"
            ) from None
    if isinstance(item, (list, tuple)) and len(item) == 4:
        try:
            return Box(int(item[0]), int(item[1]), int(item[2]), int(item[3]))
        except (TypeError, ValueError):
            raise AnnotateError(f"矩形标注需为 4 个整数，当前: {item!r}") from None
    text = str(item or "").strip()
    parts = _split_numbers(text)
    if len(parts) != 4:
        raise AnnotateError(
            f"矩形标注格式非法: {item!r}（应为 'x,y,width,height' 或 "
            f"{{x,y,width,height}}）"
        )
    return Box(*parts)


def parse_point(item) -> Marker:
    """把 ``'x,y'`` / 序列 / ``{'x','y'}`` 解析为 :class:`Marker`。"""
    if isinstance(item, Marker):
        return item
    if isinstance(item, dict):
        try:
            return Marker(int(item["x"]), int(item["y"]))
        except (KeyError, TypeError, ValueError):
            raise AnnotateError(f"点标注需要 x/y 字段，当前: {item!r}") from None
    if isinstance(item, (list, tuple)) and len(item) == 2:
        try:
            return Marker(int(item[0]), int(item[1]))
        except (TypeError, ValueError):
            raise AnnotateError(f"点标注需为 2 个整数，当前: {item!r}") from None
    text = str(item or "").strip()
    parts = _split_numbers(text)
    if len(parts) != 2:
        raise AnnotateError(
            f"点标注格式非法: {item!r}（应为 'x,y' 或 {{x,y}}）"
        )
    return Marker(*parts)


def _split_numbers(text: str) -> list[int]:
    """从 ``'10,20,30,40'`` / ``'10 20 30 40'`` 中提取整数。"""
    normalized = text.replace("，", ",")
    values: list[int] = []
    token = ""
    for char in normalized:
        if char.isdigit() or (char == "-" and not token):
            token += char
        elif char in ", \t;×x":
            if token and token != "-":
                values.append(int(token))
            token = ""
        else:
            raise AnnotateError(f"标注含非法字符: {text!r}")
    if token and token != "-":
        values.append(int(token))
    return values


def annotate_png_file(path: str, *, boxes: Iterable = (), points: Iterable = (),
                      labels: Sequence | None = None, color=DEFAULT_ANNOTATE_COLOR,
                      output: str | None = None, thickness: int = DEFAULT_THICKNESS,
                      text_scale: int = DEFAULT_TEXT_SCALE,
                      grid: int | None = None) -> AnnotationResult:
    """在 PNG 上绘制矩形 / 十字 / 编号标签，写出到 ``output``（缺省覆盖原图）。

    Args:
        path: 输入 PNG 路径。
        boxes: 矩形列表（``'x,y,w,h'`` / 序列 / 字典）。
        points: 点列表（``'x,y'`` / 序列 / 字典）。
        labels: 与「矩形 + 点」合并顺序对应的标签；缺省自动编号（1 起）。
        color: 标记颜色（RGB / ``'#RRGGBB'`` / 颜色名）。
        output: 输出路径（``None`` = 覆盖 ``path``）。
        thickness: 边框线宽（像素，>= 1）。
        text_scale: 标签放大倍率（像素，>= 1）。
        grid: 可选坐标网格步长（``0`` = 自动；``None`` = 不叠加）。

    Raises:
        AnnotateError: 标记无法解析或数量超限。
        ScreenshotError: 图像无法解码或写入失败。
    """
    rgb_color = _resolve_color(color)
    box_list = [parse_box(item) for item in list(boxes or [])]
    point_list = [parse_point(item) for item in list(points or [])]
    total = len(box_list) + len(point_list)
    if total == 0 and grid is None:
        raise AnnotateError("annotate 至少需要 boxes / points 之一，或提供 grid 叠加网格")
    if len(box_list) > MAX_MARKS or len(point_list) > MAX_MARKS:
        raise AnnotateError(
            f"单次标注的元素过多（最多各 {MAX_MARKS} 个）：请分多次调用"
        )
    label_list = _normalize_labels(labels, total)
    try:
        image = png_decode.decode_png_file(path)
    except png_decode.PNGDecodeError as exc:
        raise AnnotateError(f"截图无法解码用于标注: {exc}") from exc
    except OSError as exc:
        raise AnnotateError(f"读取截图失败: {exc}") from exc
    pixels = image.rgb
    if grid is not None:
        pixels = draw_grid_rgb(pixels, image.width, image.height, int(grid))
    mutable = bytearray(pixels)
    # 先画标签、再画标记：标签不会被标记覆盖（保持可读），标记本身也始终清晰
    for index, box in enumerate(box_list):
        _draw_box_label(mutable, image.width, image.height, box,
                        label_list[index], rgb_color, text_scale)
    for offset, point in enumerate(point_list):
        _draw_point_label(mutable, image.width, image.height, point,
                          label_list[len(box_list) + offset], rgb_color, text_scale)
    for box in box_list:
        draw_rect_rgb(mutable, image.width, image.height, box, rgb_color,
                      thickness=thickness)
    for point in point_list:
        draw_cross_rgb(mutable, image.width, image.height, point, rgb_color,
                       thickness=thickness)
    data = png.encode_png_rgb(image.width, image.height, bytes(mutable))
    target = output or path
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    write_png_atomic(target, data)
    return AnnotationResult(
        path=target,
        width=image.width,
        height=image.height,
        boxes=len(box_list),
        points=len(point_list),
        labels=sum(1 for label in label_list if label),
        grid=int(grid) if grid is not None else None,
    )


def _resolve_color(color) -> RGB:
    """解析标注颜色（``RGB`` / 文本 / 三元组），失败时回退默认色。"""
    if color is None:
        return RGB(*DEFAULT_ANNOTATE_COLOR)
    if isinstance(color, RGB):
        return color
    if isinstance(color, (list, tuple)) and len(color) == 3:
        return RGB(*(int(value) for value in color))
    try:
        return parse_color(color)
    except ColorError as exc:
        raise AnnotateError(str(exc)) from exc


def _normalize_labels(labels, total: int) -> list[str]:
    """把标签归一化为长度等于 ``total`` 的字符串列表（缺省自动编号）。"""
    if total <= 0:
        return []
    if labels is None:
        return [str(index + 1) for index in range(total)]
    if isinstance(labels, str):
        items = [labels]
    else:
        items = [str(item) for item in labels]
    result = list(items[:total])
    while len(result) < total:
        result.append(str(len(result) + 1))
    return result


# ── 绘制原语（RGB 像素，坐标为整图像素） ────────────────

def draw_rect_rgb(pixels: bytearray, width: int, height: int, box: Box,
                  color: RGB, *, thickness: int = DEFAULT_THICKNESS) -> None:
    """画矩形**边框**（超出图像的部分自动裁剪）。"""
    line = max(int(thickness), 1)
    left = max(box.x, 0)
    top = max(box.y, 0)
    right = min(box.right, width)
    bottom = min(box.bottom, height)
    if right <= left or bottom <= top:
        return
    for offset in range(line):
        _fill_row(pixels, width, left, min(top + offset, height - 1),
                  right - left, color)
        _fill_row(pixels, width, left, max(bottom - 1 - offset, 0),
                  right - left, color)
        _fill_column(pixels, width, height, min(left + offset, width - 1),
                     top, bottom - top, color)
        _fill_column(pixels, width, height, max(right - 1 - offset, 0),
                     top, bottom - top, color)


def draw_cross_rgb(pixels: bytearray, width: int, height: int, point: Marker,
                   color: RGB, *, thickness: int = DEFAULT_THICKNESS,
                   size: int = 6) -> None:
    """画十字 + 中心点（以 ``point`` 为中心，超出图像的部分自动裁剪）。"""
    arm = max(int(size), 1)
    line = max(int(thickness), 1)
    start = -(line // 2)
    for offset in range(start, start + line):
        _fill_row(pixels, width, point.x - arm, point.y + offset,
                  arm * 2 + 1, color)
        _fill_column(pixels, width, height, point.x + offset, point.y - arm,
                     arm * 2 + 1, color)


def draw_text_rgb(pixels: bytearray, width: int, height: int, x: int, y: int,
                  text: str, color: RGB, *, scale: int = 1,
                  background: RGB | None = None) -> tuple[int, int]:
    """在 ``(x, y)``（左上角）用 5x7 点阵绘制文本，返回占用尺寸 ``(w, h)``。

    未知字符按 ``'?'`` 绘制；``background`` 非空时先铺一层底色，提升可读性。
    """
    scale = max(int(scale), 1)
    text = str(text or "")
    if not text:
        return (0, 0)
    glyphs = [FONT_5X7.get(char.upper(), FONT_5X7["?"]) for char in text]
    text_width = len(glyphs) * (_GLYPH_WIDTH + _GLYPH_GAP) - _GLYPH_GAP
    text_height = _GLYPH_HEIGHT
    out_width = text_width * scale
    out_height = text_height * scale
    if background is not None:
        pad = scale
        _fill_row(pixels, width, x - pad, y - pad, out_width + pad * 2,
                  background)
        for row in range(-pad, out_height + pad):
            _fill_column(pixels, width, height, x - pad, y + row,
                         out_width + pad * 2, background)
    for column, glyph in enumerate(glyphs):
        origin_x = x + column * (_GLYPH_WIDTH + _GLYPH_GAP) * scale
        for row, line in enumerate(glyph):
            for cell, char in enumerate(line):
                if char != "1":
                    continue
                px = origin_x + cell * scale
                py = y + row * scale
                for dy in range(scale):
                    _fill_row(pixels, width, px, py + dy, scale, color)
    return (out_width, out_height)


def _draw_box_label(pixels: bytearray, width: int, height: int, box: Box,
                    label: str, color: RGB, text_scale: int) -> None:
    """在矩形的**上方**绘制编号标签；上方空间不足时放到矩形右上外侧。"""
    if not str(label or "").strip():
        return
    scale = max(int(text_scale), 1)
    text_width, text_height = _label_size(label, scale)
    label_x = box.x
    label_y = box.y - text_height - scale
    if label_y < 0:
        label_x = box.right + scale
        label_y = box.y
    _blit_label(pixels, width, height, label_x, label_y, label, color, scale,
                text_width, text_height)


def _draw_point_label(pixels: bytearray, width: int, height: int, point: Marker,
                      label: str, color: RGB, text_scale: int) -> None:
    """在点的**右侧**绘制编号标签（避免覆盖十字中心）。"""
    if not str(label or "").strip():
        return
    scale = max(int(text_scale), 1)
    text_width, text_height = _label_size(label, scale)
    label_x = point.x + 4 * scale
    label_y = point.y - text_height // 2
    _blit_label(pixels, width, height, label_x, label_y, label, color, scale,
                text_width, text_height)


def _label_size(label: str, scale: int) -> tuple[int, int]:
    """标签文本的像素尺寸 ``(width, height)``。"""
    text = str(label)
    return ((len(text) * (_GLYPH_WIDTH + _GLYPH_GAP) - _GLYPH_GAP) * scale,
            _GLYPH_HEIGHT * scale)


def _blit_label(pixels: bytearray, width: int, height: int, label_x: int,
                label_y: int, label: str, color: RGB, scale: int,
                text_width: int, text_height: int) -> None:
    """在给定位置绘制带反色底纹的标签（自动夹到图像内）。"""
    pad = scale
    x = max(0, min(int(label_x), max(width - text_width - pad, 0)))
    y = max(0, min(int(label_y), max(height - text_height - pad, 0)))
    background = RGB(255 - color.r, 255 - color.g, 255 - color.b)
    for row in range(-pad, text_height + pad):
        _fill_row(pixels, width, x - pad, y + row, text_width + pad * 2,
                  background)
    draw_text_rgb(pixels, width, height, x, y, label, color, scale=scale)


def _fill_row(pixels: bytearray, width: int, x: int, y: int, length: int,
              color: RGB) -> None:
    """在 ``y`` 行从 ``x`` 起填充 ``length`` 个像素（自动裁剪到图像内）。"""
    if length <= 0:
        return
    start = max(x, 0)
    end = min(x + length, width)
    if end <= start:
        return
    offset = (y * width + start) * 3
    pixels[offset:offset + (end - start) * 3] = bytes(
        (color.r, color.g, color.b)) * (end - start)


def _fill_column(pixels: bytearray, width: int, height: int, x: int, y: int,
                 length: int, color: RGB) -> None:
    """在 ``x`` 列从 ``y`` 起填充 ``length`` 个像素（自动裁剪到图像内）。"""
    if length <= 0:
        return
    start = max(y, 0)
    end = min(y + length, height)
    if end <= start:
        return
    pixel = bytes((color.r, color.g, color.b))
    for row in range(start, end):
        offset = (row * width + x) * 3
        pixels[offset:offset + 3] = pixel


__all__ = [
    "DEFAULT_ANNOTATE_COLOR",
    "DEFAULT_TEXT_SCALE",
    "DEFAULT_THICKNESS",
    "FONT_5X7",
    "MAX_MARKS",
    "AnnotationResult",
    "AnnotateError",
    "Box",
    "Marker",
    "annotate_png_file",
    "draw_cross_rgb",
    "draw_rect_rgb",
    "draw_text_rgb",
    "parse_box",
    "parse_point",
]
