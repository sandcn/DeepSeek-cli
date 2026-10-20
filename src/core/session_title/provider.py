"""all-prompts 标题提供方 —— 提示词构建与模型输出解析。

对应 DeepSeek Harness ``dsh-session-title-all-prompts-llm``：

- **节奏**：每条新的合格用户提示词之后开启一次修订，折叠截至当前的**全部**
  合格用户消息（含会话历史），每次请求都携带已有标题并要求模型在旧标题仍然
  准确时**逐字保留**——同主题追问、补充细节、致谢、「继续」都不构成改名理由，
  只有主要主题/任务实质变化或扩展才更新；
- **输入**：系统指令（本模块 ``build_system_prompt``）+ 一条 user 消息，
  内容是 ``{"currentTitle": ..., "messages": [{"seq": n, "text": ...}],
  "omittedMessages": n}`` 的 JSON——**所有合格用户消息都参与**（截至当前
  修订的全量历史）；仅在超出 ``max_input_bytes`` 时按**最新优先**裁剪：
  从最新消息往前累计预算，更旧的超预算消息被省略并计入 ``omittedMessages``，
  最新一条永远保留（单独超预算时就地截断），因此标题始终以最新消息为准；
- **输出**：只取标题文本——容忍代码围栏、引号、``标题:`` 前缀与句末标点。

提供方只负责「构建输入 + 解析输出」；路由、上限、取消、记录、落盘与接受
校验由 ``SessionTitleService`` 统一负责（与 dsh 的执行模块分工一致）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from .normalize import sanitize_title, truncate_utf8

#: 标题模型系统指令模板（``{cjk}`` / ``{words}`` 为目标长度）。
SYSTEM_TEMPLATE = (
    "你是会话标题生成器。阅读用户消息，输出一个概括整段对话主要主题或任务的简短标题。\n"
    "规则：\n"
    "- **以最新的用户消息为准**：先读最新的用户消息，判断它要求的是什么；标题必须覆盖"
    "对话当前的主题与进展，更早的消息只作背景。\n"
    "- 只输出标题文本本身：不要引号、不要编号、不要解释、不要句末标点。\n"
    "- 使用与用户消息相同的语言。\n"
    "- 中文标题不超过 {cjk} 个汉字；其它语言不超过 {words} 个词。\n"
    "- 只有最新消息仍属于同一件事、旧标题仍然准确时，才逐字返回 currentTitle："
    "同主题追问、补充细节、致谢、继续请求都不构成修改理由。\n"
    "- 若最新消息把工作推进到新阶段或新主题（例如从「讨论方案」到「实现并验证」），"
    "即使旧标题仍沾边，也要用能代表最新阶段的新标题。"
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
    *, omitted: int = 0,
) -> str:
    """构建标题请求的 user 消息内容（JSON 文本）。

    Args:
        messages: ``[(seq, text), ...]``——参与本次修订的合格用户消息
            （默认全部；超预算时由 ``select_messages_within_budget`` 裁剪）。
        current_title: 已接受的最新标题（可能来自 fallback；``None``/空串表示
            尚无标题，走初始生成路径）。
        omitted: 因超出字节预算被省略的**更旧**消息数（>0 时写入
            ``omittedMessages``，让模型知道历史被裁剪、以给定最新消息为准）。

    Returns:
        UTF-8 JSON 文本。
    """
    payload = {
        "currentTitle": sanitize_title(current_title or ""),
        "messages": [
            {"seq": int(seq), "text": str(text or "")}
            for seq, text in messages
        ],
    }
    if omitted > 0:
        payload["omittedMessages"] = int(omitted)
    return json.dumps(payload, ensure_ascii=False)


def _entry_bytes(seq: int, text: str) -> int:
    """单条消息在 JSON 载荷中的字节数（含分隔逗号）。"""
    return len(
        json.dumps(
            {"seq": int(seq), "text": str(text or "")}, ensure_ascii=False,
        ).encode("utf-8", "replace")
    ) + 1


def select_messages_within_budget(
    messages: Sequence[tuple[int, str]], max_bytes: int,
) -> tuple[list[tuple[int, str]], int]:
    """按「最新优先」裁剪消息，使 JSON 载荷不超过 ``max_bytes``。

    规则（用户需求 2026-10-20：所有用户消息参与、以最新为准）：

    - 从**最新**一条往前累计预算；放得下就保留，放不下就停止——更旧的
      消息被省略（数量由返回值给出），**绝不因为旧消息挤掉最新消息**；
    - 最新一条即使单独超预算也保留（文本按 UTF-8 安全截断到预算内），
      保证标题始终看得到最新诉求；
    - ``max_bytes <= 0`` 表示不限制（全量返回，``omitted=0``）。

    Args:
        messages: 按时间顺序的用户消息 ``[(seq, text), ...]``。
        max_bytes: 消息列表的字节预算（不含 system 指令与 JSON 外壳）。

    Returns:
        ``(保留的消息列表（保持原顺序）, 被省略的旧消息数)``。
    """
    items = [(int(seq), str(text or "")) for seq, text in messages]
    if not items:
        return [], 0
    budget = int(max_bytes)
    if budget <= 0:
        return items, 0

    used = 0
    start = len(items)
    for index in range(len(items) - 1, -1, -1):
        seq, text = items[index]
        cost = _entry_bytes(seq, text)
        if used + cost <= budget:
            used += cost
            start = index
            continue
        if start == len(items):
            # 最新一条单独超预算：截断文本（保留开头）后仍要包含它——
            # 「所有消息参与」在预算不足时的最低保证是「最新一定参与」。
            text_budget = max(16, budget - _entry_bytes(seq, ""))
            truncated = truncate_utf8(text, text_budget)
            if truncated:
                items[index] = (seq, truncated)
                start = index
        break
    return items[start:], start


def _request_total(
    system: str, selected: Sequence[tuple[int, str]],
    current_title: str | None, omitted: int,
) -> int:
    """完整请求（system + user）的 UTF-8 字节数。"""
    return request_bytes([
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": build_user_payload(selected, current_title, omitted=omitted),
        },
    ])


def build_title_messages(
    messages: Sequence[tuple[int, str]],
    current_title: str | None,
    *,
    target_words: int,
    target_cjk_characters: int,
    max_input_bytes: int = 0,
) -> list[dict]:
    """构建完整的标题请求消息列表（system + user）。

    Args:
        messages: 全部合格用户消息（按时间顺序）。
        current_title: 已接受的最新标题（仅作参考，不强制保留）。
        target_words / target_cjk_characters: 标题目标长度。
        max_input_bytes: 整个请求的字节预算（``0`` = 不限制）。超出预算时按
            **最新优先**裁剪：旧消息省略、最新消息必定保留（单独超预算时
            截断），system 指令与既有标题的开销已精确预留。

    Returns:
        ``[system, user]`` 两条消息。
    """
    system = build_system_prompt(
        target_words=target_words,
        target_cjk_characters=target_cjk_characters,
    )
    selected: list[tuple[int, str]] = list(messages)
    omitted = 0
    if max_input_bytes and int(max_input_bytes) > 0:
        limit = int(max_input_bytes)
        # 固定开销：system 内容、JSON 外壳、role 字段、既有标题。
        fixed = (
            len(system.encode("utf-8", "replace"))
            + len(sanitize_title(current_title or "").encode("utf-8", "replace"))
            + 160
        )
        selected, omitted = select_messages_within_budget(
            messages, max(64, limit - fixed),
        )
        # 估算与实际有几十字节偏差时继续收敛（从最旧丢起，至少保留最新一条）。
        while (
            len(selected) > 1
            and _request_total(system, selected, current_title, omitted) > limit
        ):
            selected = selected[1:]
            omitted += 1
        if selected and _request_total(system, selected, current_title, omitted) > limit:
            seq, text = selected[-1]
            tail_budget = max(
                8, limit - _request_total(system, [(seq, "")], current_title, omitted) - 8,
            )
            selected = [(seq, truncate_utf8(text, tail_budget))]
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": build_user_payload(selected, current_title, omitted=omitted),
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
    "select_messages_within_budget",
]
