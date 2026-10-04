"""多模态模型能力检测与图片 content blocks 构造 — 兼容 re-export 层。

实现已下沉核心层 ``core.multimodal``（消除核心层/基础设施层对 api 的反向
依赖）。本模块保留旧路径 ``src.api.multimodal`` 兼容既有调用方；新代码请
使用 ``src.core.multimodal``。
"""

from __future__ import annotations

from ..core.multimodal import (  # noqa: F401
    _IMAGE_EXTENSIONS,
    _MD_IMAGE_RE,
    _MULTIMODAL_MODEL_PATTERNS,
    _RAW_IMAGE_URL_RE,
    _SHORT_MODEL_PATTERN,
    _configured_multimodal_models,
    _is_image_file,
    _local_image_data_uri,
    _match_model_pattern,
    _multimodal_cache,
    build_image_content_blocks,
    build_user_content_blocks,
    clear_multimodal_cache,
    content_to_text,
    extract_image_refs,
    is_multimodal_model,
)

__all__ = [
    "is_multimodal_model",
    "clear_multimodal_cache",
    "build_image_content_blocks",
    "content_to_text",
    "extract_image_refs",
    "build_user_content_blocks",
]
