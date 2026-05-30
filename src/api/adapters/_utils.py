"""适配器层公共工具函数"""
from __future__ import annotations

import logging
from typing import Optional

# ── 推理模型名称匹配模式 ──────────────────────────────────
_REASONER_PATTERNS: frozenset[str] = frozenset({"reasoner"})


def ensure_reasoning_content(messages: list, model: Optional[str] = None) -> list:
    """确保消息列表中所有 assistant 消息的 reasoning_content 字段正确。

    这是网络边界处的最终防御层——无论上游代码是否正确设置了
    reasoning_content，消息在发往 API 前都会被修复。

    DeepSeek V4 thinking mode 的推理内容校验规则（2026-04-30 最终修正）：
    - **所有** assistant 消息的 `reasoning_content` key **必须存在**
    - `reasoning_content` 值必须是字符串类型
    - 不含 tool_calls 的消息：`reasoning_content` 可以是空字符串
    - 含 tool_calls 的消息：`reasoning_content` 也可以是空字符串
    """
    _log = logging.getLogger(__name__)
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        rc = msg.get("reasoning_content")
        if "reasoning_content" not in msg:
            msg["reasoning_content"] = ""
            _log.debug("assistant[%d] 缺失 reasoning_content，已补空字符串", i)
        elif not isinstance(rc, str):
            msg["reasoning_content"] = ""
            _log.debug("assistant[%d] reasoning_content 修复: %s → ''",
                       i, type(rc).__name__)
    return messages
