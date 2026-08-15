"""chat_ui 全局状态模块 — 活跃实例引用 + 引用计数管理。

Layer 0 — 仅依赖 typing，被 _consumer + 外部调用方引用。
注意：线程本地重入保护（_handler_reentrant）定义在 _error_handler.py。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary, ref as _weakref

if TYPE_CHECKING:
    from .._consumer import ChatUIConsumer

# ── 活跃实例引用（供交互式工具暂停/恢复，引用计数防竞态） ──
# 多实例场景下，start() 递增计数并设置引用，stop() 递减计数，
# 仅归零时清空引用。防止 A.stop() 误清 B 的活跃引用。
_active_consumer: "ChatUIConsumer | None" = None
_active_consumer_refcount: int = 0
_state_global_lock = threading.Lock()

# 弱引用注册表：以线程 ID 为键，在 _active_consumer 悬空时提供兜底恢复
_weak_consumer_registry: "WeakValueDictionary[int, ChatUIConsumer]" = WeakValueDictionary()

#: 按注册顺序记录活跃实例的弱引用栈（P1-1 多实例回滚用）。
#: 弱引用防止阻止实例回收；栈顶为最近注册实例。
#: 与 refcount 的对应关系：每个活跃（started）实例入栈一次，stop 时经
#: ``_latest_started_consumer`` 惰性清理已停止/已回收引用。
_active_consumer_stack: list = []

def get_active_chat_ui() -> "ChatUIConsumer | None":
    """获取当前活跃的 ChatUIConsumer 实例，供交互式终端工具使用。

    user_select 等工具需要独占终端，通过此函数获取 ChatUIConsumer
    引用后可调用 suspend()/resume() 暂停/恢复后台渲染。

    若 _active_consumer 已被清空（引用计数归零），
    尝试从 _weak_consumer_registry 恢复当前线程的 consumer。
    """
    if _active_consumer is not None:
        return _active_consumer
    # 兜底：从弱引用注册表恢复（覆盖引用计数悬空场景）
    return _weak_consumer_registry.get(threading.get_ident())


def _is_started(consumer) -> bool:
    """判断 consumer 实例是否仍活跃（started）。

    P1-1 辅助：真实实例经 ``_started`` property 读取 lifecycle 状态
    （``ChatUIConsumer._started`` → ``_lifecycle.is_started`` bool）。
    mock 场景（无 _started 或调用异常）视为活跃（兼容测试桩——mock
    不真实启动，回滚逻辑在 mock 场景不触发）。
    """
    try:
        return bool(getattr(consumer, "_started", False))
    except Exception:
        return True


def _latest_started_consumer() -> "ChatUIConsumer | None":
    """返回注册栈中最近一个仍活跃的实例，无则 None。

    P1-1 辅助：从栈顶（最近注册）往回遍历，跳过已停止/已回收实例；
    同时惰性清理栈中已停止/已回收引用（栈仅保留活跃实例，防无限增长）。
    """
    _active_consumer_stack[:] = [
        ref for ref in _active_consumer_stack
        if ref() is not None and _is_started(ref())
    ]
    if not _active_consumer_stack:
        return None
    return _active_consumer_stack[-1]()


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
        _active_consumer_stack.append(_weakref(consumer))
        _weak_consumer_registry[threading.get_ident()] = consumer


def _unregister_consumer() -> None:
    """注销活跃 ChatUIConsumer 实例（引用计数 -1）。

    引用计数归零时清空 _active_consumer。
    包含 try/except TypeError 兼容测试 mock 场景（MagicMock 不支持 int 运算/比较）。

    stop() 中调用此函数替代直接操作 _active_consumer_refcount。
    """
    with _state_global_lock:
        global _active_consumer, _active_consumer_refcount
        try:
            # ★ P2-3：下限保护——引用计数不可为负（防御重复注销/计数漂移，
            #   修复前 ``-= 1`` 可减到负数导致 _active_consumer 永不归零清空）。
            _active_consumer_refcount = max(0, _active_consumer_refcount - 1)
            # 内部断言：递减后引用计数必须非负（防御性，正常路径恒成立）
            assert _active_consumer_refcount >= 0
            if _active_consumer_refcount <= 0:
                _active_consumer = None
            else:
                # ★ P1-1：refcount>0 时回滚 _active_consumer 到最近一个仍
                #   活跃的实例——修复前「后注册者恒胜」：A.start → B.start →
                #   B.stop 后 refcount=1 但 active 仍为已停止的 B，
                #   get_active_chat_ui() 返回已停止实例。
                _active_consumer = _latest_started_consumer()
        except TypeError:
            # ★ P3-1：整个「递减+比较」移入 try（修复前仅 ``<=`` 比较在
            #   try 内，``-= 1`` 在 try 外——mock 场景递减即抛异常）——
            #   兼容测试 mock 场景（MagicMock 不支持 int 运算/比较），直接清空。
            _active_consumer = None
        _weak_consumer_registry.pop(threading.get_ident(), None)
