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
import os
import re
from typing import Any, Optional

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
    # DeepSeek V4 多模态（实验性视觉模型）
    "deepseek-v4-flash-vision-exp",
)

# 短模型名（o1/o3/o4 等）需边界匹配防误命中（如 "foo1" 含 "o1"）
_SHORT_MODEL_PATTERN = re.compile(r"(^|[^a-z0-9])(o[134])([^a-z0-9]|$)")

# 结果缓存（模型名 → bool，模型名在运行期固定；模型名集合有限，缓存
# 无上限增长可接受——review P3 说明）
_multimodal_cache: dict[str, bool] = {}

# ── 图片引用识别（用户消息图片输入） ─────────────────────
# 支持三种图片引用语法：
#   1. Markdown 图片：![alt](path_or_url)
#   2. 裸 http(s) URL（以常见图片扩展名结尾，忽略查询参数）
#   3. 本地图片路径（文件存在且以常见图片扩展名结尾）
_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
})

# Markdown 图片语法（捕获 alt 与引用目标，引用目标不含空白）
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)\s*\)")

# 裸 http(s) 图片 URL：扩展名必须为图片格式（忽略 ?query 与 #fragment）
_RAW_IMAGE_URL_RE = re.compile(
    r"(https?://[^\s<>\"']+?\.(?:png|jpe?g|gif|webp|bmp)"
    r"(?:[?#][^\s<>\"']*)?)",
    re.IGNORECASE,
)


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
        model: 模型名（如 "claude-sonnet-4-6"、"deepseek-v4-flash-vision-exp"）。

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


# ── 消息 content 兼容辅助（str / list[dict]） ─────────────

def content_to_text(content: Any) -> str:
    """将消息 content（str 或 list[dict] content blocks）转换为纯文本。

    多模态用户消息 / 工具结果的 content 为 OpenAI 兼容 content blocks 列表
    （如 ``[{"type": "text", "text": ...}, {"type": "image_url", ...}]``）。
    本函数提取其中的 text 部分拼接为纯文本，供标题提取、token 估算、
    上下文统计、导出等消费方使用；str 原样返回。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if block is None:
                continue
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        parts.append(text)
                    continue
                if btype == "image_url":
                    # 只保留占位标记，不输出 data URI/base64——避免图片数据
                    # 灌入上下文统计、会话标题、导出等文本消费方。
                    parts.append("[图片]")
                    continue
                # 其他 block（file 等）：尝试取 text 字段，无则跳过
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    parts.append(text)
                continue
            parts.append(str(block))
        return " ".join(parts)
    return str(content)


# ── 用户消息图片输入（本地图片 / 图片 URL） ───────────────

def _is_image_file(path: str) -> bool:
    """判断路径是否为本地图片文件（扩展名匹配且文件存在）。"""
    if not path or not os.path.isfile(path):
        return False
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS


def _local_image_data_uri(path: str) -> Optional[str]:
    """读取本地图片并编码为 data URI（失败返回 None）。

    限制：单文件最大 32 MiB（DeepSeek API 内联图片上限），超限返回 None
    并记录日志（提示改用 Files API / read_image 工具降采样）。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > 32 * 1024 * 1024:
        _logger.warning("本地图片 %s 超过 32MiB 内联限制，跳过", path)
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/png")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{b64}"


def extract_image_refs(text: str) -> list[dict]:
    """从用户文本中提取图片引用（保持出现顺序）。

    支持三种语法（按优先级）：
    1. Markdown 图片 ``![alt](path_or_url)``
    2. 裸 http(s) 图片 URL（以 .png/.jpg/.jpeg/.gif/.webp/.bmp 结尾）
    3. 本地图片路径（文件存在且扩展名为图片格式）

    Returns:
        ``[{"kind": "md"|"url"|"local", "alt": str, "ref": str,
            "start": int, "end": int}, ...]``
        ref 为路径或 URL；start/end 为在原始文本中的字符区间（含）。
    """
    if not text:
        return []
    refs: list[dict] = []

    # 1. Markdown 图片语法
    md_spans: list[tuple[int, int]] = []
    for m in _MD_IMAGE_RE.finditer(text):
        alt = (m.group(1) or "").strip()
        target = m.group(2).strip()
        if not target:
            continue
        if target.lower().startswith(("http://", "https://")):
            kind = "url"
        else:
            kind = "local" if _is_image_file(target) else "skip"
            if kind == "skip":
                continue
        start, end = m.start(), m.end() - 1
        md_spans.append((start, end))
        refs.append({
            "kind": kind, "alt": alt, "ref": target,
            "start": start, "end": end,
        })

    # 2. 裸 http(s) 图片 URL（跳过已被 Markdown 语法覆盖的区间）
    for m in _RAW_IMAGE_URL_RE.finditer(text):
        start, end = m.start(), m.end() - 1
        if any(not (end < s or start > e) for s, e in md_spans):
            continue
        refs.append({
            "kind": "url", "alt": "", "ref": m.group(1),
            "start": start, "end": end,
        })

    # 3. 本地图片路径：按空白/换行切分 token，匹配存在的图片文件
    #    （仅检查 Markdown 语法之外的裸路径片段）
    for m in re.finditer(r"\S+", text):
        start, end = m.start(), m.end() - 1
        if any(not (end < s or start > e) for s, e in md_spans):
            continue
        token = m.group(0).rstrip(",;:。，；：、")
        if not token:
            continue
        if token.lower().startswith(("http://", "https://")):
            continue  # URL 已由规则 2 处理
        if _is_image_file(token):
            refs.append({
                "kind": "local", "alt": "", "ref": token,
                "start": start, "end": start + len(token) - 1,
            })

    # 按出现顺序排序
    refs.sort(key=lambda r: r["start"])
    return refs


def build_user_content_blocks(text: str, model: Optional[str]) -> Any:
    """将用户输入文本转换为消息 content（多模态模型 + 图片引用时）。

    - 模型非多模态：原样返回 text（str）——图片引用作为普通文本传给模型。
    - 模型多模态但无图片引用：原样返回 text（str）。
    - 模型多模态且有图片引用：返回 OpenAI 兼容 content blocks
      ``[{"type": "text", ...}, {"type": "image_url", ...}, ...]``：
        本地路径 → base64 data URI 内联；http(s) URL → 原样 image_url。
      原文本中的图片引用替换为 ``[图片: <描述>]`` 占位标记，保留上下文。

    Args:
        text: 用户输入文本。
        model: 当前模型名（用于多模态能力判定）。

    Returns:
        str 或 list[dict]（content blocks）。
    """
    if not text or not is_multimodal_model(model):
        return text
    refs = extract_image_refs(text)
    if not refs:
        return text

    # 本地图片读取失败（过大/IO 错误）→ 跳过该引用（保留原文本占位）
    blocks: list[dict] = []
    text_parts: list[str] = []
    cursor = 0
    has_image = False
    for ref in refs:
        url: Optional[str] = None
        if ref["kind"] == "url":
            url = ref["ref"]
        elif ref["kind"] == "local":
            url = _local_image_data_uri(ref["ref"])
        if not url:
            continue  # 本地图片读取失败：跳过，文本中保留原样
        has_image = True
        # 文本段：光标到引用起点 + 引用替换为占位标记
        text_parts.append(text[cursor:ref["start"]])
        alt = ref.get("alt") or ""
        label = alt if alt else (ref["ref"] if ref["kind"] == "local" else "图片")
        text_parts.append(f"[图片: {label}]")
        cursor = ref["end"] + 1
        blocks.append({
            "type": "image_url",
            "image_url": {"url": url},
        })

    if not has_image:
        return text

    text_parts.append(text[cursor:])
    blocks.insert(0, {"type": "text", "text": "".join(text_parts)})
    return blocks
