"""chat_ui 全局状态模块 — 活跃实例引用。

Layer 0 — 仅依赖 typing，被 _error_handler + _consumer + 外部调用方引用。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.parallel.display import ParallelDisplay
    from ._consumer import ChatUIConsumer

# ── 活跃实例引用（供交互式工具暂停/恢复） ────────────
_active_consumer: "ChatUIConsumer | None" = None

# ── 活跃 ParallelDisplay 引用（由 ParallelDisplay.start/stop 管理） ──
# 供 ChatUIConsumer._drain_queue 在每次渲染循环中驱动帧刷新，
# 取代 ParallelDisplay 原有的独立定时器机制。
_active_parallel_display: "ParallelDisplay | None" = None

# ── 线程本地重入保护（防止 emit → logger → emit 递归） ──
_handler_reentrant = threading.local()


def get_active_chat_ui() -> "ChatUIConsumer | None":
    """获取当前活跃的 ChatUIConsumer 实例，供交互式终端工具使用。

    user_select 等工具需要独占终端，通过此函数获取 ChatUIConsumer
    引用后可调用 suspend()/resume() 暂停/恢复后台渲染。
    """
    return _active_consumer
