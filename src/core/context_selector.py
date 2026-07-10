#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上下文消息选择模块

纯函数，无副作用。负责：
- 消息文本提取
- 上下文限制检测
- 压缩候选消息筛选（含工具组保护）
"""

import json
from functools import lru_cache
from .internal.shared._message_stats_cache import MessageStatsCache  # noqa: F401 — re-exported for backward compat

_EXCESS_BUFFER = 1.3  # 30% 超额释放缓冲，避免频繁触发压缩


# ── 单次遍历计算（避免重复全量遍历）───────────────────────

def compute_message_stats(messages):
    """单次遍历计算总字符数和总 token 数。"""
    total_chars_val = 0
    total_tokens_val = 0
    for m in messages:
        text = message_to_text(m)
        total_chars_val += len(text)
        from ..api.tokens import estimate_tokens
        total_tokens_val += estimate_tokens(text)
    return total_chars_val, total_tokens_val


def exceeds_limit_values(total_chars_val, total_tokens_val):
    """使用预计算的值检查是否超出限制。"""
    from ..config import MAX_CONTEXT_CHARS, MAX_CONTEXT_TOKENS  # 配置常量 — 函数体内延迟导入
    if MAX_CONTEXT_CHARS > 0 and total_chars_val > MAX_CONTEXT_CHARS:
        return True
    if MAX_CONTEXT_TOKENS > 0 and total_tokens_val > MAX_CONTEXT_TOKENS:
        return True
    return False


def should_auto_force_values(total_chars_val, total_tokens_val):
    """使用预计算的值检查是否应自动全量压缩。"""
    from ..config import AUTO_FORCE_COMPRESS_THRESHOLD, MAX_CONTEXT_TOKENS  # 配置常量 — 函数体内延迟导入
    if AUTO_FORCE_COMPRESS_THRESHOLD > 0:
        if total_chars_val > AUTO_FORCE_COMPRESS_THRESHOLD:
            return True
        # token 使用独立阈值估算（按 1 token ≈ 2 字符折算）
        TOKEN_FORCE_THRESHOLD = AUTO_FORCE_COMPRESS_THRESHOLD // 2
        if MAX_CONTEXT_TOKENS > 0 and total_tokens_val > TOKEN_FORCE_THRESHOLD:
            return True
    return False


def calc_excess_chars_values(total_chars_val, total_tokens_val):
    """使用预计算的值计算需要释放的字符数。"""
    from ..config import MAX_CONTEXT_CHARS, MAX_CONTEXT_TOKENS  # 配置常量 — 函数体内延迟导入
    char_excess = max(0, total_chars_val - MAX_CONTEXT_CHARS) if MAX_CONTEXT_CHARS > 0 else 0
    tok_excess_chars = 0
    if MAX_CONTEXT_TOKENS > 0:
        tok_excess = total_tokens_val - MAX_CONTEXT_TOKENS
        tok_excess_chars = int(tok_excess * 1.5) if tok_excess > 0 else 0
    return max(char_excess, tok_excess_chars)


def calc_usage_percent_values(total_chars_val):
    """使用预计算的值计算上下文使用百分比。"""
    from ..config import MAX_CONTEXT_CHARS  # 配置常量 — 函数体内延迟导入
    if MAX_CONTEXT_CHARS <= 0:
        return 0.0
    return total_chars_val / MAX_CONTEXT_CHARS * 100


# ── 消息文本提取 ──────────────────────────────────────────

@lru_cache(maxsize=256)
def _parse_tool_args(args_str):
    """缓存解析工具参数字符串，避免重复 json.loads 开销。"""
    try:
        return json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return None


def message_to_text(msg):
    """将消息转为纯文本表示，包括工具调用信息。"""
    role = msg.get("role", "")
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls")

    if role == "assistant" and tool_calls:
        parts = [content] if content else []
        for tc in tool_calls:
            func = tc.get("function") or tc
            name = func.get("name", "")
            args = func.get("arguments", "")
            if isinstance(args, str):
                parsed = _parse_tool_args(args)
                if parsed is not None:
                    args = parsed
            if isinstance(args, dict):
                args_str = json.dumps(args, ensure_ascii=False)[:100]
            else:
                args_str = str(args)[:100]
            parts.append(f"[调用工具 {name}({args_str})]")
        return " ".join(parts)

    if role == "tool":
        tool_id = msg.get("tool_call_id", "")
        return f"[工具结果 {tool_id[:12]}] {content}"

    return content


# ── 限制检测 ──────────────────────────────────────────────

def total_chars(messages):
    chars, _ = compute_message_stats(messages)
    return chars


def total_tokens(messages):
    _, tokens = compute_message_stats(messages)
    return tokens


# ── 工具调用组保护 ────────────────────────────────────────

def find_tool_groups(messages):
    """查找工具调用组：(assistant_with_tool_calls, tool, tool, ...)。
    返回 [(start, end), ...] 闭区间。"""
    groups = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            start = i
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool":
                i += 1
            groups.append((start, i - 1))
        else:
            i += 1
    return groups


def adjust_keep_for_tool_groups(messages, keep_recent=None):
    """如果工具调用组被 keep_recent 切分，扩大保留范围。"""
    if keep_recent is None:
        from ..config import KEEP_RECENT_MESSAGES  # 配置常量 — 函数体内延迟导入
        keep_recent = KEEP_RECENT_MESSAGES
    for start, end in find_tool_groups(messages):
        boundary = len(messages) - keep_recent
        if start < boundary <= end:
            keep_recent = max(keep_recent, len(messages) - start)
    return keep_recent


def extend_to_complete_tool_group(messages, indices):
    """如果压缩范围的末尾是带 tool_calls 的 assistant，追加后续 tool 消息。"""
    if not indices:
        return indices
    result = list(indices)
    last = result[-1]
    msg = messages[last]
    if msg.get("role") == "assistant" and msg.get("tool_calls"):
        for j in range(last + 1, len(messages)):
            if messages[j].get("role") == "tool":
                result.append(j)
            else:
                break
    # ★ 去重：candidates 中可能已包含 tool 消息索引
    seen = set()
    deduped = []
    for idx in result:
        if idx not in seen:
            seen.add(idx)
            deduped.append(idx)
    return deduped


# ── 消息选择 ──────────────────────────────────────────────

def select_candidates(messages, keep_recent):
    """选择可压缩的消息索引。

    规则：
    - 跳过 index 0（系统 prompt）
    - 跳过 pinned 消息
    - 跳过 system 消息（除旧摘要外）
    - 保留最近 keep_recent 条
    """
    boundary = max(1, len(messages) - keep_recent)
    candidates = []
    for i in range(1, boundary):
        msg = messages[i]
        if msg.get("pinned"):
            continue
        if msg.get("role") == "system" and not (msg.get("content") or "").startswith("[对话摘要]"):
            continue
        candidates.append(i)
    return candidates


def select_for_compression(messages, keep_recent=None, force=False, total_chars_val=None, total_tokens_val=None):
    """选择要压缩的消息索引列表。

    force=True: 压缩所有可压缩消息。
    force=False: 只压缩足够释放超出量的消息（从旧到新）。
    """
    if keep_recent is None:
        keep_recent = adjust_keep_for_tool_groups(messages)
    candidates = select_candidates(messages, keep_recent)

    if force:
        return extend_to_complete_tool_group(messages, candidates)

    if total_chars_val is not None or total_tokens_val is not None:
        excess = calc_excess_chars_values(total_chars_val or 0, total_tokens_val or 0)
    else:
        excess = calc_excess_chars_values(*compute_message_stats(messages))
    if excess <= 0:
        return []

    target = int(excess * _EXCESS_BUFFER)
    selected = []
    accumulated = 0
    for idx in candidates:
        selected.append(idx)
        accumulated += len(message_to_text(messages[idx]))
        if accumulated >= target:
            break

    return extend_to_complete_tool_group(messages, selected)
