"""trace 多模态图片 → 终端半块彩色缩略图渲染（2026-08-22）。

消息 content 里的图片 block（``image_url`` data URI / ``image`` base64）在
轨迹检查器中渲染为「半块（▀）真彩缩略图」行（``list[list[StyledRun]]``），
替代原来 ``_content_str`` 折叠出的超长 ``[图片: data:image/...;base64,...]``。

设计要点（对齐 trace 重渲染/每帧重建的性能约束）：
- **解析与解码分离**：``parse_image_blocks`` 只解析出图片元信息（media_type/
  base64/近似字节数），**不解码**（记录构建热路径零图片解码）；解码与渲染只在
  检查器渲染时进行，且按 ``sha256(base64)`` + 尺寸缓存——同图跨帧零重复解码。
- **降级安全**：Pillow 缺失 / 解码失败 / 非图片 → 返回占位行或空，不中断
  台账/检查器构建。
- **终端能力自适应**：真彩或 256 色按 ``detect_truecolor()`` 自动选择
  （Style.fg/bg 仅接受 int|TrueColor，不传 Color256 值对象）。
"""

from __future__ import annotations

import base64
import hashlib
import re
from io import BytesIO

#: ``data:image/<type>;base64,<b64>`` 匹配
_IMAGE_B64_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.S)
#: 半块字符（上=fg，下=bg）
_HALF = "\u2580"
#: 缩略图单元上限（列=右栏宽自适应，行=17 个半块）
_THUMB_ROWS = 16
#: 渲染/解码缓存上限（超限清空重建——miss 仅多一次渲染）
_ROW_CACHE_MAX = 64
_rows_cache: dict = {}


def _b64_approx_len(b64: str) -> int:
    """base64 字符串近似解码字节数（免实际解码；热路径用）。"""
    pad = 2 if b64.endswith("==") else (1 if b64.endswith("=") else 0)
    return max(0, (len(b64) * 3 // 4) - pad)


def _is_image_block(block) -> bool:
    """判断 content block 是否为图片块（image_url / image）。"""
    if not isinstance(block, dict):
        return False
    btype = block.get("type", "")
    if btype == "image_url":
        return True
    if btype == "image":
        return True
    return False


def _image_url_payload(block: dict):
    """从 image_url block 提取 (media_type, base64)。非 data URI 返回 None。"""
    cont = block.get("image_url") or {}
    url = cont.get("url", "") if isinstance(cont, dict) else ""
    if not isinstance(url, str):
        return None
    m = _IMAGE_B64_RE.match(url)
    if not m:
        return None
    return m.group(1), m.group(2)


def _image_block_payload(block: dict):
    """从 image block（Anthropic 原生 / 通用）提取 (media_type, base64)。"""
    cont = block.get("source") or block
    if not isinstance(cont, dict):
        return None
    # Anthropic image block: {"type":"base64","media_type":..., "data":...}
    if cont.get("type") == "base64":
        mt = cont.get("media_type") or "image/png"
        data = cont.get("data")
        if isinstance(data, str):
            return mt, data
        return None
    # 兜底 image block 直带 url/data
    url = cont.get("url")
    if isinstance(url, str):
        m = _IMAGE_B64_RE.match(url)
        if m:
            return m.group(1), m.group(2)
    return None


def parse_image_blocks(content) -> list[dict]:
    """从 content（str/list）提取图片 block 元信息列表（不解码）。

    每个元素 ``{media_type, b64, sha, approx_bytes}``；非图片块忽略。
    content 非 list（纯文本）返回空列表。
    """
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for c in content:
        if not _is_image_block(c):
            continue
        payload = _image_url_payload(c) if c.get("type") == "image_url" else _image_block_payload(c)
        if payload is None:
            continue
        media_type, b64 = payload
        if not b64:
            continue
        out.append({
            "media_type": media_type,
            "b64": b64,
            "sha": hashlib.sha256(b64.encode("ascii")).hexdigest(),
            "approx_bytes": _b64_approx_len(b64),
        })
    return out


def image_summary(image: dict) -> str:
    """图片的单行摘要（台账/详情文本行；不触发解码）。

    ``[图片 image/png ~12KB]``。
    """
    size = image.get("approx_bytes", 0)
    if size >= 1024 * 1024:
        s = f"{size // (1024 * 1024)}MB"
    elif size >= 1024:
        s = f"{size // 1024}KB"
    else:
        s = f"{size}B"
    return f"[图片 {image.get('media_type', 'image')} ~{s}]"


def thumbnail_rows(image: dict, right_w: int) -> list:
    """图片 → 检查器半块真彩缩略图行（``list[list[StyledRun]]``）。

    缓存键 = (sha, w_cells, h_cells)。``right_w<=0`` / Pillow 缺失 / 解码
    失败 → 返回降级占位行（不中断渲染）。
    """
    if right_w <= 0:
        return []
    w_cells = max(8, min(44, right_w - 2))
    h_cells = _THUMB_ROWS
    key = (image["sha"], w_cells, h_cells)
    cached = _rows_cache.get(key)
    if cached is not None:
        return cached
    rows = _render(image["b64"], w_cells, h_cells)
    if len(_rows_cache) >= _ROW_CACHE_MAX:
        _rows_cache.clear()
    _rows_cache[key] = rows
    return rows


def _placeholder() -> list:
    """解码失败/缺 Pillow 的降级占位行。"""
    from src.tui.ink import StyledRun
    from src.tui.core.style import Style
    return [[StyledRun("(图片无法解码)", Style(fg=242))]]


def _color(r: int, g: int, b: int):
    """按终端能力返回 TrueColor 或 256 色 int（Style.fg/bg 兼容）。"""
    from src.tui.core.color import TrueColor, detect_truecolor, rgb_to_256
    if detect_truecolor():
        return TrueColor(r, g, b)
    return rgb_to_256(r, g, b)


def _render(b64: str, w_cells: int, h_cells: int) -> list:
    """解码 + 归一化 + 半块渲染 → StyledRun 行。"""
    from src.tui.ink import StyledRun
    from src.tui.core.style import Style

    try:
        from PIL import Image
    except ImportError:
        return _placeholder()

    try:
        raw = base64.b64decode(b64)
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception:
        return _placeholder()

    # EXIF 方向归一化（对齐 DSH normalizeImage）
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    # 透明合成到暗灰背景（避免透明像素显示为黑块/预乘色）
    has_alpha = img.mode in ("RGBA", "LA") or ("transparency" in img.info)
    if has_alpha:
        try:
            from PIL import Image as _Image
            bg_img = _Image.new("RGBA", img.size, (30, 30, 30, 255))
            img = bg_img.alpha_composite(img.convert("RGBA"))
        except Exception:
            pass
    try:
        img = img.convert("RGB")
    except Exception:
        return _placeholder()

    w, h = img.size
    if w <= 0 or h <= 0:
        return _placeholder()

    # 像素目标：w_cells 宽 × h_cells*2 高（半块：每格 = 1 像素宽 × 2 像素高）
    pw, ph = w_cells, h_cells * 2
    scale = min(pw / w, ph / h, 1.0)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    try:
        img = img.resize((nw, nh), Image.LANCZOS)
    except Exception:
        return _placeholder()

    px = img.load()
    rows: list = []
    for cy in range((nh + 1) // 2):
        row: list = []
        for cx in range(nw):
            y_top = cy * 2
            r1, g1, b1 = px[cx, min(y_top, nh - 1)]
            y_bot = y_top + 1
            if y_bot < nh:
                r2, g2, b2 = px[cx, y_bot]
            else:
                r2, g2, b2 = r1, g1, b1
            row.append(StyledRun(_HALF, Style(fg=_color(r1, g1, b1), bg=_color(r2, g2, b2))))
        rows.append(row)
    return rows if rows else _placeholder()
