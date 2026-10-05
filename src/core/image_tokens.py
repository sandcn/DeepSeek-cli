"""多模态图片（视觉）token 估算 — 上下文统计口径。

messages 中的图片以 OpenAI 兼容 content blocks 承载（``image_url`` data URI /
Anthropic ``image`` block）。文本提取 ``content_to_text`` 对图片只保留
``[图片]`` 占位，视觉 token 不计入上下文统计 → 上下文使用率百分比与压缩
判断对含图会话低估。本模块按图像尺寸估算视觉 token：

    tokens = ceil(w / patch) * ceil(h / patch)      # 默认 patch=28

并**对齐上传前瘦身规则**（``image_upload_*`` 配置）——只计最近
``image_upload_keep_recent`` 张、长边按 ``image_upload_max_dimension`` 等比
降采样——使估算接近模型实际收到的图片。

计费口径无关：``/cost`` 的输入 token 来自 API 真实 usage（已含图片 token），
不依赖本估算；本估算仅用于**上下文占用百分比与压缩判断**。

配置项（可经 RC 覆盖，便于按模型/供应商扩展）：
  - ``multimodal_image_token_patch``   ：分块边长（默认 28；<=0 时全部用固定值）
  - ``multimodal_image_token_default`` ：无法读尺寸（http URL / 解码失败 /
    Pillow 缺失）时每图固定占用（默认 800）
"""

from __future__ import annotations

import base64
import io
import re
from typing import Any, Iterator, Optional, Tuple

_DEFAULT_PATCH = 28
_DEFAULT_FIXED = 800

#: ``data:image/<type>;base64,<data>``（忽略 media type 参数）
_DATA_URI_RE = re.compile(r"^data:(?P<mime>[^;,]+)?;base64,(?P<data>.*)$", re.S)

_IMAGE_BLOCK_TYPES = ("image_url", "image")


# ── 配置读取（延迟导入 + 容错，缺失/异常回退默认值） ──────

def _cfg(name: str, default: Any) -> Any:
    try:
        from .. import config as _config_mod
        value = getattr(_config_mod, name, None)
        return default if value is None else value
    except Exception:
        return default


def _cfg_int(name: str, default: int) -> int:
    value = _cfg(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cfg_bool(name: str, default: bool) -> bool:
    value = _cfg(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


# ── 图片块解析 ────────────────────────────────────────────

def is_image_block(block: Any) -> bool:
    """判断 content block 是否为图片块（image_url / Anthropic image）。"""
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if btype == "image_url":
        container = block.get("image_url")
        return isinstance(container, dict) and isinstance(container.get("url"), str)
    if btype == "image":
        source = block.get("source")
        if isinstance(source, dict) and source.get("type") == "base64":
            return isinstance(source.get("data"), str)
        if isinstance(source, dict) and source.get("type") == "url":
            return isinstance(source.get("url"), str)
    return False


def _block_raw_bytes(block: dict) -> Optional[bytes]:
    """提取图片原始字节（仅 data URI / base64 source；http URL 返回 None）。"""
    btype = block.get("type")
    if btype == "image_url":
        container = block.get("image_url")
        url = container.get("url") if isinstance(container, dict) else None
        if not isinstance(url, str):
            return None
        m = _DATA_URI_RE.match(url)
        if not m:
            return None
        mime = (m.group("mime") or "").strip().lower()
        if mime and not mime.startswith("image/"):
            return None
        try:
            return base64.b64decode(m.group("data"))
        except Exception:
            return None
    if btype == "image":
        source = block.get("source")
        if not isinstance(source, dict):
            return None
        if source.get("type") == "base64":
            data = source.get("data")
            if not isinstance(data, str):
                return None
            try:
                return base64.b64decode(data)
            except Exception:
                return None
    return None


def _image_size(raw: bytes) -> Optional[Tuple[int, int]]:
    """读取图片像素尺寸（仅读 header，不解码全图）；失败返回 None。"""
    if not raw:
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return int(width), int(height)


# ── 估算 ──────────────────────────────────────────────────

def estimate_image_tokens_by_size(width: int, height: int,
                                  patch: int = _DEFAULT_PATCH,
                                  default: int = _DEFAULT_FIXED) -> int:
    """按尺寸估算视觉 token：``ceil(w/patch) * ceil(h/patch)``。"""
    try:
        width = int(width)
        height = int(height)
        patch = int(patch)
    except (TypeError, ValueError):
        return max(0, int(default))
    if width <= 0 or height <= 0 or patch <= 0:
        return max(0, int(default))
    cols = (width + patch - 1) // patch
    rows = (height + patch - 1) // patch
    return max(1, cols * rows)


def estimate_image_block_tokens(block: dict, patch: int = _DEFAULT_PATCH,
                                max_dimension: int = 0,
                                default: int = _DEFAULT_FIXED) -> int:
    """估算单个图片块的视觉 token（读尺寸 → 长边约束 → patch 分块）。"""
    size = _image_size(_block_raw_bytes(block)) if is_image_block(block) else None
    if size is None:
        return max(0, int(default))
    width, height = size
    if max_dimension and max_dimension > 0 and max(width, height) > max_dimension:
        scale = max_dimension / float(max(width, height))
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))
    return estimate_image_tokens_by_size(width, height, patch, default)


def iter_image_blocks(content: Any) -> Iterator[dict]:
    """遍历 content（list[dict]）中的图片块。"""
    if not isinstance(content, list):
        return
    for block in content:
        if is_image_block(block):
            yield block


def _iter_messages_image_blocks(messages: list) -> Iterator[dict]:
    if not isinstance(messages, list):
        return
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        yield from iter_image_blocks(msg.get("content"))


def estimate_messages_image_tokens(
    messages: list,
    *,
    keep_recent: Optional[int] = None,
    max_dimension: Optional[int] = None,
    patch: Optional[int] = None,
    default: Optional[int] = None,
) -> int:
    """估算 messages 中图片的视觉 token 合计（对齐上传瘦身规则）。

    Args:
        messages: 消息列表。
        keep_recent: 只计最近 N 张图片（None 读配置：``image_upload_optimize``
            开启时取 ``image_upload_keep_recent``，否则不折叠）；<=0 表示全部计入。
        max_dimension: 长边上限（None 读配置；optimize 关闭时不限制）。
        patch: 分块边长（None 读 ``multimodal_image_token_patch``，默认 28）。
        default: 无法读尺寸时每图占用（None 读
            ``multimodal_image_token_default``，默认 800）。

    Returns:
        视觉 token 合计（无图片时 0）。
    """
    if not messages:
        return 0
    optimize = _cfg_bool("IMAGE_UPLOAD_OPTIMIZE", True)
    if keep_recent is None:
        keep_recent = _cfg_int("IMAGE_UPLOAD_KEEP_RECENT", 4) if optimize else 0
    if max_dimension is None:
        max_dimension = _cfg_int("IMAGE_UPLOAD_MAX_DIMENSION", 1568) if optimize else 0
    if patch is None:
        patch = _cfg_int("MULTIMODAL_IMAGE_TOKEN_PATCH", _DEFAULT_PATCH)
    if default is None:
        default = _cfg_int("MULTIMODAL_IMAGE_TOKEN_DEFAULT", _DEFAULT_FIXED)

    blocks = list(_iter_messages_image_blocks(messages))
    if not blocks:
        return 0
    if keep_recent and keep_recent > 0 and len(blocks) > keep_recent:
        blocks = blocks[-keep_recent:]

    return sum(
        estimate_image_block_tokens(b, patch=patch,
                                    max_dimension=max_dimension, default=default)
        for b in blocks
    )


__all__ = [
    "is_image_block",
    "iter_image_blocks",
    "estimate_image_tokens_by_size",
    "estimate_image_block_tokens",
    "estimate_messages_image_tokens",
]
