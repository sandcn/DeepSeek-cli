"""chat_ui 全局状态模块 — 活跃实例引用 + 引用计数管理。

Layer 0 — 仅依赖 typing，被 _consumer + 外部调用方引用。
注意：线程本地重入保护（_handler_reentrant）定义在 _error_handler.py。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._tui import ChatUIConsumer

# ── 活跃实例引用（供交互式工具暂停/恢复，引用计数防竞态） ──
# 多实例场景下，start() 递增计数并设置引用，stop() 递减计数，
# 仅归零时清空引用。防止 A.stop() 误清 B 的活跃引用。
_active_consumer: "ChatUIConsumer | None" = None
_active_consumer_refcount: int = 0
_state_global_lock = threading.Lock()

def get_active_chat_ui() -> "ChatUIConsumer | None":
    """获取当前活跃的 ChatUIConsumer 实例，供交互式终端工具使用。

    user_select 等工具需要独占终端，通过此函数获取 ChatUIConsumer
    引用后可调用 suspend()/resume() 暂停/恢复后台渲染。
    """
    return _active_consumer


# ── 引用计数管理（封装 start()/stop() 中对全局变量的操作） ──

def _register_consumer(consumer: "ChatUIConsumer") -> None:
    """注册活跃 ChatUIConsumer 实例（引用计数 +1）。

    多实例场景下，递增引用计数并设置引用。
    start() 中调用此函数替代直接操作 _active_consumer_refcount。
    """
    with _state_global_lock:
        global _active_consumer, _active_consumer_refcount
        _active_consumer_refcount += 1
        _active_consumer = consumer


def _unregister_consumer() -> None:
    """注销活跃 ChatUIConsumer 实例（引用计数 -1）。

    引用计数归零时清空 _active_consumer。
    包含 try/except TypeError 兼容测试 mock 场景（MagicMock 不支持 int 比较）。

    stop() 中调用此函数替代直接操作 _active_consumer_refcount。
    """
    with _state_global_lock:
        global _active_consumer, _active_consumer_refcount
        _active_consumer_refcount -= 1
        try:
            if _active_consumer_refcount <= 0:
                _active_consumer = None
        except TypeError:
            # 兼容测试 mock 场景：MagicMock 不支持 int <= 比较，直接清空
            _active_consumer = None
