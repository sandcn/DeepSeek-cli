"""PNG 解码（零第三方依赖）——截图裁剪的读回通道。

Windows 后端在内存像素上直接裁剪；X11 / macOS 后端由外部截图命令产出
PNG 文件，裁剪需先解码再重编码。为保证最小环境可用（Cygwin / Termux /
无 Pillow 的 Python），这里用标准库 ``zlib`` + ``struct`` 手写解码，覆盖：

  - 色彩类型：0 灰度 / 2 真彩 / 3 调色板 / 4 灰度+alpha / 6 真彩+alpha；
  - 位深：1 / 2 / 4 / 8 / 16（16 位取高字节）；
  - 隔行：非隔行与 Adam7（7 个 pass）；
  - 行过滤器：None / Sub / Up / Average / Paeth 全部 5 种。

输出统一为 8-bit RGB：alpha 通道按「丢弃」策略处理（与
``png.encode_png_bgra`` 的既有约定一致），tRNS 透明色信息同样不参与。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .png import MAX_DIMENSION, PNG_SIGNATURE

#: 各色彩类型的通道数
_CHANNELS: dict[int, int] = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

#: 合法「色彩类型 → 位深」组合（PNG 规范表 11.1）
_VALID_BIT_DEPTHS: dict[int, tuple[int, ...]] = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}

#: Adam7 隔行的 7 个 pass：(x0, y0, dx, dy)
_ADAM7_PASSES: tuple[tuple[int, int, int, int], ...] = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)

#: 行过滤器类型数量（0-4）
_FILTER_TYPES = 5

#: 解码像素数上限（RGB 缓冲 width*height*3，防御异常大图占满内存）
MAX_PIXELS = 64_000_000


class PNGDecodeError(ValueError):
    """PNG 解码失败（签名 / 结构 / 过滤器 / 尺寸异常）。"""


@dataclass(frozen=True)
class DecodedImage:
    """解码后的图像（统一 8-bit RGB）。"""

    width: int
    height: int
    rgb: bytes


@dataclass(frozen=True)
class _Header:
    """IHDR 内容。"""

    width: int
    height: int
    bit_depth: int
    color_type: int
    compression: int
    filter_method: int
    interlace: int


def decode_png_file(path: str) -> DecodedImage:
    """读取并解码 PNG 文件为 8-bit RGB 图像。

    Raises:
        OSError: 文件不可读。
        PNGDecodeError: 内容不是合法 PNG 或超出解码限制。
    """
    with open(path, "rb") as handle:
        return decode_png(handle.read())


def decode_png(data: bytes) -> DecodedImage:
    """解码 PNG 字节为 8-bit RGB 图像。

    Raises:
        PNGDecodeError: 数据不是合法 PNG、结构 / 过滤器异常、或尺寸超限。
    """
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        raise PNGDecodeError("不是合法的 PNG 数据（签名不匹配）")

    header: _Header | None = None
    palette: list[tuple[int, int, int]] | None = None
    idat = bytearray()
    offset = 8
    total = len(data)
    while offset + 8 <= total:
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > total:
            raise PNGDecodeError(f"PNG chunk 数据不完整: {tag!r}")
        payload = data[start:end]
        if tag == b"IHDR":
            header = _parse_ihdr(payload)
        elif tag == b"PLTE":
            palette = _parse_palette(payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        offset = end + 4

    if header is None:
        raise PNGDecodeError("PNG 缺少 IHDR 块")
    if not idat:
        raise PNGDecodeError("PNG 缺少 IDAT 图像数据")
    if header.color_type == 3 and not palette:
        raise PNGDecodeError("调色板图像缺少 PLTE 块")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PNGDecodeError(f"IDAT 数据解压失败: {exc}") from exc

    out = bytearray(header.width * header.height * 3)
    if header.interlace == 0:
        _decode_pass(
            raw, 0, header, palette, out,
            x0=0, y0=0, dx=1, dy=1,
            pass_width=header.width, pass_height=header.height,
        )
    else:
        cursor = 0
        for x0, y0, dx, dy in _ADAM7_PASSES:
            pass_width = _pass_size(header.width, x0, dx)
            pass_height = _pass_size(header.height, y0, dy)
            if pass_width == 0 or pass_height == 0:
                continue
            cursor = _decode_pass(
                raw, cursor, header, palette, out,
                x0=x0, y0=y0, dx=dx, dy=dy,
                pass_width=pass_width, pass_height=pass_height,
            )
    return DecodedImage(header.width, header.height, bytes(out))


def _parse_ihdr(payload: bytes) -> _Header:
    """解析并校验 IHDR 块。"""
    if len(payload) != 13:
        raise PNGDecodeError(f"IHDR 长度非法: {len(payload)}（应为 13）")
    (width, height, bit_depth, color_type,
     compression, filter_method, interlace) = struct.unpack(">IIBBBBB", payload)
    if width <= 0 or height <= 0:
        raise PNGDecodeError(f"图像尺寸非法: {width}x{height}")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise PNGDecodeError(f"图像尺寸超出上限 {MAX_DIMENSION}: {width}x{height}")
    if width * height > MAX_PIXELS:
        raise PNGDecodeError(f"图像像素数超出解码上限 {MAX_PIXELS}: {width}x{height}")
    if color_type not in _CHANNELS:
        raise PNGDecodeError(f"不支持的色彩类型: {color_type}")
    if bit_depth not in _VALID_BIT_DEPTHS[color_type]:
        raise PNGDecodeError(f"色彩类型 {color_type} 不支持位深 {bit_depth}")
    if compression != 0:
        raise PNGDecodeError(f"不支持的压缩方法: {compression}")
    if filter_method != 0:
        raise PNGDecodeError(f"不支持的过滤方法: {filter_method}")
    if interlace not in (0, 1):
        raise PNGDecodeError(f"不支持的隔行方式: {interlace}")
    return _Header(width, height, bit_depth, color_type,
                   compression, filter_method, interlace)


def _parse_palette(payload: bytes) -> list[tuple[int, int, int]]:
    """解析 PLTE 块为 RGB 三元组列表。"""
    if not payload or len(payload) % 3 != 0:
        raise PNGDecodeError(f"PLTE 长度非法: {len(payload)}")
    if len(payload) // 3 > 256:
        raise PNGDecodeError(f"PLTE 条目过多: {len(payload) // 3}")
    return [
        (payload[i], payload[i + 1], payload[i + 2])
        for i in range(0, len(payload), 3)
    ]


def _pass_size(total: int, start: int, step: int) -> int:
    """Adam7 单个 pass 在该维度上的像素数（可能为 0）。"""
    if total <= start:
        return 0
    return (total - start + step - 1) // step


def _decode_pass(raw: bytes, offset: int, header: _Header,
                 palette: list[tuple[int, int, int]] | None, out: bytearray, *,
                 x0: int, y0: int, dx: int, dy: int,
                 pass_width: int, pass_height: int) -> int:
    """解码一个 pass，返回解压数据中消耗到的偏移。"""
    channels = _CHANNELS[header.color_type]
    stride = (pass_width * channels * header.bit_depth + 7) // 8
    bpp = max(1, (channels * header.bit_depth + 7) // 8)
    previous = bytes(stride)
    for row in range(pass_height):
        if offset + 1 + stride > len(raw):
            raise PNGDecodeError(
                f"IDAT 数据不足（pass {pass_width}x{pass_height} 第 {row} 行）"
            )
        filter_type = raw[offset]
        offset += 1
        if filter_type >= _FILTER_TYPES:
            raise PNGDecodeError(f"未知行过滤器类型: {filter_type}")
        line = _unfilter(raw[offset:offset + stride], previous, filter_type, bpp)
        offset += stride
        previous = line
        rgb_row = _row_to_rgb(line, pass_width, header.bit_depth,
                              header.color_type, palette)
        _write_row(out, header.width, rgb_row, y0 + row * dy, x0, dx, pass_width)
    return offset


def _unfilter(chunk: bytes, previous: bytes, filter_type: int, bpp: int) -> bytes:
    """按 PNG 规范重建一行原始样本字节（in-place 于副本上）。"""
    if filter_type == 0:
        return bytes(chunk)
    line = bytearray(chunk)
    size = len(line)
    if filter_type == 1:  # Sub
        for i in range(bpp, size):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif filter_type == 2:  # Up
        for i in range(size):
            line[i] = (line[i] + previous[i]) & 0xFF
    elif filter_type == 3:  # Average
        for i in range(size):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
    else:  # Paeth
        for i in range(size):
            left = line[i - bpp] if i >= bpp else 0
            up_left = previous[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + _paeth(left, previous[i], up_left)) & 0xFF
    return bytes(line)


def _paeth(left: int, up: int, up_left: int) -> int:
    """PNG Paeth 预测器。"""
    estimate = left + up - up_left
    dist_left = abs(estimate - left)
    dist_up = abs(estimate - up)
    dist_up_left = abs(estimate - up_left)
    if dist_left <= dist_up and dist_left <= dist_up_left:
        return left
    if dist_up <= dist_up_left:
        return up
    return up_left


def _row_to_rgb(line: bytes, count: int, bit_depth: int,
                color_type: int, palette: list[tuple[int, int, int]] | None) -> bytes:
    """把一行原始样本转换为 RGB 字节（8 位常见组合走切片快路径）。"""
    if bit_depth == 8:
        if color_type == 2:  # RGB
            return bytes(line[:count * 3])
        if color_type == 6:  # RGBA
            out = bytearray(count * 3)
            out[0::3] = line[0:count * 4:4]
            out[1::3] = line[1:count * 4:4]
            out[2::3] = line[2:count * 4:4]
            return bytes(out)
        if color_type == 0:  # 灰度
            gray = line[:count]
            out = bytearray(count * 3)
            out[0::3] = gray
            out[1::3] = gray
            out[2::3] = gray
            return bytes(out)
        if color_type == 4:  # 灰度 + alpha
            gray = line[0:count * 2:2]
            out = bytearray(count * 3)
            out[0::3] = gray
            out[1::3] = gray
            out[2::3] = gray
            return bytes(out)
        if color_type == 3:  # 调色板
            out = bytearray(count * 3)
            for index in range(count):
                r, g, b = _palette_entry(palette, line[index])
                out[index * 3] = r
                out[index * 3 + 1] = g
                out[index * 3 + 2] = b
            return bytes(out)
    return _generic_row(line, count, bit_depth, color_type, palette)


def _generic_row(line: bytes, count: int, bit_depth: int,
                 color_type: int, palette: list[tuple[int, int, int]] | None) -> bytes:
    """位深 1/2/4/16 与未走快路径组合的通用转换。"""
    channels = _CHANNELS[color_type]
    out = bytearray(count * 3)
    if bit_depth < 8:
        per_byte = 8 // bit_depth
        mask = (1 << bit_depth) - 1
        scale = 255 // mask
        for index in range(count):
            byte = line[index // per_byte]
            shift = 8 - bit_depth * (index % per_byte + 1)
            value = (byte >> shift) & mask
            if color_type == 3:
                r, g, b = _palette_entry(palette, value)
            else:
                r = g = b = value * scale
            out[index * 3] = r
            out[index * 3 + 1] = g
            out[index * 3 + 2] = b
        return bytes(out)

    for index in range(count):
        base = index * channels * (bit_depth // 8)
        if bit_depth == 16:  # 高字节近似为 8 位
            if color_type == 0:
                r = g = b = line[base]
            elif color_type == 2:
                r, g, b = line[base], line[base + 2], line[base + 4]
            elif color_type == 3:
                r, g, b = _palette_entry(palette, line[base])
            elif color_type == 4:
                r = g = b = line[base]
            else:
                r, g, b = line[base], line[base + 2], line[base + 4]
        else:  # 8 位（快路径未覆盖的兜底）
            if color_type == 0:
                r = g = b = line[base]
            elif color_type == 2:
                r, g, b = line[base], line[base + 1], line[base + 2]
            elif color_type == 3:
                r, g, b = _palette_entry(palette, line[base])
            elif color_type == 4:
                r = g = b = line[base]
            else:
                r, g, b = line[base], line[base + 1], line[base + 2]
        out[index * 3] = r
        out[index * 3 + 1] = g
        out[index * 3 + 2] = b
    return bytes(out)


def _palette_entry(palette: list[tuple[int, int, int]] | None,
                   index: int) -> tuple[int, int, int]:
    """取调色板条目，越界 / 缺块时报错。"""
    if palette is None:
        raise PNGDecodeError("调色板图像缺少 PLTE 块")
    if index >= len(palette):
        raise PNGDecodeError(f"调色板索引越界: {index}（调色板大小 {len(palette)}）")
    return palette[index]


def _write_row(out: bytearray, image_width: int, rgb_row: bytes,
               y: int, x0: int, dx: int, count: int) -> None:
    """把一行 RGB 写入目标网格（按 pass 步长散布；整行时直接切片）。"""
    base = y * image_width * 3
    if x0 == 0 and dx == 1 and count == image_width:
        out[base:base + count * 3] = rgb_row
        return
    for index in range(count):
        src = index * 3
        dst = base + (x0 + index * dx) * 3
        out[dst:dst + 3] = rgb_row[src:src + 3]


__all__ = [
    "MAX_PIXELS",
    "DecodedImage",
    "PNGDecodeError",
    "decode_png",
    "decode_png_file",
]
