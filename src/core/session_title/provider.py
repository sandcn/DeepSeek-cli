"""all-prompts 标题提供方 —— 提示词构建与模型输出解析。

对应 DeepSeek Harness ``dsh-session-title-all-prompts-llm``：

- **节奏**：每条新的合格用户提示词之后开启一次修订，折叠截至当前的**全部**
  合格用户消息（含会话历史），每次请求都携带已有标题并要求模型在旧标题仍然
  准确时**逐字保留**——同主题追问、补充细节、致谢、「继续」都不构成改名理由，
  只有主要主题/任务实质变化或扩展才更新；
- **输入**：系统指令（本模块 ``build_system_prompt``）+ 一条 user 消息，
  内容是 ``{"currentTitle": ..., "messages": [{"seq": n, "text": ...}]}``
  的 JSON；消息与既有标题都计入 ``max_input_bytes``（超限直接失败、保留旧
  标题，不截断历史）；
- **输出**：只取标题文本——容忍代码围栏、引号、``标题:`` 前缀与句末标点。

提供方只负责「构建输入 + 解析输出」；路由、上限、取消、记录、落盘与接受
校验由 ``SessionTitleService`` 统一负责（与 dsh 的执行模块分工一致）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from .normalize import sanitize_title

#: 标题模型系统指令模板（``{cjk}`` / ``{words}`` 为目标长度）。
SYSTEM_TEMPLATE = (
    "你是会话标题生成器。阅读用户消息，输出一个概括整段对话主要主题或任务的简短标题。\n"
    "规则：\n"
    "- 只输出标题文本本身：不要引号、不要编号、不要解释、不要句末标点。\n"
    "- 使用与用户消息相同的语言。\n"
    "- 中文标题不超过 {cjk} 个汉字；其它语言不超过 {words} 个词。\n"
    "- 若给定 currentTitle 仍然准确描述主要主题或任务，逐字原样返回它："
    "同主题追问、补充细节、致谢、继续请求都不构成修改理由。\n"
    "- 只有主要主题或任务发生实质变化或扩展、使旧标题不再准确时，才返回新标题。"
)

#: 代码围栏（````` ```text ````` / 行内反引号）——模型偶尔把标题包在围栏里。
_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*|\s*```\s*$")
#: ``标题:`` / ``标题：`` / ``Title:`` 前缀。
_PREFIX_RE = re.compile(r"^\s*(?:会话)?(?:标题|title)\s*[:：]\s*", re.IGNORECASE)
#: 包裹引号与包裹反引号（含日式「」『』——模型中文输出常见）。
_WRAPPING_RE = re.compile(r"^[\"'“”‘’`「」『』]+|[\"'“”‘’`「」『』]+$")
#: 句末标点（中文标题常被模型补上句号）。
_TRAILING_PUNCT = "。．.；;，,、:：!！?？"

#: 中断 / 空响应占位符的识别关键词：管线在对话被中断或返回空响应时会以
#: ``(已中断)`` / ``(已中断，无内容)`` 之类的括注文本作为正文返回——那**不是
#: 标题**（实测：中断后紧接着的标题请求会拿到该占位符，被写成标题）。命中即
#: 视为无有效输出，调用方保留当前标题。
_PLACEHOLDER_MARKERS = ("已中断", "无内容")


def _is_placeholder_title(text: str) -> bool:
    """是否为管线占位符（``(已中断)`` / ``(已中断，无内容)`` 等），不是标题。"""
    stripped = str(text or "").strip()
    if not (stripped.startswith("(") and stripped.endswith(")")):
        return False
    return any(marker in stripped for marker in _PLACEHOLDER_MARKERS)


def build_system_prompt(*, target_words: int, target_cjk_characters: int) -> str:
    """构建标题模型的系统指令（目标词数 / CJK 字符数）。"""
    return SYSTEM_TEMPLATE.format(
        cjk=max(1, int(target_cjk_characters)),
        words=max(1, int(target_words)),
    )


def build_user_payload(
    messages: Sequence[tuple[int, str]], current_title: str | None,
) -> str:
    """构建标题请求的 user 消息内容（JSON 文本）。

    Args:
        messages: ``[(seq, text), ...]``——截至当前修订的全部合格用户消息。
        current_title: 已接受的最新标题（可能来自 fallback；``None``/空串表示
            尚无标题，走初始生成路径）。

    Returns:
        UTF-8 JSON 文本（调用方按字节上限校验，超限则不发起请求）。
    """
    payload = {
        "currentTitle": sanitize_title(current_title or ""),
        "messages": [
            {"seq": int(seq), "text": str(text or "")}
            for seq, text in messages
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def build_title_messages(
    messages: Sequence[tuple[int, str]],
    current_title: str | None,
    *,
    target_words: int,
    target_cjk_characters: int,
) -> list[dict]:
    """构建完整的标题请求消息列表（system + user）。"""
    return [
        {
            "role": "system",
            "content": build_system_prompt(
                target_words=target_words,
                target_cjk_characters=target_cjk_characters,
            ),
        },
        {
            "role": "user",
            "content": build_user_payload(messages, current_title),
        },
    ]


def request_bytes(messages: Sequence[dict]) -> int:
    """请求消息的 UTF-8 字节数（口径同 ``max_input_bytes`` 校验）。"""
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += len(content.encode("utf-8", "replace"))
        elif content is not None:
            total += len(str(content).encode("utf-8", "replace"))
        role = message.get("role")
        if isinstance(role, str):
            total += len(role.encode("utf-8", "replace"))
    return total


def parse_title_response(raw: object) -> str:
    """把模型输出解析成标题文本（容错：围栏 / 引号 / 前缀 / 多行 / 句末标点）。

    取**第一个非空行**作为标题（模型偶尔先写解释行、再写标题）；随后剥离
    代码围栏、``标题:`` 前缀、包裹引号与句末标点，最后统一走
    ``normalize.sanitize_title``（控制序列 / 不可见符 / 空白 / 代理项）。

    Args:
        raw: 模型返回的正文（``reasoning_content`` 兜底时也走本函数）。

    Returns:
        可接受的标题；无法解析出非空文本时返回空串（调用方保留旧标题）。
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    text = _FENCE_RE.sub("", text).strip()
    line = ""
    for candidate in text.splitlines():
        line = candidate.strip()
        if line:
            break
    if not line:
        return ""
    line = _PREFIX_RE.sub("", line)
    line = line.strip()
    line = _WRAPPING_RE.sub("", line)
    line = line.strip()
    line = line.rstrip(_TRAILING_PUNCT).strip()
    cleaned = sanitize_title(line)
    if _is_placeholder_title(cleaned):
        return ""
    return cleaned


__all__ = [
    "SYSTEM_TEMPLATE",
    "build_system_prompt",
    "build_title_messages",
    "build_user_payload",
    "parse_title_response",
    "request_bytes",
]
