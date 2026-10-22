"""attachments — 内容寻址图片附件存储（对齐 DSH durable attachment）。

为什么需要附件存储
------------------
``read_image`` 旧实现把图片编码为 base64 data URI 直接塞进工具结果，图片
随每条历史消息重复序列化上传；同时会话存档里的 data URI 也无法跨会话复用。
本模块把图片字节落到 ``~/.chat/attachments/v1/objects/``，工具结果只携带
``{"type": "image", "attachment": {...}}`` 引用：

- **内容寻址**：文件名 = sha256，同一字节只存一份（去重）；
- **持久化**：会话存档只存引用，重启/恢复后仍可读取；
- **发送前解析**：``resolve_attachment_blocks`` 在请求副本上把引用展开为
  OpenAI 兼容 ``image_url`` data URI（Anthropic 适配器再转 image block）；
- **可核验**：读取时校验对象存在且长度与元数据一致。

目录布局（对齐 DSH 的 ``v1/objects`` 语义）::

    ~/.chat/attachments/v1/objects/<sha[:2]>/<sha>.<ext>
    ~/.chat/attachments/v1/objects/<sha[:2]>/<sha>.json   # 元数据

Attachments are never deleted automatically（对齐 DSH；避免删除仍被历史
会话引用的对象）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

_logger = logging.getLogger(__name__)

#: ``sha256:<64 hex>`` 附件 ID（严格校验，防止路径穿越）
_ATTACHMENT_ID_RE = re.compile(r"^sha256:([0-9a-f]{64})$")

#: 媒体类型 → 对象文件扩展名
_EXT_BY_MEDIA: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/vnd.microsoft.icon": ".ico",
    "image/x-portable-pixmap": ".ppm",
    "image/x-targa": ".tga",
    "image/x-sgi": ".sgi",
}

_lock = threading.RLock()


class AttachmentError(Exception):
    """附件读写失败（带回稳定错误码，供工具/请求层转换为可读提示）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def attachments_root() -> Path:
    """附件根目录（每次调用读取，便于测试/运行时切换 ``CHAT_DIR`` 位置）。"""
    from .. import paths

    return Path(paths.CHAT_ATTACHMENTS_DIR)


def _objects_dir(sha: str) -> Path:
    return attachments_root() / "v1" / "objects" / sha[:2]


def _atomic_write(path: Path, data: bytes) -> None:
    """同目录临时文件 + os.replace 原子发布（崩溃不会留下半截对象）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _media_type_from_ext(path: Path) -> str:
    ext = path.suffix.lower()
    for media, known_ext in _EXT_BY_MEDIA.items():
        if ext == known_ext:
            return media
    return "image/png"


def _make_ref(sha: str, meta: dict) -> dict:
    ref: dict[str, Any] = {
        "attachmentId": f"sha256:{sha}",
        "mediaType": meta.get("mediaType") or "image/png",
        "bytes": int(meta.get("bytes") or 0),
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
    }
    name = meta.get("name")
    if isinstance(name, str) and name:
        ref["name"] = name
    original = meta.get("originalDimensions")
    if isinstance(original, dict):
        try:
            ref["originalDimensions"] = {
                "width": int(original["width"]),
                "height": int(original["height"]),
            }
        except (KeyError, TypeError, ValueError):
            pass
    return ref


def save_image(
    data: bytes,
    media_type: str,
    *,
    width: int,
    height: int,
    name: Optional[str] = None,
    original_dimensions: Optional[dict] = None,
) -> dict:
    """持久化图片字节并返回附件引用（同字节幂等去重）。

    Args:
        data: 编码后的图片字节。
        media_type: MIME 类型（如 ``image/png``）。
        width / height: 规范化后（返回给模型的）像素尺寸。
        name: 原始文件名（仅展示，不参与路径）。
        original_dimensions: 自动降采样前的尺寸；未降采样时传 None。

    Returns:
        ``{"attachmentId": "sha256:…", "mediaType": …, "bytes": …,
        "width": …, "height": …, "name"?: …, "originalDimensions"?: …}``
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise AttachmentError("ATTACHMENT_EMPTY", "image attachment payload is empty")
    if not isinstance(media_type, str) or not media_type.startswith("image/"):
        raise AttachmentError(
            "ATTACHMENT_INVALID_MEDIA", f"invalid image media type: {media_type!r}"
        )
    raw = bytes(data)
    sha = hashlib.sha256(raw).hexdigest()
    media = media_type.split(";", 1)[0].strip().lower()
    ext = _EXT_BY_MEDIA.get(media, ".bin")
    directory = _objects_dir(sha)
    object_path = directory / f"{sha}{ext}"
    meta_path = directory / f"{sha}.json"

    meta = {
        "attachmentId": f"sha256:{sha}",
        "mediaType": media,
        "bytes": len(raw),
        "width": int(width or 0),
        "height": int(height or 0),
    }
    if name:
        meta["name"] = os.path.basename(str(name))
    if isinstance(original_dimensions, dict) and original_dimensions:
        try:
            meta["originalDimensions"] = {
                "width": int(original_dimensions["width"]),
                "height": int(original_dimensions["height"]),
            }
        except (KeyError, TypeError, ValueError):
            pass

    with _lock:
        # 已存在且长度一致 → 直接复用（内容寻址去重），不重复写盘。
        if object_path.is_file():
            try:
                if object_path.stat().st_size == len(raw):
                    if meta_path.is_file():
                        try:
                            stored = json.loads(meta_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            stored = None
                        if isinstance(stored, dict):
                            return _make_ref(sha, stored)
                    # 对象已存在但元数据缺失（上次写入在两步之间中断）→ 补写
                    # 元数据，保证附件尺寸/展示名可跨会话恢复。
                    _atomic_write_json(meta_path, meta)
                    return _make_ref(sha, meta)
            except OSError:
                _logger.debug("附件对象 stat 失败，准备重写: %s", object_path, exc_info=True)
        try:
            _atomic_write(object_path, raw)
            _atomic_write_json(meta_path, meta)
        except OSError as exc:
            raise AttachmentError(
                "ATTACHMENT_WRITE_FAILED", f"could not store image attachment: {exc}"
            ) from exc
    return _make_ref(sha, meta)


def _resolve_object(sha: str) -> tuple[Path, dict]:
    """定位附件对象并读取元数据（不存在/损坏时抛 AttachmentError）。"""
    directory = _objects_dir(sha)
    meta_path = directory / f"{sha}.json"
    meta: dict = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError):
            _logger.debug("附件元数据读取失败: %s", meta_path, exc_info=True)

    if meta.get("mediaType"):
        ext = _EXT_BY_MEDIA.get(str(meta["mediaType"]).lower(), ".bin")
        candidate = directory / f"{sha}{ext}"
        if candidate.is_file():
            return candidate, meta

    # 元数据缺失/扩展名变化：在对象目录内按 sha 前缀查找（排除 .json）。
    try:
        matches = [
            p for p in directory.iterdir()
            if p.is_file() and p.name.startswith(sha) and p.suffix != ".json"
        ]
    except OSError:
        matches = []
    if not matches:
        raise AttachmentError(
            "ATTACHMENT_NOT_FOUND",
            f"image attachment sha256:{sha} is not available",
        )
    return matches[0], meta


def load_image(attachment_id: str, *, expected_bytes: Optional[int] = None) -> tuple[bytes, dict]:
    """读取附件字节与元数据。

    Args:
        attachment_id: ``sha256:<64 hex>``。
        expected_bytes: 引用中记录的字节数；不一致视为损坏。

    Returns:
        ``(data, meta)``；meta 至少含 mediaType/bytes/width/height。
    """
    match = _ATTACHMENT_ID_RE.match(str(attachment_id or ""))
    if not match:
        raise AttachmentError(
            "ATTACHMENT_INVALID_ID", f"invalid attachment id: {attachment_id!r}"
        )
    sha = match.group(1)
    with _lock:
        path, meta = _resolve_object(sha)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AttachmentError(
                "ATTACHMENT_READ_FAILED", f"could not read image attachment: {exc}"
            ) from exc
    if expected_bytes is not None and int(expected_bytes) != len(data):
        raise AttachmentError(
            "ATTACHMENT_CORRUPT",
            f"image attachment sha256:{sha} has {len(data)} bytes, expected {expected_bytes}",
        )
    # 内容寻址完整性：读取时复核摘要（DSH 的 verified read 语义）。
    if hashlib.sha256(data).hexdigest() != sha:
        raise AttachmentError(
            "ATTACHMENT_CORRUPT",
            f"image attachment sha256:{sha} content digest does not match",
        )
    resolved_meta = dict(meta)
    resolved_meta.setdefault("attachmentId", f"sha256:{sha}")
    resolved_meta.setdefault("mediaType", _media_type_from_ext(path))
    resolved_meta.setdefault("bytes", len(data))
    return data, resolved_meta


def is_attachment_image_block(block: Any) -> bool:
    """判断 content block 是否为附件图片引用 block。"""
    if not isinstance(block, dict) or block.get("type") != "image":
        return False
    attachment = block.get("attachment")
    return (
        isinstance(attachment, dict)
        and isinstance(attachment.get("attachmentId"), str)
    )


def attachment_image_block(ref: dict) -> dict:
    """构造 DSH 风格附件图片 block：``{"type": "image", "attachment": ref}``。"""
    return {"type": "image", "attachment": dict(ref)}


def attachment_ref_from_block(block: dict) -> Optional[dict]:
    """取附件图片 block 的引用（非附件 block 返回 None）。"""
    if not is_attachment_image_block(block):
        return None
    ref = block.get("attachment")
    return dict(ref) if isinstance(ref, dict) else None


def load_block_image(block: dict) -> tuple[str, bytes, dict]:
    """读取附件图片 block：返回 ``(media_type, data, meta)``。"""
    ref = attachment_ref_from_block(block)
    if ref is None:
        raise AttachmentError("ATTACHMENT_INVALID_BLOCK", "not an attachment image block")
    data, meta = load_image(
        str(ref.get("attachmentId") or ""),
        expected_bytes=ref.get("bytes"),
    )
    media_type = str(ref.get("mediaType") or meta.get("mediaType") or "image/png")
    return media_type, data, meta


def block_base64(block: dict) -> tuple[str, str]:
    """附件图片 block → ``(media_type, base64)``（Trace 缩略图等展示用）。"""
    media_type, data, _meta = load_block_image(block)
    return media_type, base64.b64encode(data).decode("ascii")


def _iter_blocks(messages: Iterable) -> Iterable[tuple[list, int, dict]]:
    """遍历 messages 中 content list 的 (content, index, block)。"""
    if not isinstance(messages, (list, tuple)):
        return
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if isinstance(block, dict):
                yield content, index, block


def resolve_attachment_blocks(messages: list) -> dict:
    """在**请求副本**上把附件图片引用展开为 image_url data URI（原地修改）。

    发送前调用（``api.model_async``）：持久化存档保留附件引用，实际请求
    携带标准 OpenAI 兼容图片块。附件缺失/损坏时替换为文本占位，并记录
    日志——单张历史图片损坏不应让整个会话无法继续。

    Returns:
        ``{"resolved": n, "missing": m}``。
    """
    stats = {"resolved": 0, "missing": 0}
    for content, index, block in list(_iter_blocks(messages)):
        ref = attachment_ref_from_block(block)
        if ref is None:
            continue
        try:
            media_type, data, _meta = load_block_image(block)
        except Exception as exc:
            stats["missing"] += 1
            code = getattr(exc, "code", "ATTACHMENT_READ_FAILED")
            message = getattr(exc, "message", str(exc))
            _logger.warning("图片附件不可用（%s）：%s", code, message)
            content[index] = {
                "type": "text",
                "text": (
                    f"[图片附件不可用: {ref.get('attachmentId', '?')} "
                    f"({code})]"
                ),
            }
            continue
        b64 = base64.b64encode(data).decode("ascii")
        content[index] = {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{b64}"},
        }
        stats["resolved"] += 1
    return stats


__all__ = [
    "AttachmentError",
    "attachments_root",
    "save_image",
    "load_image",
    "is_attachment_image_block",
    "attachment_image_block",
    "attachment_ref_from_block",
    "load_block_image",
    "block_base64",
    "resolve_attachment_blocks",
]
