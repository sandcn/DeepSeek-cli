"""保留尾部范围选择 — 对齐 dsh ``compaction-basic/region``。

从「第一条非系统消息」开始、在保留近期逐字尾部的前提下选出最旧的
可压缩范围；范围边界必须保持工具调用/结果配对平衡，绝不切断一个步骤。

- 系统提示词（开头连续的 system 消息）永不进入范围；
- 从后往前累加 token 直到达到 ``retain_tokens`` 预算，得到候选边界；
- 边界收缩到「范围内工具调用与工具结果一一配对」为止。
"""

from __future__ import annotations

from .checkpoint import is_legacy_summary_message


def first_compactable_index(messages: list) -> int:
    """返回第一条可压缩消息的索引（跳过开头的系统提示词消息）。

    顶部连续的非摘要 ``system`` 消息是系统提词（可能多个 parts），
    永远不进入压缩范围；旧版摘要（``[对话摘要]`` system 消息）属于历史，
    可以再次被压缩合并。
    """
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        if (isinstance(message, dict) and message.get("role") == "system"
                and not is_legacy_summary_message(message)):
            index += 1
            continue
        break
    return index


def _collect_pairing(messages: list, start: int, end: int) -> tuple[set, set]:
    """收集 [start, end] 范围内的工具调用 id 与工具结果 id。"""
    call_ids: set = set()
    result_ids: set = set()
    for index in range(start, end + 1):
        message = messages[index]
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant" and message.get("tool_calls"):
            for call in message.get("tool_calls") or ():
                call_id = (call.get("id") if isinstance(call, dict) else None)
                if call_id:
                    call_ids.add(call_id)
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if call_id:
                result_ids.add(call_id)
    return call_ids, result_ids


def range_is_balanced(messages: list, start: int, end: int) -> bool:
    """范围内每条工具调用都有对应结果、每条结果都有对应调用。"""
    if start > end:
        return False
    call_ids, result_ids = _collect_pairing(messages, start, end)
    return call_ids == result_ids


def select_compactable_range(messages: list, retain_tokens: int, tokens_of) -> tuple[int, int] | None:
    """选出可压缩的包含区间 ``(start, end)``；无可安全压缩范围返回 None。

    Args:
        messages: 消息列表。
        retain_tokens: 逐字保留的近期尾部 token 预算（0 = 尽量多压缩）。
        tokens_of: 回调 ``idx -> int``，返回该消息的估算 token。

    Returns:
        ``(start, end)`` 闭区间，或 None。
    """
    total = len(messages)
    if total == 0:
        return None
    first = first_compactable_index(messages)
    if first >= total:
        return None

    accumulated = 0
    keep_from = total
    for index in range(total - 1, first - 1, -1):
        accumulated += max(0, int(tokens_of(index) or 0))
        keep_from = index
        if accumulated >= retain_tokens:
            break

    end = keep_from - 1
    # 保护 pinned 消息（项目特性：/pin 标记的重要消息压缩时保留）——
    # 范围不得越过第一条 pinned 消息。
    for index in range(first, end + 1):
        message = messages[index]
        if isinstance(message, dict) and message.get("pinned"):
            end = index - 1
            break
    while end >= first and not range_is_balanced(messages, first, end):
        end -= 1
    if end < first:
        return None
    return (first, end)


__all__ = [
    "first_compactable_index",
    "range_is_balanced",
    "select_compactable_range",
]
