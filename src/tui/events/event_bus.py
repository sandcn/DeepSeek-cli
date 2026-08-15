"""DisplayEventBus — 显示层事件总线（直接分发实现）。

自行实现线程安全的事件分发，移除对 CoreEventBus 的包装委托，
消除高频事件（ContentChunkEvent/ReasoningChunkEvent）的装箱/拆箱开销。

架构：
  - 按事件类型（DisplayEvent 子类）存储 handler 列表
  - subscribe() 支持按类型订阅或订阅所有（event_type=None）
  - publish() 直接调用 handler，异常隔离

设计原则：
  - 线程安全：RLock 保护 handler 注册表，异常隔离
  - 轻量无依赖：无需 CoreEventBus，自实现完整分发
  - 向后兼容：所有公开接口签名与旧版完全一致

2026-08-05 死代码清理：时间窗口批处理机制（``_TimeWindowBatcher`` /
``register_batched_event`` / ``unregister_batched_event`` / ``_batched_events``）
已删除——生产路径从未启用（批处理将「延迟分发的高频事件」与「同步直发的
阶段切换事件」的顺序竞态放大为固定窗口，导致推理文本静默丢失），保留属于
未启用死代码。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional, Type

from .event_types import DisplayEvent
from ..core.singleton import SingletonMeta

_logger = logging.getLogger(__name__)

#: L3（2026-08-15）：publish 异常日志降级 + 限频窗口（秒）——高频事件
#: （ContentChunk/ReasoningChunk）handler 持续异常时刷屏污染终端；窗口内
#: 同事件类型只记 1 条 warning（含栈），其余记 debug（含计数由 debug 条目
#: 承载）。不同事件类型独立限频（按 ``event_type.__name__`` 分桶）。
_EXC_LOG_WINDOW = 5.0
_last_exc_log: dict[str, float] = {}

EventHandler = Callable[[DisplayEvent], Any]


# ═══════════════════════════════════════════════════════════
# DisplayEventBus — 显示层事件总线
# ═══════════════════════════════════════════════════════════

class DisplayEventBus(metaclass=SingletonMeta):
    """显示层事件总线 — 同步发布/订阅（直接分发实现）。

    线程安全。支持按事件类型过滤订阅。
    单例行为由 ``SingletonMeta`` 自动提供 get_default / reset_default。

    单例作用域评估（2026-07-31 方向D）：
      CLI/WebUI 共享 DisplayEventBus 进程级单例为既有架构；webui bridge
      （_EVENT_BINDINGS 16 类）与 TUI 内部（_lifecycle/_subagent_panel/consumers）
      强依赖 get_default()，隔离改动过大 → 标记 P2 遗留，保持单例，不做隔离。
    """

    def __new__(cls):
        """拦截直接构造：返回进程级单例（与 get_default() 一致）。

        ★ P3-9：修复前 ``DisplayEventBus()`` 直接实例化可绕过单例——
        现在直接构造与 ``get_default()`` 等价（首次创建并缓存，后续返回
        既有实例）。注意：直接构造返回既有单例时 Python 仍会调用
        ``__init__``，由 ``__init__`` 幂等保护（已初始化则跳过），
        避免重置单例订阅状态。

        ★ P3-24（并发构造竞态，文档声明）：本路径无锁——直接构造
        （``DisplayEventBus()``）与 ``get_default()``（DCL）并发首次调用时
        可能重复创建实例（后写者覆盖，被丢弃实例的订阅状态丢失）。不并入
        ``_instance_lock`` 的原因：``SingletonMeta.get_default()`` 已持有
        ``_instance_lock``（threading.Lock 非重入）后经 ``cls()`` 进入本
        方法，此处再加锁会**死锁**。竞态窗口极小（仅首次构造；生产路径经
        get_default 已 DCL 保护，直接构造为测试/兼容场景），文档声明为
        已知边界。
        """
        inst = cls._instance
        if inst is not None:
            return inst
        inst = super().__new__(cls)
        cls._instance = inst
        return inst

    def __init__(self):
        # ★ 修复（P2-10）：__init__ 异常时回滚单例缓存——修复前 __new__ 已
        #   赋值 cls._instance，__init__ 抛异常会缓存半初始化单例（后续
        #   get_default 返回未初始化实例）；try/except 回滚 _instance=None，
        #   下次构造重新走完整初始化（仅当缓存的是本实例时回滚，不覆盖
        #   其他线程已缓存的单例）。
        try:
            if getattr(self, "_handlers", None) is not None:
                # 幂等保护：直接构造返回既有单例时不重置订阅状态（P3-9）
                return
            self._handlers: dict[type, list[EventHandler]] = {}
            self._all_handlers: list[EventHandler] = []
            self._lock = threading.RLock()
        except Exception:
            if type(self)._instance is self:
                type(self)._instance = None
            raise

    # 单例访问由 SingletonMeta 提供：
    #   DisplayEventBus.get_default() → 线程安全单例获取（DCL）
    #   DisplayEventBus.reset_default() → 线程安全单例重置（供测试使用）

    # ── 订阅管理 ────────────────────────────────────────

    def subscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """注册事件处理函数。

        Args:
            handler: 事件处理函数，接受 DisplayEvent 参数。
            event_type: 指定订阅的事件类型。None 表示订阅所有事件。

        ★ P3-6（基类订阅永不触发）：``publish`` 按 ``type(event)`` **精确匹配**
        ——订阅 ``event_type=DisplayEvent``（抽象基类）或其它基类事件类型时，
        子类事件不会被触发（``issubclass`` 校验通过但精确匹配不命中）。
        ``DisplayEvent`` 为抽象基类，请订阅具体子类型（如 ``ToolStartedEvent``）。

        ★ P3-8（同 handler 重复触发）：同一 handler 同时注册到全局
        （``event_type=None``）与特定类型时，发布该类型事件会**触发两次**
        ——这是显式注册语义（全局订阅 + 类型订阅是两条独立注册通道）；
        如需仅触发一次请勿同时注册。
        """
        if event_type is not None:
            if not issubclass(event_type, DisplayEvent):
                raise TypeError(f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}")
            with self._lock:
                handlers = self._handlers.setdefault(event_type, [])
                if handler not in handlers:
                    handlers.append(handler)
        else:
            with self._lock:
                if handler not in self._all_handlers:
                    self._all_handlers.append(handler)

    def unsubscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """移除事件处理函数。

        Args:
            handler: 之前注册的事件处理函数。
            event_type: 指定取消订阅的类型。None 表示从全局订阅中移除。

        Raises:
            TypeError: event_type 非 DisplayEvent 子类（与 subscribe 对齐校验，
                ★ P3-7）。
        """
        # P3-7：与 subscribe 对齐的类型校验（修复前 unsubscribe 无校验，
        # 非法 event_type 静默走 ``_handlers.get(event_type)`` 不报错）。
        if event_type is not None:
            if not issubclass(event_type, DisplayEvent):
                raise TypeError(f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}")
        with self._lock:
            if event_type is not None:
                handlers = self._handlers.get(event_type)
                if handlers and handler in handlers:
                    handlers.remove(handler)
                    if not handlers:
                        del self._handlers[event_type]
            else:
                if handler in self._all_handlers:
                    self._all_handlers.remove(handler)

    def clear(self) -> None:
        """清除所有订阅（全量重置，供测试隔离与整体 teardown）。

        与 stop() 不同（stop 不注销批处理以保持生命周期内稳定），
        clear() 为完整重置语义。批处理机制已随 2026-08-05 死代码清理移除。

        P2-7 审计：clear() 全量重置（订阅）仅供测试隔离/整体 teardown；
        **生产代码当前无调用方**（生产路径使用 subscribe/unsubscribe
        或 stop() 的生命周期语义）。
        """
        with self._lock:
            self._handlers.clear()
            self._all_handlers.clear()

    @property
    def subscriber_count(self) -> int:
        """获取当前订阅者总数。"""
        with self._lock:
            count = len(self._all_handlers)
            for handlers in self._handlers.values():
                count += len(handlers)
            return count

    # ── 发布 ────────────────────────────────────────────

    def publish(self, event: DisplayEvent) -> None:
        """同步发布事件到所有匹配的订阅者。

        Args:
            event: 要发布的事件对象。
        """
        event_type = type(event)
        # 收集目标 handler（在锁内快照，锁外调用避免死锁）
        targets: list[EventHandler] = []
        with self._lock:
            if event_type in self._handlers:
                targets.extend(self._handlers[event_type])
            targets.extend(self._all_handlers)
        for handler in targets:
            try:
                handler(event)
            except Exception:
                # L3（2026-08-15）：异常日志降级 + 按事件类型 5s 窗口限频——
                # 修复前 ``_logger.exception``（ERROR + 完整栈）每次异常都打，
                # 高频事件（ContentChunk/ReasoningChunk）handler 持续异常时
                # 刷屏污染终端。窗口内（now - last < 5.0）同事件类型只记
                # debug，窗口外记 1 条 warning（含完整栈）并更新时间戳；
                # 不同事件类型独立限频（按 ``event_type.__name__`` 分桶）。
                # 模块级 dict 无锁——GIL 原子读写，日志场景可接受（与项目
                # 其他限频模式一致）。
                handler_name = getattr(handler, "__name__", repr(handler))
                etype_name = event_type.__name__
                now = time.monotonic()
                if now - _last_exc_log.get(etype_name, 0.0) >= _EXC_LOG_WINDOW:
                    _last_exc_log[etype_name] = now
                    _logger.warning(
                        "事件处理函数 %s 处理 %s 时异常（5s 限频）",
                        handler_name, etype_name, exc_info=True,
                    )
                else:
                    _logger.debug(
                        "事件处理函数 %s 处理 %s 时异常（限频抑制）",
                        handler_name, etype_name,
                    )
