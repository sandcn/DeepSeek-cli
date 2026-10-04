"""消息文本提取 — 纯函数模块（零副作用）

将消息转为纯文本表示，供 token 统计与上下文选择使用。
从 ``context_selector.py`` 提取，作为单一真源，打破
``context_selector ↔ _message_stats_cache`` 循环依赖
（cache 直接依赖本模块，不再反向依赖 context_selector）。
"""

from __future__ import annotations

import json
from functools import lru_cache


@lru_cache(maxsize=256)
def _parse_tool_args(args_str):
    """缓存解析工具参数字符串，避免重复 json.loads 开销。"""
    try:
        return json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return None


def message_to_text(msg):
    """将消息转为纯文本表示，包括工具调用信息。

    content 可能为 str 或 list[dict]（多模态 content blocks，如
    image_url）——list 时用 content_to_text 提取文本部分，保证
    compute_message_stats 的 len/estimate_tokens 不因非 str 崩溃。
    """
    role = msg.get("role", "")
    content = msg.get("content") or ""
    # 多模态 content blocks（list[dict]，如 image_url）→ 先归一化为纯文本，
    # 避免 assistant+tool_calls 分支对 list join 抛 TypeError、tool 分支把
    # base64 data URI 以 list repr 灌入上下文统计。
    if isinstance(content, list):
        from ...multimodal import content_to_text
        content = content_to_text(content)
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
        prefix = f"[工具结果 {tool_id[:12]}]"
        # content 可能为空（多模态空 list 归一化为 ""）——不产生尾随空格
        return f"{prefix} {content}" if content else prefix

    return content


__all__ = ["message_to_text", "_parse_tool_args"]
