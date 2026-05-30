"""模块级全局状态管理。

提供 _active_consumer / _active_parallel_display 全局引用，
供交互式终端工具（user_select/read_file 等）和并行面板驱动使用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._consumer import ChatUIConsumer
    from ..ui.parallel.display import ParallelDisplay

# ── 活跃实例引用（供交互式工具暂停/恢复） ────────────
_active_consumer: "ChatUIConsumer | None" = None

# ── 活跃 ParallelDisplay 引用（由 ParallelDisplay.start/stop 管理） ──
# 供 ChatUIConsumer._drain_queue 在每次渲染循环中驱动帧刷新，
# 取代 ParallelDisplay 原有的独立定时器机制。
_active_parallel_display: "ParallelDisplay | None" = None


def get_active_chat_ui() -> "ChatUIConsumer | None":
    """获取当前活跃的 ChatUIConsumer 实例，供交互式终端工具使用。

    user_select 等工具需要独占终端，通过此函数获取 ChatUIConsumer
    引用后可调用 suspend()/resume() 暂停/恢复后台渲染。
    """
    return _active_consumer
