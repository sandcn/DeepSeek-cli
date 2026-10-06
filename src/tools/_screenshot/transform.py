"""截图变换（区域裁剪）。

裁剪语义跨平台一致：以窗口截图的左上角为原点（(0,0) 即窗口截图左上角），
(x, y) 为区域左上角，(width, height) 为区域尺寸，区域必须完全落在图像内，
越界即报错（错误信息带图像实际尺寸，便于调用方修正）。

实现分工：
  - Windows 后端在内存 BGRA 像素上直接裁剪（省一次 PNG 解码/编码）；
  - X11 / macOS 后端由外部截图命令产出 PNG 文件，走
    :func:`apply_crop_to_png_file`（解码 → 裁剪 → 原子重编码）。

后续新增同类变换（缩放、旋转等）在此模块扩展，平台后端只依赖本模块的
公共契约，无需改动既有后端逻辑。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from . import png, png_decode
from .result import ScreenshotError

#: 裁剪参数的分隔符（逗号为主，兼容中文逗号与空白）
_SEPARATORS = (",", "，", " ", "\t")


class CropError(ScreenshotError):
    """裁剪参数非法或区域越界。

    继承 :class:`ScreenshotError`：调用方（bash_opt）既有的截图错误处理
    路径可直接复用，同时可按需单独识别裁剪类失败。
    """


@dataclass(frozen=True)
class CropRegion:
    """窗口截图的像素裁剪区域（左上角原点，含区域尺寸）。"""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y),
                            ("width", self.width), ("height", self.height)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise CropError(f"裁剪参数 {name} 必须是整数，当前: {value!r}")
        if self.x < 0 or self.y < 0:
            raise CropError(f"裁剪起始坐标不能为负: x={self.x}, y={self.y}")
        if self.width <= 0 or self.height <= 0:
            raise CropError(f"裁剪区域尺寸必须为正: width={self.width}, height={self.height}")

    @classmethod
    def parse(cls, text: str) -> "CropRegion":
        """解析 ``'x,y,width,height'`` 形式的裁剪参数。

        分隔符支持英文逗号 / 中文逗号 / 空白，如 ``'10,20,300,200'``、
        ``'10 20 300 200'``。

        Raises:
            CropError: 字段数量不为 4、含非整数、或数值范围非法。
        """
        raw = str(text or "").strip()
        if not raw:
            raise CropError("裁剪参数为空，格式应为 'x,y,width,height'（如 '0,0,800,600'）")
        normalized = raw
        for separator in _SEPARATORS:
            normalized = normalized.replace(separator, ",")
        parts = [part for part in normalized.split(",") if part != ""]
        if len(parts) != 4:
            raise CropError(
                f"裁剪参数应为 'x,y,width,height' 四个整数，当前: {text!r}"
            )
        try:
            values = [int(part) for part in parts]
        except ValueError:
            raise CropError(f"裁剪参数含非整数值: {text!r}") from None
        return cls(*values)

    def validate_against(self, image_width: int, image_height: int) -> None:
        """校验区域完全落在 ``image_width x image_height`` 的图像内。

        Raises:
            CropError: 图像尺寸非法，或区域越界。
        """
        if image_width <= 0 or image_height <= 0:
            raise CropError(f"图像尺寸非法，无法裁剪: {image_width}x{image_height}")
        if self.x + self.width > image_width or self.y + self.height > image_height:
            raise CropError(
                f"裁剪区域超出截图范围: x={self.x}, y={self.y}, "
                f"width={self.width}, height={self.height}，"
                f"需满足 x+width<={image_width} 且 y+height<={image_height}；"
                f"当前窗口截图为 {image_width}x{image_height}"
            )

    def is_full(self, image_width: int, image_height: int) -> bool:
        """区域是否恰好覆盖整幅图像（可跳过重编码）。"""
        return (
            self.x == 0 and self.y == 0
            and self.width == image_width and self.height == image_height
        )

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


def crop_rgb(rgb: bytes, image_width: int, image_height: int,
             region: CropRegion) -> bytes:
    """从 RGB 像素数据中裁出 ``region``，返回裁剪后的连续 RGB 字节。"""
    return _crop_pixels(rgb, image_width, image_height, region, 3)


def crop_bgra(bgra: bytes, image_width: int, image_height: int,
              region: CropRegion) -> bytes:
    """从 BGRA 像素数据中裁出 ``region``（保留 4 通道布局）。"""
    return _crop_pixels(bgra, image_width, image_height, region, 4)


def _crop_pixels(pixels: bytes, image_width: int, image_height: int,
                 region: CropRegion, pixel_size: int) -> bytes:
    """按像素尺寸裁剪（逐行切片复制）。"""
    region.validate_against(image_width, image_height)
    expected = image_width * image_height * pixel_size
    if len(pixels) != expected:
        raise CropError(f"像素数据长度不匹配: 期望 {expected}，实际 {len(pixels)}")
    source_stride = image_width * pixel_size
    row_bytes = region.width * pixel_size
    out = bytearray(row_bytes * region.height)
    for row in range(region.height):
        source = (region.y + row) * source_stride + region.x * pixel_size
        out[row * row_bytes:(row + 1) * row_bytes] = pixels[source:source + row_bytes]
    return bytes(out)


def apply_crop_to_png_file(path: str, region: CropRegion) -> tuple[int, int]:
    """就地把 PNG 文件裁剪为 ``region``，返回裁剪后的 ``(width, height)``。

    区域恰好覆盖整幅图像时不重编码（零开销）。重编码走临时文件 +
    ``os.replace`` 原子替换，避免中途失败留下半成品。

    Raises:
        CropError: 区域越界、文件不是可解码的 PNG。
        OSError: 文件读写失败。
    """
    try:
        image = png_decode.decode_png_file(path)
    except png_decode.PNGDecodeError as exc:
        raise CropError(f"截图产物无法解码用于裁剪: {exc}") from exc
    region.validate_against(image.width, image.height)
    if region.is_full(image.width, image.height):
        return image.width, image.height
    cropped = crop_rgb(image.rgb, image.width, image.height, region)
    data = png.encode_png_rgb(region.width, region.height, cropped)
    write_png_atomic(path, data)
    return region.width, region.height


def write_png_atomic(path: str, data: bytes) -> None:
    """同目录临时文件写入后原子替换（失败清理临时文件）。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle, temp_path = tempfile.mkstemp(suffix=".png", prefix="crop-", dir=directory)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


__all__ = [
    "CropError",
    "CropRegion",
    "apply_crop_to_png_file",
    "crop_bgra",
    "crop_rgb",
    "write_png_atomic",
]
