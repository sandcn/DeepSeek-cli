"""截图取色与颜色检测（``bash_opt`` 的 ``op=pixel`` 实现层）。

用途：界面里没有可枚举控件、模板匹配又需要先备好模板时，直接读**像素颜色**
来判定状态——状态灯（红/绿）、进度条填充色、按钮高亮、画布某点颜色等：

  - **点取色**：读窗口截图里某坐标的 RGB（与输入 op / ``op=screenshot``
    同源坐标系，可直接对照截图）；
  - **区域取色**：读一块矩形区域的均值 / 极值 / 主色（判断「整体偏红还是偏绿」）；
  - **颜色查找**：在整窗或指定区域内查找与目标颜色容差范围内匹配的像素，
    按**连通块**聚合成若干候选位置（每个块给出包围盒与中心点，可直接点击），
    用于定位多个同色元素（一排状态灯、地图标记等）。

坐标为窗口截图左上角原点；颜色用「每通道平均绝对差」度量，``tolerance``
为允许的平均通道差（0..255），与 ``op=locate`` 的模板匹配容差口径一致。

纯 Python，零第三方依赖；新增能力（如颜色直方图、渐变色检测）在此模块扩展，
工具层与平台后端无需改动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .png_decode import DecodedImage, decode_png_file
from .transform import CropRegion, crop_rgb

#: 默认颜色容差（每通道平均绝对差，0..255）。0 表示完全一致。
DEFAULT_COLOR_TOLERANCE = 12

#: ``op=pixel`` 颜色查找返回的最大连通块数
DEFAULT_MAX_REGIONS = 10

#: 颜色查找允许的最大连通块数（防御误传超大值）
MAX_REGIONS_LIMIT = 100

#: 单次颜色查找扫描的像素数上限（防御超大图逐像素扫描过慢）
MAX_SCAN_PIXELS = 64_000_000

#: 区域统计的采样上限（主色统计用，避免逐像素 Counter 过慢）
_STATS_SAMPLE_LIMIT = 40000

#: 宽高均为 0 的连通块（占位）
_EMPTY_BOX = (0, 0, 0, 0)


class ColorError(ValueError):
    """颜色参数非法或取色失败（越界 / 无法解析 / 尺寸非法）。"""


_HEX_RE = re.compile(r"^(?:#|0x)?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})"
    r"\s*(?:[,/]\s*[\d.]+\s*)?\)$"
)
_TRIPLE_RE = re.compile(r"^\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*$")

#: 常用颜色名（小写）→ RGB。收录常见界面色，未收录时建议用十六进制。
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "lime": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "cyan": (0, 255, 255),
    "aqua": (0, 255, 255),
    "magenta": (255, 0, 255),
    "pink": (255, 192, 203),
    "brown": (165, 42, 42),
    "teal": (0, 128, 128),
    "navy": (0, 0, 128),
    "maroon": (128, 0, 0),
    "olive": (128, 128, 0),
    "gold": (255, 215, 0),
    "transparent": (0, 0, 0),
}


@dataclass(frozen=True)
class RGB:
    """一个 RGB 颜色。"""

    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        for name in ("r", "g", "b"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ColorError(f"颜色通道 {name} 必须是整数，当前: {value!r}")
            if not 0 <= value <= 255:
                raise ColorError(f"颜色通道 {name} 需在 0..255，当前: {value}")

    @property
    def hex(self) -> str:
        """``#RRGGBB`` 形式。"""
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @property
    def gray(self) -> int:
        """亮度（ITU-R BT.601 加权）。"""
        return (self.r * 299 + self.g * 587 + self.b * 114) // 1000

    def channel_delta(self, other: "RGB") -> int:
        """三通道绝对差之和（0..765）。"""
        return (abs(self.r - other.r) + abs(self.g - other.g)
                + abs(self.b - other.b))

    def matches(self, other: "RGB", tolerance: int = DEFAULT_COLOR_TOLERANCE) -> bool:
        """是否在容差范围内（按每通道平均绝对差比较）。"""
        limit = max(int(tolerance), 0) * 3
        return self.channel_delta(other) <= limit

    def to_dict(self) -> dict:
        return {"r": self.r, "g": self.g, "b": self.b, "hex": self.hex}


@dataclass(frozen=True)
class ColorRegion:
    """一块匹配目标颜色的连通像素区域。"""

    left: int
    top: int
    width: int
    height: int
    pixels: int

    @property
    def center_x(self) -> int:
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        return self.top + self.height // 2

    def to_dict(self) -> dict:
        return {
            "x": self.left,
            "y": self.top,
            "width": self.width,
            "height": self.height,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "pixels": self.pixels,
        }


def parse_color(text) -> RGB:
    """把颜色文本解析为 :class:`RGB`。

    支持的形式：

      - 十六进制：``'#RRGGBB'`` / ``'RRGGBB'`` / ``'#RGB'`` / ``'0xRRGGBB'``；
      - 函数式：``'rgb(255,0,0)'`` / ``'rgba(255,0,0,1)'``；
      - 三元组：``'255,0,0'`` / ``'255 0 0'``；
      - 颜色名：``'red'`` / ``'green'`` 等（见 :data:`NAMED_COLORS`）。

    Raises:
        ColorError: 无法解析。
    """
    if isinstance(text, RGB):
        return text
    if isinstance(text, (list, tuple)) and len(text) == 3:
        return RGB(*(_channel(value) for value in text))
    raw = str(text or "").strip()
    if not raw:
        raise ColorError("颜色不能为空，示例: '#FF0000' / 'rgb(255,0,0)' / 255,0,0 / 'red'")
    named = NAMED_COLORS.get(raw.lower())
    if named is not None:
        return RGB(*named)
    match = _HEX_RE.match(raw)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        return RGB(int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    match = _RGB_RE.match(raw.lower().replace(" ", ""))
    if match:
        return RGB(*(_channel(match.group(index)) for index in (1, 2, 3)))
    match = _TRIPLE_RE.match(raw)
    if match:
        return RGB(*(_channel(match.group(index)) for index in (1, 2, 3)))
    raise ColorError(
        f"无法解析颜色: {text!r}。支持 '#RRGGBB' / 'rgb(r,g,b)' / 'r,g,b' 或颜色名"
        f"（如 'red'）"
    )


def _channel(value) -> int:
    """把单个通道值解析为 0..255 的整数。"""
    if isinstance(value, bool):
        raise ColorError(f"颜色通道不能是布尔值: {value!r}")
    try:
        number = int(str(value).strip(), 10)
    except (TypeError, ValueError):
        raise ColorError(f"颜色通道必须是整数: {value!r}") from None
    if not 0 <= number <= 255:
        raise ColorError(f"颜色通道需在 0..255，当前: {number}")
    return number


def _resolve_region(image: DecodedImage, region: CropRegion | None) -> CropRegion:
    """把可选裁剪区域归一化为覆盖整图或指定区域，并校验越界。"""
    if region is None:
        return CropRegion(0, 0, image.width, image.height)
    region.validate_against(image.width, image.height)
    return region


def pixel_at(image: DecodedImage, x: int, y: int) -> RGB:
    """读取 ``image`` 在 ``(x, y)`` 处的颜色（左上角原点）。

    Raises:
        ColorError: 坐标越界。
    """
    if not 0 <= x < image.width or not 0 <= y < image.height:
        raise ColorError(
            f"取色坐标越界: ({x}, {y})，需满足 0<=x<{image.width} 且 "
            f"0<=y<{image.height}（窗口截图尺寸 {image.width}x{image.height}）"
        )
    offset = (y * image.width + x) * 3
    return RGB(image.rgb[offset], image.rgb[offset + 1], image.rgb[offset + 2])


def pixel_at_file(path: str, x: int, y: int) -> RGB:
    """从 PNG 文件读取 ``(x, y)`` 处的颜色。"""
    return pixel_at(decode_png_file(path), x, y)


def region_stats(image: DecodedImage, region: CropRegion | None = None,
                 *, sample_limit: int = _STATS_SAMPLE_LIMIT) -> dict:
    """统计一块区域的颜色：均值 / 极值 / 主色 / 灰度均值。

    Args:
        image: 解码图像。
        region: 目标区域（``None`` = 整图）。
        sample_limit: 主色统计的采样上限。

    Raises:
        CropError: 区域越界。
    """
    area = _resolve_region(image, region)
    pixels = crop_rgb(image.rgb, image.width, image.height, area)
    count = area.width * area.height
    min_rgb = [255, 255, 255]
    max_rgb = [0, 0, 0]
    totals = [0, 0, 0]
    step = max(1, count // max(int(sample_limit), 1))
    counter: dict[tuple[int, int, int], int] = {}
    index = 0
    for offset in range(0, count * 3, 3):
        r, g, b = pixels[offset], pixels[offset + 1], pixels[offset + 2]
        totals[0] += r
        totals[1] += g
        totals[2] += b
        for channel, value in enumerate((r, g, b)):
            if value < min_rgb[channel]:
                min_rgb[channel] = value
            if value > max_rgb[channel]:
                max_rgb[channel] = value
        if index % step == 0:
            key = (r, g, b)
            counter[key] = counter.get(key, 0) + 1
        index += 1
    average = RGB(*(total // count for total in totals))
    dominant = max(counter.items(), key=lambda pair: pair[1])[0] if counter else (0, 0, 0)
    return {
        "width": area.width,
        "height": area.height,
        "pixels": count,
        "average": average.to_dict(),
        "min": RGB(*min_rgb).to_dict(),
        "max": RGB(*max_rgb).to_dict(),
        "dominant": RGB(*dominant).to_dict(),
        "dominant_ratio": round((counter.get(dominant, 0) * step) / count, 4)
        if count else 0.0,
    }


def find_color_regions(image: DecodedImage, target: RGB, *,
                       tolerance: int = DEFAULT_COLOR_TOLERANCE,
                       region: CropRegion | None = None,
                       max_regions: int = DEFAULT_MAX_REGIONS,
                       min_pixels: int = 1,
                       label: str = "颜色") -> list[ColorRegion]:
    """在 ``region``（缺省整图）内查找与 ``target`` 匹配的像素连通块。

    用 4 连通扫描线聚合成若干候选区域，按像素数降序返回（默认最多
    ``max_regions`` 个）。坐标是相对**整图**左上角的像素（叠加了 region 偏移），
    与输入 op / ``op=screenshot`` 同源，可直接点击。

    Raises:
        ColorError: 区域越界或扫描规模过大。
    """
    area = _resolve_region(image, region)
    if area.width * area.height > MAX_SCAN_PIXELS:
        raise ColorError(
            f"颜色查找区域过大（{area.width}x{area.height}），请用 region 缩小范围"
        )
    limit = max(int(tolerance), 0) * 3
    mask = _build_mask(image, area, target, limit)
    return _collect_regions(mask, area, max_regions=max_regions,
                            min_pixels=min_pixels)


def _build_mask(image: DecodedImage, area: CropRegion, target: RGB,
                limit: int) -> bytearray:
    """在区域内逐像素判定是否匹配，返回同尺寸的 0/1 掩码。"""
    pixels = image.rgb
    stride = image.width * 3
    mask = bytearray(area.width * area.height)
    tr, tg, tb = target.r, target.g, target.b
    for row in range(area.height):
        source = (area.y + row) * stride + area.x * 3
        base = row * area.width
        for column in range(area.width):
            offset = source + column * 3
            delta = (abs(pixels[offset] - tr) + abs(pixels[offset + 1] - tg)
                     + abs(pixels[offset + 2] - tb))
            if delta <= limit:
                mask[base + column] = 1
    return mask


def _collect_regions(mask: bytearray, area: CropRegion, *, max_regions: int,
                     min_pixels: int) -> list[ColorRegion]:
    """把掩码聚合为连通块（4 连通，扫描线 + 显式栈）。"""
    width, height = area.width, area.height
    visited = bytearray(len(mask))
    regions: list[ColorRegion] = []
    min_pixels = max(int(min_pixels), 1)
    for start in range(len(mask)):
        if not mask[start] or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        min_x = max_x = start % width
        min_y = max_y = start // width
        count = 0
        while stack:
            index = stack.pop()
            count += 1
            x = index % width
            y = index // width
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            if x > 0:
                _push(mask, visited, stack, index - 1)
            if x + 1 < width:
                _push(mask, visited, stack, index + 1)
            if y > 0:
                _push(mask, visited, stack, index - width)
            if y + 1 < height:
                _push(mask, visited, stack, index + width)
        if count < min_pixels:
            continue
        regions.append(ColorRegion(
            left=area.x + min_x,
            top=area.y + min_y,
            width=max_x - min_x + 1,
            height=max_y - min_y + 1,
            pixels=count,
        ))
    regions.sort(key=lambda item: item.pixels, reverse=True)
    return regions[:max(int(max_regions), 1)]


def _push(mask: bytearray, visited: bytearray, stack: list, index: int) -> None:
    if mask[index] and not visited[index]:
        visited[index] = 1
        stack.append(index)


def summarize(value: RGB, target: RGB | None = None, *,
              tolerance: int = DEFAULT_COLOR_TOLERANCE) -> dict:
    """把单个颜色整理为结果字典（可选与目标色比较）。"""
    payload = value.to_dict()
    if target is not None:
        payload["target"] = target.to_dict()
        payload["delta"] = value.channel_delta(target)
        payload["tolerance"] = int(tolerance)
        payload["match"] = value.matches(target, tolerance)
    return payload


__all__ = [
    "DEFAULT_COLOR_TOLERANCE",
    "DEFAULT_MAX_REGIONS",
    "MAX_REGIONS_LIMIT",
    "MAX_SCAN_PIXELS",
    "NAMED_COLORS",
    "ColorError",
    "ColorRegion",
    "RGB",
    "find_color_regions",
    "parse_color",
    "pixel_at",
    "pixel_at_file",
    "region_stats",
    "summarize",
]
