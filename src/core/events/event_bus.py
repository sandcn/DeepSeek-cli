"""核心事件总线 — 通用事件发布/订阅系统

线程安全，支持通配符订阅和优先级排序。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from typing import Callable
from functools import wraps

from .event_types import CoreEvent, EventPriority

_logger = logging.getLogger(__name__)

# 非标准日志级别：TRACE = DEBUG - 5
_TRACE_LEVEL: int = logging.DEBUG - 5

# 处理器类型签名
EventHandler = Callable[[CoreEvent], None]

class _TimeWindowBatcher:
    """时间窗口批处理器

    在指定时间窗口(~33ms)内聚合高频事件，窗口结束时批量分发。
    使用 asyncio 定时器控制窗口边界，窗口内事件保持加入顺序。

    无运行中事件循环时自动降级为直接分发，不阻塞。
    """

    def __init__(
        self,
        bus: CoreEventBus,
        window_sec: float = 0.033,
        max_batch: int = 50,
    ):
        self._bus = bus
        self._window_sec = window_sec
        self._max_batch = max_batch
        self._buffer: list[CoreEvent] = []
        self._timer: asyncio.TimerHandle | None = None
        self._lock = threading.RLock()

    def add(self, event: CoreEvent) -> None:
        """将事件加入缓冲，首次加入时启动定时器"""
        with self._lock:
            self._buffer.append(event)
            if self._timer is None:
                self._start_timer()
            if len(self._buffer) >= self._max_batch:
                self._flush_locked()

    def flush(self) -> None:
        """强制刷新缓冲（供外部调用，如 ensure_cursor_in_lower 场景）"""
        with self._lock:
            self._flush_locked()

    def _start_timer(self) -> None:
        """启动 asyncio 定时器"""
        try:
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(self._window_sec, self._flush_callback)
        except RuntimeError:
            # 无运行中的事件循环 → 直接刷新（降级行为）
            self._flush_callback()

    def _flush_callback(self) -> None:
        """定时器触发的刷新回调"""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """持有锁时刷新缓冲：取出所有事件，在锁外逐个 dispatch"""
        self._timer = None
        if not self._buffer:
            return

        events = self._buffer
        self._buffer = []

        # 记录批处理统计（debug 级别）
        if len(events) > 1:
            _logger.debug(
                "时间窗口批处理刷新: batch_size=%d window_sec=%.3f",
                len(events), self._window_sec,
            )

        # 离开锁后逐个 dispatch（保持事件顺序，避免处理器死锁）
        for event in events:
            try:
                self._bus._dispatch(event)
            except Exception:
                _logger.exception(
                    "批处理事件分发异常: event_type=%s", event.event_type,
                )

    @property
    def pending_count(self) -> int:
        """缓冲中待处理的事件数量"""
        with self._lock:
            return len(self._buffer)

class CoreEventBus:
    """核心事件总线

    特性：
    - 线程安全（读写锁保护）
    - 通配符订阅（"model.*" 匹配 "model.call.completed"）
    - 优先级排序
    - 异常隔离（单个处理器异常不影响其他处理器）
    - 支持异步通知（通过 asyncio 事件循环）
    - 时间窗口批处理（高频事件 ~33ms 聚合）

    使用方式:
        bus = CoreEventBus()

        def on_model_call(event: CoreEvent):
            publish_output(f"模型调用完成: {event.data}", level="info")

        bus.subscribe("model.call.completed", on_model_call)
        bus.publish("model.call.completed", {"model": "deepseek", "tokens": 100})
    """

    def __init__(self):
        self._lock = threading.RLock()
        # event_type → [(priority, handler), ...] 按优先级降序
        self._handlers: dict[str, list[tuple[int, EventHandler]]] = defaultdict(list)
        self._stats: dict[str, int] = defaultdict(int)  # event_type → 发布计数
        # CoreHooks 兼容映射：on() 注册的原始 callback → 包装后 handler 映射
        # 此映射是永久设计而非临时方案——on() 需要将 cb(**data) 风格回调
        # 包装为 handler(event) 风格，映射使 off() 能反向查找并移除包装 handler，
        # 并使 __getitem__() 能将包装 handler 还原为原始 callback 返回。
        self._on_compat_map: dict[Callable, Callable] = {}
        # _dispatch 预计算缓存：event_type → [unique_handlers]
        # None 表示缓存未初始化/已失效，下次 _dispatch 时重新计算
        self._dispatch_cache: dict[str, list[EventHandler]] | None = None
        # 时间窗口批处理：需批处理的事件类型集合
        self._batched_events: set[str] = set()
        # 时间窗口批处理器实例（惰性初始化）
        self._batcher: _TimeWindowBatcher | None = None

    # ── 订阅 ────────────────────────────────────────────

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """订阅事件

        Args:
            event_type: 事件类型（支持通配符 "*" 和 "prefix.*" 模式）
            handler: 事件处理函数
            priority: 优先级，默认为 NORMAL
        """
        with self._lock:
            handlers = self._handlers[event_type]
            handlers.append((priority.value, handler))
            # 按优先级降序排列
            handlers.sort(key=lambda x: x[0], reverse=True)
            # 增量更新：清空预计算缓存，下次 _dispatch 重新计算
            self._dispatch_cache = None

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """取消订阅

        Returns:
            找到并移除返回 True，未找到返回 False
        """
        with self._lock:
            handlers = self._handlers.get(event_type)
            if not handlers:
                return False
            for i, (_, h) in enumerate(handlers):
                if h is handler:
                    handlers.pop(i)
                    # 增量更新：清空预计算缓存
                    self._dispatch_cache = None
                    return True
            return False

    # ── 发布 ────────────────────────────────────────────

    def publish(
        self,
        event_type: str,
        data: dict | None = None,
        source: str = "core",
        batch: bool | None = None,
    ) -> int:
        """发布事件

        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件来源
            batch: 是否使用时间窗口批处理。
                   None=根据 _batched_events 自动判断，
                   True=强制批处理，
                   False=强制直发。

        Returns:
            被调用的处理器数量（批处理模式返回 0）
        """
        # 自动检测是否需要批处理
        if batch is None:
            with self._lock:
                batch = self._should_batch(event_type)

        event = CoreEvent(
            event_type=event_type,
            data=data or {},
            source=source,
            timestamp=time.time(),
        )

        if batch:
            batcher = self._ensure_batcher()
            batcher.add(event)
            return 0  # 批处理模式下无法立即获知处理器数量
        else:
            return self._dispatch(event)

    # ── CoreHooks 兼容接口 ──────────────────────────────

    def on(self, event_type: str, callback: Callable) -> None:
        """注册事件回调（CoreHooks 兼容接口）。

        将 CoreHooks 风格的 `cb(**data)` 包装为 `handler(event)`。
        """
        @wraps(callback)
        def _handler(event: CoreEvent) -> None:
            callback(**event.data)

        # 存储包装引用，供 off() 移除时使用
        self._on_compat_map[callback] = _handler
        self.subscribe(event_type, _handler, EventPriority.NORMAL)

    def off(self, event_type: str, callback: Callable) -> None:
        """移除事件回调（CoreHooks 兼容接口）。"""
        wrapped = self._on_compat_map.pop(callback, callback)
        self.unsubscribe(event_type, wrapped)

    def _emit(self, event_type: str, **data) -> bool:
        """触发事件（CoreHooks 兼容接口）。"""
        self.publish(event_type, data)
        return True

    # ── 时间窗口批处理 ──────────────────────────────────

    def register_batched_event(self, event_type: str) -> None:
        """注册需要时间窗口批处理的事件类型

        注册后，该类型事件自动走 ~33ms 窗口批处理路径，
        降低高频事件的分发开销。

        Args:
            event_type: 事件类型字符串（如 "model.stream.chunk"）
        """
        with self._lock:
            self._batched_events.add(event_type)

    def unregister_batched_event(self, event_type: str) -> None:
        """取消事件类型的批处理注册"""
        with self._lock:
            self._batched_events.discard(event_type)

    def flush_batcher(self) -> None:
        """强制刷新批处理器缓冲（确保最终事件被分发）"""
        batcher = self._batcher
        if batcher is not None:
            batcher.flush()

    def _should_batch(self, event_type: str) -> bool:
        """判断事件类型是否需要时间窗口批处理（持有锁时调用）"""
        return event_type in self._batched_events

    def _ensure_batcher(self) -> _TimeWindowBatcher:
        """获取或创建时间窗口批处理器（线程安全，双检锁）

        Returns:
            _TimeWindowBatcher 实例
        """
        if self._batcher is None:
            with self._lock:
                if self._batcher is None:
                    self._batcher = _TimeWindowBatcher(self)
        return self._batcher

    # ── CoreHooks 兼容：dict-like 接口 ───────────────────

    def __getitem__(self, event: str) -> list[Callable]:
        """返回指定事件类型的处理器列表（CoreHooks 兼容）。

        对不存在的 event_type 返回 [] 而非抛 KeyError（与 defaultdict(list) 一致）。
        通过 on() 注册的包装 handler 会自动还原为原始 callback。

        注意：返回的 handler 列表是快照副本，不受后续 subscribe/unsubscribe 影响。
        """
        with self._lock:
            handlers = self._handlers.get(event, [])
            # 将包装 handler 还原为原始 callback（CoreHooks 兼容）
            compat_reverse = {v: k for k, v in self._on_compat_map.items()}
            return [compat_reverse.get(h, h) for _, h in handlers]

    def __contains__(self, event: str) -> bool:
        """检查事件类型是否有注册订阅。"""
        with self._lock:
            return event in self._handlers

    def __len__(self) -> int:
        """返回事件类型数量。"""
        with self._lock:
            return len(self._handlers)

    def __repr__(self) -> str:
        with self._lock:
            return f"CoreEventBus(types={len(self._handlers)}, subscribers={self.subscriber_count()})"

    def copy(self) -> CoreEventBus:
        """返回新 CoreEventBus 实例，_handlers 和 _stats 为深拷贝（回调引用共享）。

        用于测试场景中需要独立事件总线但共享回调引用的场景。
        批处理器状态不复制（新实例从头开始）。

        注意：返回的 handler 列表是快照副本，不受后续 subscribe/unsubscribe 影响。
        """
        import copy as _copy
        new_bus = CoreEventBus()
        with self._lock:
            new_bus._handlers = _copy.deepcopy(self._handlers)
            new_bus._stats = _copy.deepcopy(self._stats)
            new_bus._on_compat_map = _copy.deepcopy(self._on_compat_map)
            # 批处理状态不复制：新实例从头开始
        return new_bus

    def _dispatch(self, event: CoreEvent) -> int:
        """将事件分发给所有匹配的处理器

        使用预计算缓存优化性能：
        - 首次发布某事件类型时，计算结果并缓存
        - 后续同类型事件直接命中缓存，跳过通配符匹配
        - subscribe/unsubscribe 时缓存失效（全量清除）

        注意：返回的 handler 列表是快照副本，不受后续 subscribe/unsubscribe 影响。
        """
        count = 0
        with self._lock:
            self._stats[event.event_type] += 1

            # 尝试从预计算缓存获取
            cache = self._dispatch_cache
            try:
                if cache is not None and event.event_type in cache:
                    unique_handlers = cache[event.event_type]
                    if _logger.isEnabledFor(_TRACE_LEVEL):
                        _logger.debug(
                            "缓存命中: event_type=%s handlers=%d",
                            event.event_type, len(unique_handlers),
                        )
                else:
                    # 缓存未命中 — 执行完整匹配逻辑
                    if _logger.isEnabledFor(_TRACE_LEVEL):
                        _logger.debug(
                            "缓存未命中: event_type=%s cache=%s",
                            event.event_type, 'None' if cache is None else 'stale',
                        )

                    # 精确匹配 — 组内已按优先级降序
                    exact_matched = self._handlers.get(event.event_type, [])

                    # 通配符匹配: "model.*" 匹配 "model.call.completed"
                    wildcard_matched: list[tuple[int, EventHandler]] = []
                    for pattern, handlers in self._handlers.items():
                        if pattern.endswith("*") and not pattern.endswith("**"):
                            prefix = pattern[:-1]
                            if event.event_type.startswith(prefix):
                                wildcard_matched.extend(handlers)
                        elif pattern == "*":
                            wildcard_matched.extend(handlers)

                    # 通配符全局按优先级降序
                    wildcard_matched.sort(key=lambda x: x[0], reverse=True)

                    # 去重：精确优先于通配符（首现保留原则）
                    seen: set[EventHandler] = set()
                    unique_handlers: list[EventHandler] = []
                    for _, h in exact_matched:
                        if h not in seen:
                            seen.add(h)
                            unique_handlers.append(h)
                    for _, h in wildcard_matched:
                        if h not in seen:
                            seen.add(h)
                            unique_handlers.append(h)

                    # 写入缓存（惰性初始化）
                    if cache is None:
                        self._dispatch_cache = {}
                    self._dispatch_cache[event.event_type] = unique_handlers
            except (TypeError, KeyError) as cache_err:
                # 缓存数据损坏/类型错误时降级：清空缓存，从原始 handlers 执行完整匹配
                _logger.warning(
                    "缓存异常，降级到原始分发: event_type=%s error=%s",
                    event.event_type, cache_err,
                )
                self._dispatch_cache = None
                # 降级路径：执行完整的通配符匹配逻辑（复用缓存未命中逻辑）
                exact_matched = self._handlers.get(event.event_type, [])
                wildcard_matched: list[tuple[int, EventHandler]] = []
                for pattern, handlers in self._handlers.items():
                    if pattern.endswith("*") and not pattern.endswith("**"):
                        prefix = pattern[:-1]
                        if event.event_type.startswith(prefix):
                            wildcard_matched.extend(handlers)
                    elif pattern == "*":
                        wildcard_matched.extend(handlers)
                wildcard_matched.sort(key=lambda x: x[0], reverse=True)
                seen: set[EventHandler] = set()
                unique_handlers: list[EventHandler] = []
                for _, h in exact_matched:
                    if h not in seen:
                        seen.add(h)
                        unique_handlers.append(h)
                for _, h in wildcard_matched:
                    if h not in seen:
                        seen.add(h)
                        unique_handlers.append(h)

        # 在锁外分发，避免处理器死锁
        for handler in unique_handlers:
            try:
                handler(event)
                count += 1
            except Exception:
                _logger.exception(
                    "事件处理器异常: event_type=%s handler=%s",
                    event.event_type, handler.__name__,
                )
        return count

    # ── 工具方法 ────────────────────────────────────────

    def clear(self) -> None:
        """清空所有订阅、统计、预计算缓存和批处理状态"""
        with self._lock:
            self._handlers.clear()
            self._stats.clear()
            self._dispatch_cache = None
            self._batched_events.clear()
            self._batcher = None

    def get_stats(self) -> dict[str, int]:
        """获取各事件类型的发布计数"""
        with self._lock:
            return dict(self._stats)

    def subscriber_count(self, event_type: str | None = None) -> int:
        """获取订阅者数量"""
        with self._lock:
            if event_type:
                return len(self._handlers.get(event_type, []))
            return sum(len(h) for h in self._handlers.values())

# ── 模块级全局单例 ──────────────────────────────────────

_default_bus: CoreEventBus | None = None
_bus_lock = threading.RLock()

def get_default_bus() -> CoreEventBus:
    """获取全局默认事件总线（线程安全单例）"""
    global _default_bus
    if _default_bus is None:
        with _bus_lock:
            if _default_bus is None:
                _default_bus = CoreEventBus()
    return _default_bus

def set_default_bus(bus: CoreEventBus) -> None:
    """设置全局默认事件总线（用于测试/依赖注入）"""
    global _default_bus
    with _bus_lock:
        _default_bus = bus

def reset_default_bus() -> None:
    """重置全局默认事件总线（主要用于测试）"""
    global _default_bus
    with _bus_lock:
        _default_bus = None