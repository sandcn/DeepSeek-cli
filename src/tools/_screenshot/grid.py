"""截图坐标网格（在截图上叠加参考线，便于按像素定位）。

按截图像素给出输入坐标时，肉眼在纯画面上估位容易偏差；本模块在截图产物上
叠加等距参考线，模型读图后可直接数格子推算出目标像素坐标：

  - **主线**：每 ``step`` 像素一条（默认按画面尺寸自动取值，约 10 条）；
  - **次线**：每 ``step / 2`` 像素一条（仅当 ``step`` 足够大时绘制）。

线为半透明混合（不遮挡界面内容），以图像左上角为原点，与
``op=screenshot`` / 输入注入的坐标系一致。

实现分工（与裁剪一致的思路）：
  - Windows 后端在内存 BGRA 像素上直接绘制（省一次 PNG 解码/编码）；
  - X11 / macOS 后端对截图命令产出的 PNG 走
    :func:`paint_grid_on_png_file`（解码 → 绘制 → 原子重编码）。
"""

from __future__ import annotations

import logging

from . import png, png_decode
from .result import ScreenshotError
from .transform import write_png_atomic

logger = logging.getLogger(__name__)

#: 自动步长的目标线数（画面短边被切成约这么多格）
AUTO_TARGET_LINES = 10
#: 自动步长下限 / 上限（像素）
MIN_STEP = 4
MAX_STEP = 1000
#: 手动指定步长的合法范围
MIN_REQUESTED_STEP = 2
#: 次线仅在主步长不小于该值时绘制（否则画面会被线占满）
MIN_STEP_FOR_MINOR = 32

#: 参考线颜色（RGB）与不透明度
MAJOR_COLOR = (255, 64, 64)
MAJOR_ALPHA = 0.55
MINOR_ALPHA_RATIO = 0.4


def auto_step(width: int, height: int) -> int:
    """按画面尺寸给出参考线步长（让短边约分 :data:`AUTO_TARGET_LINES` 格）。"""
    short = max(min(int(width), int(height)), 1)
    raw = short / AUTO_TARGET_LINES
    rounded = int(round(raw / 10.0)) * 10
    step = max(MIN_STEP, min(MAX_STEP, rounded or 10))
    return step


def resolve_step(width: int, height: int, requested: int = 0) -> int:
    """把手动步长（``0`` 表示自动）归一化为可用值。

    Raises:
        ScreenshotError: 手动步长小于 2（无法构成网格）。
    """
    value = int(requested or 0)
    if value <= 0:
        return auto_step(width, height)
    if value < MIN_REQUESTED_STEP:
        raise ScreenshotError(
            f"网格步长过小: {value}（需 >= {MIN_REQUESTED_STEP} 像素，或传 0 自动选择）"
        )
    return min(value, MAX_STEP)


def draw_grid_rgb(pixels: bytes, width: int, height: int, step: int,
                  *, color: tuple[int, int, int] = MAJOR_COLOR,
                  alpha: float = MAJOR_ALPHA) -> bytes:
    """在 RGB 像素上叠加参考线，返回新的 RGB 字节串。"""
    return _draw(pixels, width, height, step, 3, color, alpha)


def draw_grid_bgra(pixels: bytes, width: int, height: int, step: int,
                   *, color: tuple[int, int, int] = MAJOR_COLOR,
                   alpha: float = MAJOR_ALPHA) -> bytes:
    """在 BGRA 像素上叠加参考线（保留 alpha 通道），返回新的字节串。"""
    return _draw(pixels, width, height, step, 4, color, alpha)


def _draw(pixels: bytes, width: int, height: int, step: int, pixel_size: int,
          color: tuple[int, int, int], alpha: float) -> bytes:
    """按像素布局绘制主/次参考线。"""
    _validate_pixels(pixels, width, height, pixel_size)
    step = resolve_step(width, height, step)
    buffer = bytearray(pixels)
    # BGRA 的通道顺序是 B,G,R,A；RGB 是 R,G,B
    channel_order = (2, 1, 0) if pixel_size == 4 else (0, 1, 2)
    _paint_major(buffer, width, height, pixel_size, channel_order, step, color, alpha)
    if step >= MIN_STEP_FOR_MINOR and step // 2 >= MIN_REQUESTED_STEP:
        _paint_minor(buffer, width, height, pixel_size, channel_order, step,
                     color, alpha * MINOR_ALPHA_RATIO)
    return bytes(buffer)


def _paint_major(buffer: bytearray, width: int, height: int, pixel_size: int,
                 order: tuple[int, int, int], step: int,
                 color: tuple[int, int, int], alpha: float) -> None:
    for x in range(0, width, step):
        _paint_column(buffer, width, height, pixel_size, order, x, color, alpha)
    for y in range(0, height, step):
        _paint_row(buffer, width, pixel_size, order, y, color, alpha)


def _paint_minor(buffer: bytearray, width: int, height: int, pixel_size: int,
                 order: tuple[int, int, int], step: int,
                 color: tuple[int, int, int], alpha: float) -> None:
    half = step // 2
    for x in range(half, width, step):
        _paint_column(buffer, width, height, pixel_size, order, x, color, alpha)
    for y in range(half, height, step):
        _paint_row(buffer, width, pixel_size, order, y, color, alpha)


def _paint_column(buffer: bytearray, width: int, height: int, pixel_size: int,
                  order: tuple[int, int, int], x: int,
                  color: tuple[int, int, int], alpha: float) -> None:
    for y in range(height):
        _blend(buffer, (y * width + x) * pixel_size, pixel_size, order, color, alpha)


def _paint_row(buffer: bytearray, width: int, pixel_size: int,
               order: tuple[int, int, int], y: int,
               color: tuple[int, int, int], alpha: float) -> None:
    base = y * width * pixel_size
    for x in range(width):
        _blend(buffer, base + x * pixel_size, pixel_size, order, color, alpha)


def _blend(buffer: bytearray, offset: int, pixel_size: int,
           order: tuple[int, int, int], color: tuple[int, int, int],
           alpha: float) -> None:
    for channel in range(3):
        index = offset + order[channel]
        buffer[index] = _mix(buffer[index], color[channel], alpha)


def _mix(current: int, target: int, alpha: float) -> int:
    value = current * (1.0 - alpha) + target * alpha
    return 0 if value < 0 else (255 if value > 255 else int(value + 0.5))


def _validate_pixels(pixels: bytes, width: int, height: int, pixel_size: int) -> None:
    if width <= 0 or height <= 0:
        raise ScreenshotError(f"图像尺寸非法，无法绘制网格: {width}x{height}")
    expected = width * height * pixel_size
    if len(pixels) != expected:
        raise ScreenshotError(
            f"像素数据长度不匹配: 期望 {expected}，实际 {len(pixels)}"
        )


def paint_grid_on_png_file(path: str, step: int = 0) -> tuple[int, int, int]:
    """就地给 PNG 文件叠加参考线，返回 ``(width, height, step)``。

    Raises:
        ScreenshotError: 文件无法解码，或写入失败。
    """
    try:
        image = png_decode.decode_png_file(path)
    except png_decode.PNGDecodeError as exc:
        raise ScreenshotError(f"截图产物无法解码用于绘制网格: {exc}") from exc
    resolved = resolve_step(image.width, image.height, step)
    painted = draw_grid_rgb(image.rgb, image.width, image.height, resolved)
    data = png.encode_png_rgb(image.width, image.height, painted)
    write_png_atomic(path, data)
    return image.width, image.height, resolved


__all__ = [
    "AUTO_TARGET_LINES",
    "MAX_STEP",
    "MIN_STEP",
    "auto_step",
    "draw_grid_bgra",
    "draw_grid_rgb",
    "paint_grid_on_png_file",
    "resolve_step",
    "write_png_atomic",
]
