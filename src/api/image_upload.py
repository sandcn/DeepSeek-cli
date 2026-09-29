"""上传前图片瘦身 — 折叠 / 压缩 / 编码缓存

背景（为什么"图片一多，每次请求都很卡"）：
``read_image`` 按原始尺寸返回 base64 PNG（单张 1080p 截图 ≈ 6MB，base64
后 ≈ 8MB），并随对话累积进 messages 历史。而每轮 API 请求都会把**整个**
messages（含全部历史图片的 base64 data URI）重新序列化上传——实测 6 张
1080p 噪声 PNG 的请求体即 ≈ 47.5MB，且随图片数量线性膨胀，故多图后每次
请求都极慢。

本模块在**发送副本**上做三件事（不改动 ``agent.messages`` / 会话存档，
``read_image`` 的「原始尺寸返回」契约保持不变；TUI 显示与存档不受影响）：

1. **折叠**：发送时仅保留最近 ``N`` 张图片，更早的图片块替换为文本占位
   ——上传体积不再随图片数量线性膨胀（模型仍可从占位文本看到图片路径，
   需要时重新 ``read_image``）；
2. **压缩**：超过长边 / 体积阈值的图片降采样并重编码为 JPEG（透明通道
   白底合成）——显著减小单张 base64 体积；
3. **缓存**：同一图片字节的压缩结果按 ``(sha256, 参数)`` 缓存复用（LRU），
   避免同一张图在多次请求中重复解码 / 编码。

默认开启，经 RC 配置可关闭或调参：
``IMAGE_UPLOAD_OPTIMIZE`` / ``IMAGE_UPLOAD_KEEP_RECENT`` /
``IMAGE_UPLOAD_MAX_DIMENSION`` / ``IMAGE_UPLOAD_QUALITY``。
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import threading
from collections import OrderedDict
from typing import Any, Optional

_logger = logging.getLogger(__name__)

#: ``data:image/<type>;base64,<data>``（忽略 media type 参数，如 ``;charset``）
_DATA_URI_RE = re.compile(r"^data:(?P<mime>[^;,]+)?;base64,(?P<data>.*)$", re.S)

#: 小于该字节数的图片直接跳过（解码/重编码成本大于收益）
_MIN_COMPRESS_BYTES = 64 * 1024
#: 大于该字节数即认为「值得压缩」（即便尺寸未超长边上限）
_TARGET_BYTES = 256 * 1024
#: 压缩结果缓存上限（条）；单条为压缩后 data URI 字符串
_CACHE_MAX = 48

#: 被折叠图片的占位文本（替换 image block，保留位置语义）
_FOLDED_PLACEHOLDER = (
    "[图片已省略以节省上传体积（历史图片仅保留最近若干张；"
    "如需该图请重新调用 read_image）]"
)

#: LRU 缓存：key → 优化后的 data URI（或原 url 表示无需优化）
_compress_cache: "OrderedDict[str, str]" = OrderedDict()
_cache_lock = threading.Lock()


# ── 配置读取 ──────────────────────────────────────────────

def _cfg(name: str, default: Any) -> Any:
    """读取 RC 配置项（延迟导入 + 容错，缺失/异常回退默认值）。"""
    try:
        from .. import config as _config_mod
        value = getattr(_config_mod, name, None)
        return default if value is None else value
    except Exception:
        return default


def _cfg_bool(name: str, default: bool) -> bool:
    value = _cfg(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _cfg_int(name: str, default: int) -> int:
    value = _cfg(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── 图片块判定 / data URI 解析 ────────────────────────────

def _is_image_block(block: Any) -> bool:
    """判断 content block 是否为携带 base64 图片的块。

    支持 OpenAI 兼容 ``image_url``（data URI）与 Anthropic ``image``
    （``source.type == "base64"``）两种形态。
    """
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if btype == "image_url":
        container = block.get("image_url")
        return isinstance(container, dict) and isinstance(container.get("url"), str)
    if btype == "image":
        source = block.get("source")
        return isinstance(source, dict) and source.get("type") == "base64"
    return False


def _block_image_url(block: dict) -> Optional[str]:
    """提取块中的 data URI（非 data URI / 非法结构返回 None）。"""
    btype = block.get("type")
    if btype == "image_url":
        container = block.get("image_url")
        if isinstance(container, dict):
            url = container.get("url")
            if isinstance(url, str):
                return url
    return None


def _decode_data_uri(url: str) -> Optional[tuple[str, bytes]]:
    """解析 ``data:image/...;base64,...`` 为 (media_type, raw_bytes)。

    非 data URI / 非 image 前缀 / base64 解码失败 → None（调用方跳过）。
    """
    if not isinstance(url, str):
        return None
    m = _DATA_URI_RE.match(url)
    if not m:
        return None
    mime = (m.group("mime") or "").strip().lower()
    if not mime.startswith("image/"):
        return None
    try:
        raw = base64.b64decode(m.group("data"))
    except Exception:
        return None
    return mime, raw


# ── 压缩 ──────────────────────────────────────────────────

def _compress_image_bytes(
    raw: bytes, max_dimension: int, quality: int,
) -> Optional[bytes]:
    """降采样 + 重编码为 JPEG，返回新字节。

    返回 None 表示「无需压缩」（尺寸未超长边且体积未超阈值、多帧动图、
    解码失败等）——调用方保留原图。透明通道以白底合成为 RGB（JPEG 无 alpha）。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(raw)) as img:
            # 多帧动图（GIF/WebP 动画）：压缩只保留首帧会丢失动画，跳过
            if getattr(img, "n_frames", 1) > 1:
                return None
            img.load()
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if width <= 0 or height <= 0:
                return None
            long_side = max(width, height)
            need_resize = bool(max_dimension and max_dimension > 0
                               and long_side > max_dimension)
            need_recode = len(raw) > _TARGET_BYTES
            if not need_resize and not need_recode:
                return None
            if need_resize:
                scale = max_dimension / long_side
                new_size = (max(1, round(width * scale)),
                            max(1, round(height * scale)))
                img = img.resize(new_size, Image.LANCZOS)
            has_alpha = (
                img.mode in ("RGBA", "LA", "PA")
                or "A" in img.mode
                or (img.mode == "P" and img.info.get("transparency") is not None)
            )
            if has_alpha:
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG",
                     quality=max(1, min(100, int(quality))), optimize=True)
            return buf.getvalue()
    except Exception:
        _logger.debug("上传前图片压缩失败，保留原图", exc_info=True)
        return None


def _optimize_data_uri(url: str, max_dimension: int, quality: int) -> str:
    """优化单个 data URI：返回压缩后的 data URI（无需优化/失败时返回原值）。

    带 LRU 缓存：同一图片字节 + 同一参数 → 直接命中，不重复解码/编码。
    """
    parsed = _decode_data_uri(url)
    if parsed is None:
        return url
    mime, raw = parsed
    if len(raw) <= _MIN_COMPRESS_BYTES:
        return url

    key = f"{hashlib.sha256(raw).hexdigest()}|{mime}|{max_dimension}|{quality}"
    with _cache_lock:
        cached = _compress_cache.get(key)
        if cached is not None:
            _compress_cache.move_to_end(key)
            return cached

    new_raw = _compress_image_bytes(raw, max_dimension, quality)
    if new_raw is None or len(new_raw) >= len(raw):
        result = url
    else:
        b64 = base64.b64encode(new_raw).decode("ascii")
        result = f"data:image/jpeg;base64,{b64}"

    with _cache_lock:
        _compress_cache[key] = result
        while len(_compress_cache) > _CACHE_MAX:
            _compress_cache.popitem(last=False)
    return result


def clear_upload_cache() -> None:
    """清空压缩结果缓存（测试 / 配置变更后调用）。"""
    with _cache_lock:
        _compress_cache.clear()


# ── 折叠 ──────────────────────────────────────────────────

def _collect_image_blocks(messages: list) -> list[tuple[list, int, dict]]:
    """按消息顺序收集 (content_list, idx, block) 引用列表。"""
    refs: list[tuple[list, int, dict]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for idx, block in enumerate(content):
            if _is_image_block(block):
                refs.append((content, idx, block))
    return refs


def _fold_old_images(messages: list, keep_recent: int) -> int:
    """仅保留最近 keep_recent 张图片，更早的图片块替换为文本占位。

    keep_recent <= 0 视为不限制（全部保留）。
    """
    if not keep_recent or keep_recent <= 0:
        return 0
    refs = _collect_image_blocks(messages)
    total = len(refs)
    if total <= keep_recent:
        return 0
    fold_count = total - keep_recent
    for content, idx, _block in refs[:fold_count]:
        content[idx] = {"type": "text", "text": _FOLDED_PLACEHOLDER}
    return fold_count


# ── 主入口 ────────────────────────────────────────────────

def optimize_messages_for_upload(
    messages: list,
    *,
    enabled: Optional[bool] = None,
    keep_recent: Optional[int] = None,
    max_dimension: Optional[int] = None,
    quality: Optional[int] = None,
) -> dict:
    """在**发送副本**上执行图片折叠 + 压缩 + 缓存复用（原地修改 messages）。

    调用方须传入已深拷贝的 messages（本函数会原地替换图片块），
    本模块不负责拷贝，也不影响原始 agent.messages / 会话存档。

    Args:
        messages: 待发送消息列表（发送副本，原地修改）。
        enabled: 是否启用优化；None 读取配置 IMAGE_UPLOAD_OPTIMIZE（默认 True）。
        keep_recent: 保留最近图片数；None 读取配置（默认 4）；<=0 不折叠。
        max_dimension: 图片长边上限（像素）；None 读取配置（默认 1568）。
        quality: JPEG 质量 1~100；None 读取配置（默认 80）。

    Returns:
        统计 dict：folded（折叠图片数）/ compressed（压缩图片数）/
        skipped（压缩失败或无需压缩数）/ bytes_before / bytes_after
        （压缩前后 data URI 字符数）。任何异常都被吞掉并返回已得统计——
        图片优化失败不应影响请求发送。
    """
    stats = {
        "folded": 0, "compressed": 0, "skipped": 0,
        "bytes_before": 0, "bytes_after": 0,
    }
    if not isinstance(messages, list) or not messages:
        return stats

    if enabled is None:
        enabled = _cfg_bool("IMAGE_UPLOAD_OPTIMIZE", True)
    if not enabled:
        return stats
    if keep_recent is None:
        keep_recent = _cfg_int("IMAGE_UPLOAD_KEEP_RECENT", 4)
    if max_dimension is None:
        max_dimension = _cfg_int("IMAGE_UPLOAD_MAX_DIMENSION", 1568)
    if quality is None:
        quality = _cfg_int("IMAGE_UPLOAD_QUALITY", 80)

    try:
        stats["folded"] = _fold_old_images(messages, keep_recent)

        for content, idx, block in _collect_image_blocks(messages):
            url = _block_image_url(block)
            if not url or _decode_data_uri(url) is None:
                # 非 data URI 图片（http URL / Anthropic image block）不处理
                continue
            new_url = _optimize_data_uri(url, max_dimension, quality)
            if new_url == url:
                stats["skipped"] += 1
                continue
            block["image_url"]["url"] = new_url
            stats["compressed"] += 1
            stats["bytes_before"] += len(url)
            stats["bytes_after"] += len(new_url)

        if stats["folded"] or stats["compressed"]:
            _logger.debug(
                "上传前图片优化：折叠 %d 张、压缩 %d 张、跳过 %d 张"
                "（data URI %d → %d 字符）",
                stats["folded"], stats["compressed"], stats["skipped"],
                stats["bytes_before"], stats["bytes_after"],
            )
    except Exception:
        _logger.debug("上传前图片优化异常，跳过优化", exc_info=True)
    return stats
