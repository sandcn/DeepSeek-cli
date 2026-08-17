"""二维码图片 → 终端渲染（无第三方图像库依赖）。

微信 get_bot_qrcode 返回的 ``qrcode_img_content`` 是微信官方生成的登录
二维码（PNG/SVG/URL）。直接解码官方图片渲染，可保证扫码内容与官方
完全一致；若用 ``qrcode`` 字段重新编码生成二维码，微信可能无法识别，
导致"扫码登不上"。

支持形态：
- ``data:image/png;base64,...`` → 手写 PNG 解码（zlib 标准库）
- ``<svg ...>`` / ``data:image/svg+xml;base64,...`` → 正则解析矩形
- ``http(s)://...`` → 由调用方下载后走 PNG 解码
- 安装了 Pillow 时优先使用（更健壮）；未安装走标准库回退
"""

from __future__ import annotations

import base64
import io
import math
import re
import struct
import zlib
from typing import List, Tuple

try:  # 可选依赖：有 Pillow 用 Pillow，否则标准库回退
    from PIL import Image  # type: ignore
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False


# ═══════════════════════════════════════════════════════
# PNG 解码
# ═══════════════════════════════════════════════════════

def _png_paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png_matrix_pil(data: bytes) -> List[List[bool]]:
    """Pillow 解码 PNG → 黑白矩阵（True=深色）。"""
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    px = list(img.getdata())
    return [[px[y * w + x] < 128 for x in range(w)] for y in range(h)]


def _decode_png_matrix_zlib(data: bytes) -> List[List[bool]]:
    """标准库（struct+zlib）解码 PNG → 黑白矩阵。

    支持常见二维码 PNG：位深 8、颜色类型 0(灰度)/2(RGB)/3(调色板)/
    4(灰度+alpha)/6(RGB+alpha)、无 interlace。
    """
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG 签名不匹配")

    pos = 8
    width = height = bit_depth = color_type = None
    idat = b""
    palette: List[Tuple[int, int, int]] = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR" and length >= 10:
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk) - 2, 3)]
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length

    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError("PNG 缺少 IHDR")
    if bit_depth != 8:
        raise ValueError(f"不支持的 PNG 位深: {bit_depth}")
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if bpp is None:
        raise ValueError(f"不支持的 PNG 颜色类型: {color_type}")

    raw = zlib.decompress(idat)
    stride = width * bpp
    rows_data: List[bytes] = []
    prev = bytearray(stride)
    pos2 = 0
    for _y in range(height):
        if pos2 >= len(raw):
            raise ValueError("PNG 数据不完整")
        ftype = raw[pos2]
        pos2 += 1
        line = bytearray(raw[pos2:pos2 + stride])
        pos2 += stride
        if ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + (a + prev[i]) // 2) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _png_paeth(a, prev[i], c)) & 0xFF
        rows_data.append(bytes(line))
        prev = line

    matrix: List[List[bool]] = []
    for y in range(height):
        row = []
        row_bytes = rows_data[y]
        for x in range(width):
            off = x * bpp
            if color_type == 0:
                v = row_bytes[off]
            elif color_type == 3:
                idx = row_bytes[off]
                r, g, b = palette[idx] if idx < len(palette) else (255, 255, 255)
                v = int(0.299 * r + 0.587 * g + 0.114 * b)
            else:
                r, g, b = row_bytes[off], row_bytes[off + 1], row_bytes[off + 2]
                v = int(0.299 * r + 0.587 * g + 0.114 * b)
            row.append(v < 128)
        matrix.append(row)
    return matrix


def decode_png_matrix(data: bytes) -> List[List[bool]]:
    """解码 PNG 字节为黑白矩阵（True=深色模块）。"""
    if _HAS_PIL:
        try:
            return _decode_png_matrix_pil(data)
        except Exception as exc:
            raise ValueError(f"PNG 解码失败: {exc}") from exc
    return _decode_png_matrix_zlib(data)


# ═══════════════════════════════════════════════════════
# SVG 解析
# ═══════════════════════════════════════════════════════

_DARK_FILL_RE = re.compile(
    r"^(black|#000|#000000|#00000000|#111|#111111|#222|#222222"
    r"|#333|#333333|#444|#444444|#1a1a1a|#191919|#212121)$",
    re.IGNORECASE,
)


def _is_dark_fill(fill: str) -> bool:
    fill = (fill or "").strip().lower()
    if _DARK_FILL_RE.match(fill):
        return True
    m = re.match(r"^#([0-9a-f]{6})$", fill)
    if m:
        h = m.group(1)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) < 128
    return False


def _svg_size(svg: str) -> Tuple[int, int]:
    m = re.search(r'viewBox\s*=\s*"[^"]*\s+([\d.]+)\s+([\d.]+)"', svg)
    if m:
        return int(float(m.group(1))), int(float(m.group(2)))
    m = re.search(r'width\s*=\s*"([\d.]+)"[^>]*height\s*=\s*"([\d.]+)"', svg)
    if m:
        return int(float(m.group(1))), int(float(m.group(2)))
    raise ValueError("无法解析 SVG 尺寸")


def parse_svg_matrix(svg: str) -> List[List[bool]]:
    """解析 SVG 二维码为黑白矩阵（True=深色模块）。"""
    w, h = _svg_size(svg)
    matrix = [[False] * w for _ in range(h)]
    for tag in re.finditer(r"<rect\b[^>]*>", svg):
        attrs = dict(re.findall(r'([a-zA-Z_-]+)\s*=\s*"([^"]*)"', tag.group(0)))
        if not _is_dark_fill(attrs.get("fill", "black")):
            continue
        x = int(float(attrs.get("x", 0)))
        y = int(float(attrs.get("y", 0)))
        rw = max(1, int(float(attrs.get("width", 1))))
        rh = max(1, int(float(attrs.get("height", 1))))
        for yy in range(y, min(y + rh, h)):
            for xx in range(x, min(x + rw, w)):
                matrix[yy][xx] = True
    return matrix


# ═══════════════════════════════════════════════════════
# 矩阵 → 终端文本
# ═══════════════════════════════════════════════════════

def matrix_to_ascii(matrix: List[List[bool]], max_width: int = 45,
                    invert: bool = True) -> List[str]:
    """黑白矩阵降采样为终端文本行。

    每个输出字符对应一个采样块（宽高比 1:2 的方块字符 ██）。
    invert=True 时反色（黑底白块），适配深色终端。

    Args:
        matrix: True=深色（二维码黑模块）
        max_width: 输出最大字符宽度（约为二维码模块数）
        invert: 是否反色显示

    Returns:
        终端文本行列表
    """
    if not matrix or not matrix[0]:
        return []
    h = len(matrix)
    w = len(matrix[0])
    block = max(1, math.ceil(w / max_width))
    rows = []
    for y in range(0, h, block):
        line = ""
        for x in range(0, w, block):
            total = 0
            cnt = 0
            for yy in range(y, min(y + block, h)):
                for xx in range(x, min(x + block, w)):
                    if matrix[yy][xx]:
                        total += 1
                    cnt += 1
            dark = total / cnt > 0.5
            if invert:
                line += "  " if dark else "██"
            else:
                line += "██" if dark else "  "
        rows.append(line)
    return rows


# ═══════════════════════════════════════════════════════
# 综合入口（纯解析，不做网络请求）
# ═══════════════════════════════════════════════════════

def render_img_content(img_content: str,
                       max_width: int | None = None) -> Tuple[List[str], str]:
    """解析 qrcode_img_content，返回 (终端文本行, 附加提示)。

    微信官方 get_bot_qrcode 返回的 qrcode_img_content 有两种形态：
    1. 位图/SVG（data:image/... 或 <svg>）→ 解码后按图片矩阵渲染
    2. http(s) URL（如 liteapp.weixin.qq.com 登录页）→ 返回 (None, url)，
       由调用方用 qrcode 库**把该 URL 编码成二维码**——登录二维码的
       内容就是这个 URL，扫码后微信会识别并触发授权（qrcode 字段只是
       内部 hash，不能作为二维码内容，用它生成二维码会导致扫码无响应）。

    Args:
        img_content: get_bot_qrcode 返回的 qrcode_img_content 字段
        max_width: 输出行最大字符宽度（None 不限制）。终端过窄时降采样
            二维码模块，避免自动换行导致错位。

    Returns:
        (lines, hint)：
        - lines 非空 → 已按官方图片渲染完成
        - lines 为空且 hint 为 http URL → 需用 qrcode 库编码 hint 作为二维码
        - 其他 → ValueError
    """
    content = (img_content or "").strip()
    if not content:
        raise ValueError("无二维码图片内容")

    # data:image/svg+xml;base64,xxx
    if content.startswith("data:image/svg"):
        _, _, b64 = content.partition(",")
        svg = base64.b64decode(b64).decode(errors="replace")
        return matrix_to_ascii(parse_svg_matrix(svg), max_width=_max_modules(max_width)), ""

    # 直接 SVG 文本
    if content.startswith("<svg") or content.startswith("<?xml"):
        return matrix_to_ascii(parse_svg_matrix(content), max_width=_max_modules(max_width)), ""

    # data:image/png;base64,xxx（或其他位图）
    if content.startswith("data:image/"):
        _, _, b64 = content.partition(",")
        raw = base64.b64decode(b64)
        return matrix_to_ascii(decode_png_matrix(raw), max_width=_max_modules(max_width)), ""

    # http(s) URL：二维码内容就是该 URL（如 liteapp 登录页），
    # 由调用方用 qrcode 库编码为二维码（不能下载 HTML 当图片）。
    if content.startswith("http://") or content.startswith("https://"):
        return [], content

    # 其他：尝试作为 base64 位图解码
    try:
        raw = base64.b64decode(content)
        return matrix_to_ascii(decode_png_matrix(raw), max_width=_max_modules(max_width)), ""
    except Exception as exc:
        raise ValueError(f"无法识别的二维码内容: {content[:120]}") from exc


def _max_modules(max_width: int | None) -> int:
    """终端字符宽度 → 二维码输出模块数上限（每模块 2 字符）。"""
    if max_width is None or max_width <= 0:
        return 45
    return max(1, max_width // 2)


def render_bytes(raw: bytes) -> List[str]:
    """直接渲染图片字节（PNG/JPEG 等）为终端文本行。"""
    return matrix_to_ascii(decode_png_matrix(raw))


def qrcode_png_bytes(content: str, border: int = 4) -> bytes:
    """用 qrcode 库生成二维码 PNG 字节（标准黑白，供扫码）。"""
    import io

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M,
                       box_size=8, border=border)
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
