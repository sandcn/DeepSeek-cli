"""多模态模型能力检测与图片 content blocks 构造

read_image 等图像工具依赖本模块判断当前模型是否支持多模态（视觉输入）：
- 支持 → 返回 OpenAI 兼容的 image_url data URI content blocks
  （Anthropic 适配器自动转换为 image block）
- 不支持 → 返回 RGBA 十六进制字符

判定依据（命中其一即为多模态）：
1. 模型名匹配已知多模态模型模式（claude / gpt-4o / glm-4v / llava 等）
2. 用户通过 RC 配置 ``multimodal_models`` 列表显式声明
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Optional

_logger = logging.getLogger(__name__)

# ── 已知支持视觉（多模态）的模型名模式（小写子串匹配） ──
# 覆盖主流多模态模型族；未覆盖的模型可通过 RC 配置 multimodal_models 扩展。
# 注意：子串匹配存在"误判代价"——把不支持视觉的变体判为多模态，代价仅是
# read_image 返回 base64 图片（模型可能无法理解），宁多勿少；反之漏判则
# 返回 RGBA hex（可读但低效）。如需精确控制，用 multimodal_models 配置。
_MULTIMODAL_MODEL_PATTERNS: tuple[str, ...] = (
    # Anthropic Claude 全系
    "claude",
    # OpenAI GPT-4o / GPT-4v / o 系列
    "gpt-4o", "gpt-4v", "gpt-4-vision", "gpt-4-turbo", "gpt-4.1", "gpt-5",
    # 智谱 GLM（4V 起支持视觉）
    "glm-4v", "glm-5v", "glm-4.5v", "glm-5", "glm-6v",
    # 阿里 Qwen-VL 系列
    "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
    # 谷歌 Gemini
    "gemini",
    # 开源视觉模型
    "llava", "llama-3.2-vision", "llama-4",
    "internvl", "intern-vl", "minicpm-v", "minicpmv", "minicpm-o",
    "pixtral", "molmo", "idefics", "cogvlm", "grok-2-vision", "grok-3", "grok-4",
    "phi-3-vision", "phi-4-vision", "deepseek-vl", "yi-vision", "yi-vl",
    "step-1v", "step-1o", "doubao-vision", "hunyuan-vision", "hunyuan-vl",
    "kosmos-2", "paligemma", "bakllava", "moondream", "fuyu-8b", "flamingo",
    # 小米 MiMo（视觉版）
    "mimo-vision", "mimo-vl", "mimo-v2.5",
)

# 短模型名（o1/o3/o4 等）需边界匹配防误命中（如 "foo1" 含 "o1"）
_SHORT_MODEL_PATTERN = re.compile(r"(^|[^a-z0-9])(o[134])([^a-z0-9]|$)")

# 结果缓存（模型名 → bool，模型名在运行期固定；模型名集合有限，缓存
# 无上限增长可接受——review P3 说明）
_multimodal_cache: dict[str, bool] = {}


def _match_model_pattern(model_lower: str) -> bool:
    """按已知模式匹配模型名（小写）。"""
    for pattern in _MULTIMODAL_MODEL_PATTERNS:
        if pattern in model_lower:
            return True
    return _SHORT_MODEL_PATTERN.search(model_lower) is not None


def _configured_multimodal_models() -> tuple[str, ...]:
    """读取 RC 配置的多模态模型扩展列表（小写）。"""
    try:
        from ..config import MULTIMODAL_MODELS
        if MULTIMODAL_MODELS:
            return tuple(
                str(m).lower() for m in MULTIMODAL_MODELS
                if isinstance(m, str) and m.strip()
            )
    except Exception:
        _logger.debug("读取 MULTIMODAL_MODELS 配置失败（回退空列表）", exc_info=True)
    return ()


def is_multimodal_model(model: Optional[str]) -> bool:
    """判断模型是否支持多模态（视觉输入）。

    Args:
        model: 模型名（如 "claude-sonnet-4-6"、"glm-5.2"）。

    Returns:
        True — 支持多模态；False — 不支持（或无法判断）。
    """
    if not model:
        return False
    key = model.lower()
    cached = _multimodal_cache.get(key)
    if cached is not None:
        return cached
    result = _match_model_pattern(key)
    if not result:
        for m in _configured_multimodal_models():
            if m and m in key:
                result = True
                break
    _multimodal_cache[key] = result
    return result


def clear_multimodal_cache() -> None:
    """清空多模态判定缓存（配置变更后调用，测试用）。"""
    _multimodal_cache.clear()


def build_image_content_blocks(
    text: str,
    image_bytes: bytes,
    media_type: str = "image/png",
) -> list[dict]:
    """构建 OpenAI 兼容多模态 content blocks（text + image_url data URI）。

    Args:
        text: 图片的文本说明/元信息（text block）。
        image_bytes: 图片字节（如 PNG 编码结果）。
        media_type: MIME 类型（默认 image/png）。

    Returns:
        ``[{"type": "text", "text": text},
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,<b64>"}}]``
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
    ]
