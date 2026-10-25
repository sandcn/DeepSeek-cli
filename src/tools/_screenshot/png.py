"""PNG 编码（零第三方依赖）。

截图能力必须在最小环境可用（Cygwin / Termux / 无 Pillow 的 Python），
因此这里用标准库 ``zlib`` + ``struct`` 手写 PNG 编码：IHDR（8-bit RGB）
+ IDAT（zlib 压缩、每行 filter=0）+ IEND，不做任何第三方库依赖。
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: PNG 颜色类型：2 = Truecolor RGB（每像素 3 字节）
COLOR_TYPE_RGB = 2
BIT_DEPTH = 8
#: 行过滤器类型：0 = None（截图多为照片/UI，交由 zlib 压缩即可）
FILTER_NONE = 0
#: 单行过滤器前缀字节（每行压缩前先写一个 filter 类型）
_FILTER_NONE_BYTE = bytes((FILTER_NONE,))

#: 单边尺寸上限：防御异常窗口尺寸导致的内存爆炸（100 万像素边长的图不可编码）
MAX_DIMENSION = 100_000


def _chunk(tag: bytes, payload: bytes) -> bytes:
    """构造一个 PNG chunk：长度 + 类型 + 数据 + CRC32。"""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _compress_rows(rows, level: int) -> bytes:
    """把逐行数据流式压入一个 zlib 流（PNG IDAT 载荷）。

    ★ 性能：不再把整幅 raw（2560x1440 RGB 约 11MB）拼成 bytes 再压缩——
    那会多出一次大块复制（实测约 3~5ms）与一块 11MB 临时内存。这里把
    「filter 字节 + 行像素」的 memoryview 分片直接喂给 ``compressobj``，
    产物与 ``zlib.compress(raw, level)`` **逐字节一致**（同一 zlib 流）。

    Args:
        rows: 可迭代的 bytes-like 分片（按写入顺序）。
        level: zlib 压缩级别。

    Returns:
        压缩后的 zlib 流（IDAT 载荷）。
    """
    compressor = zlib.compressobj(level)
    parts: list[bytes] = []
    for data in rows:
        chunk = compressor.compress(data)
        if chunk:
            parts.append(chunk)
    parts.append(compressor.flush())
    return b"".join(parts)


def encode_png_rgb(width: int, height: int, rgb: bytes, *, level: int = 6) -> bytes:
    """把连续 RGB 像素编码为 PNG 字节。

    Args:
        width: 图像宽度（像素）。
        height: 图像高度（像素）。
        rgb: 逐行连续、无行填充的 RGB 数据，长度必须为 ``width * height * 3``。
        level: zlib 压缩级别（0-9，默认 6）。

    Returns:
        完整 PNG 文件字节。

    Raises:
        ValueError: 尺寸非法或像素数据长度不匹配。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"图像尺寸非法: {width}x{height}")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(f"图像尺寸超出上限 {MAX_DIMENSION}: {width}x{height}")
    stride = width * 3
    expected = stride * height
    if len(rgb) != expected:
        raise ValueError(f"RGB 数据长度不匹配: 期望 {expected}，实际 {len(rgb)}")

    view = memoryview(rgb)

    def _rows():
        for y in range(height):
            # filter 字节与像素行分别喂入，避免拼接整幅 raw 的中间副本
            yield _FILTER_NONE_BYTE
            yield view[y * stride:(y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", width, height, BIT_DEPTH, COLOR_TYPE_RGB, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", _compress_rows(_rows(), level))
        + _chunk(b"IEND", b"")
    )


def encode_png_bgra(width: int, height: int, bgra: bytes, *, level: int = 6) -> bytes:
    """把 BGRA 像素（Windows DIB 原生布局）编码为 PNG。

    仅取 B/G/R 三通道（截图产物的 alpha 通道在 DIB 中通常未使用/全 0，
    保留会导致图像全透明），丢弃 A 通道。

    ★ 性能：RGB 通道重排用三次步长切片赋值（C 级），不再对结果做
    ``bytes(rgb)`` 全量复制——直接把 bytearray 交给
    :func:`encode_png_rgb`（内部用 memoryview 零拷贝读取）。
    """
    expected = width * height * 4
    if len(bgra) != expected:
        raise ValueError(f"BGRA 数据长度不匹配: 期望 {expected}，实际 {len(bgra)}")
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    return encode_png_rgb(width, height, rgb, level=level)


def looks_blank(data: bytes, pixel_size: int = 3, *, samples: int = 2000) -> bool:
    """判断像素数据是否「空白」（采样点 RGB 完全一致，如纯黑/纯白占位图）。

    用于识别 ``PrintWindow`` 对硬件加速窗口返回的全黑占位图，以及各平台
    截图命令产出的空白结果；命中时调用方走回退路径（屏幕拷贝等）。

    Args:
        data: 像素字节流（每像素 ``pixel_size`` 字节，通道顺序不限）。
        pixel_size: 每像素字节数（RGB=3，BGRA=4）。
        samples: 最多采样的像素数（避免大图逐像素扫描）。

    Returns:
        True 表示采样点前三个通道完全一致（视为空白）。
    """
    if pixel_size <= 0:
        raise ValueError(f"pixel_size 必须为正数: {pixel_size}")
    pixel_count = len(data) // pixel_size
    if pixel_count <= 0:
        return True
    step = max(1, pixel_count // max(samples, 1))
    first = data[0:3]
    for index in range(0, pixel_count, step):
        offset = index * pixel_size
        if data[offset:offset + 3] != first:
            return False
    # 末尾像素单独比较（步长未必覆盖到最后一像素，如全黑图末尾才绘制内容）
    tail = (pixel_count - 1) * pixel_size
    return data[tail:tail + 3] == first


def is_blank_rgb(rgb: bytes, *, samples: int = 2000) -> bool:
    """判断 RGB 像素数据是否空白（``looks_blank`` 的 RGB 便捷封装）。"""
    return looks_blank(rgb, 3, samples=samples)


def read_png_size(path: str) -> tuple[int, int]:
    """读取 PNG 文件头的宽高（IHDR）。

    Args:
        path: PNG 文件路径。

    Returns:
        ``(width, height)``。

    Raises:
        ValueError: 文件不是合法 PNG（签名或 IHDR 异常）。
    """
    with open(path, "rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError(f"文件不是合法 PNG: {path}")
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def read_png_size_or(path: str, fallback: tuple[int, int]) -> tuple[int, int]:
    """读取产物尺寸，失败时回退窗口几何尺寸（供各平台后端统一使用）。"""
    try:
        return read_png_size(path)
    except (OSError, ValueError):
        return int(fallback[0]), int(fallback[1])
