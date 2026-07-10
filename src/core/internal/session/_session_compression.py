"""session.py compress/async_compress 守卫逻辑提取。

将 `ChatSession.compress()` 和 `ChatSession.async_compress()` 中
重复的前置条件检查逻辑提取为单一函数，消除代码重复。
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def _validate_compress_preconditions(
    ctx_mgr: Any | None,
    messages: list[dict],
    min_non_system: int,
) -> bool:
    """验证上下文压缩的前置条件。

    检查：
    - ContextManager 是否已初始化
    - 非系统消息数量是否超过最小阈值

    Args:
        ctx_mgr: ContextManager 实例（可能为 None）
        messages: 当前会话的消息列表
        min_non_system: 非系统消息最小阈值

    Returns:
        True — 前置条件满足，可以执行压缩
        False — 前置条件不满足，跳过压缩
    """
    if ctx_mgr is None:
        _logger.warning("ContextManager 未初始化，无法压缩")
        return False

    non_system_count = sum(
        1 for m in messages
        if m.get("role") != "system"
        or (m.get("content") or "").startswith("[对话摘要]")
    )
    if non_system_count <= min_non_system:
        _logger.info("非系统消息太少（≤%d），无需压缩", min_non_system)
        return False

    return True
