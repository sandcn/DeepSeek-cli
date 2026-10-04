"""内核事件系统 — 支持 Cordis 的五种分发模式。

分发模式语义（引用 DeepSeek Harness / Cordis 的公开约定）：

- ``emit``      监听器按注册顺序观察，不等待，无返回值；
- ``waterfall`` 环绕中间件，监听器签名 ``(…args, next)``；调用 ``next()``
                执行下游，返回值可包装后继续向外返回；不调用 ``next()``
                直接返回即短路；
- ``parallel``  所有监听器并行执行并等待；
- ``serial``    监听器按注册顺序串行执行并等待，返回最后一个返回值；
- ``bail``      监听器按注册顺序执行，遇到首个非 ``None`` 返回值即停止并返回。

监听器可以是同步函数（任意分发模式均可），也可以是协程函数。
``emit`` 不 await：协程监听器在存在运行中事件循环时被调度为后台任务。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable

_logger = logging.getLogger(__name__)

Listener = Callable[..., Any]


class DispatchMode:
    """事件分发模式常量。"""

    EMIT = "emit"
    WATERFALL = "waterfall"
    PARALLEL = "parallel"
    SERIAL = "serial"
    BAIL = "bail"

    ALL = (EMIT, WATERFALL, PARALLEL, SERIAL, BAIL)


async def _invoke(handler: Listener, args: tuple) -> Any:
    """调用监听器并在其返回协程时等待。"""
    result = handler(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


class EventBus:
    """进程内事件总线（内核层，零业务依赖）。

    同一事件名可以注册任意数量的监听器；``on`` 返回一个 disposer，
    调用即注销（供 ``Context.effect`` 与 Fiber 生命周期使用）。
    """

    def __init__(self) -> None:
        # event -> [(order, priority, prepend, handler)]
        self._listeners: dict[str, list[tuple[int, int, bool, Listener]]] = defaultdict(list)
        self._registration_order = 0

    # ── 订阅 ─────────────────────────────────────────────

    def on(
        self,
        event: str,
        handler: Listener,
        *,
        prepend: bool = False,
        priority: int = 0,
    ) -> Callable[[], None]:
        """注册监听器，返回 disposer。"""
        if not callable(handler):
            raise TypeError(f"事件监听器必须可调用: {handler!r}")
        self._registration_order += 1
        order = self._registration_order
        entry = (order, priority, bool(prepend), handler)
        handlers = self._listeners[event]
        handlers.append(entry)
        # priority 降序 → 大的先执行；同 priority：prepend 先，其次按注册顺序。
        handlers.sort(key=lambda item: (-item[1], 0 if item[2] else 1, item[0]))
        removed = False

        def _dispose() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            try:
                self._listeners[event].remove(entry)
            except ValueError:
                pass
            if not self._listeners[event]:
                self._listeners.pop(event, None)

        return _dispose

    def off(self, event: str, handler: Listener) -> bool:
        """注销监听器（按身份匹配），返回是否移除。"""
        handlers = self._listeners.get(event)
        if not handlers:
            return False
        for entry in list(handlers):
            if entry[3] is handler:
                handlers.remove(entry)
                if not handlers:
                    self._listeners.pop(event, None)
                return True
        return False

    def listeners(self, event: str) -> list[Listener]:
        """返回事件监听器快照（按执行顺序）。"""
        return [entry[3] for entry in self._listeners.get(event, ())]

    def listener_count(self, event: str) -> int:
        return len(self._listeners.get(event, ()))

    def clear(self) -> None:
        self._listeners.clear()

    # ── 分发 ─────────────────────────────────────────────

    async def emit(self, event: str, *args: Any) -> None:
        """emit 模式：依次触发监听器，不等待协程监听器、忽略返回值。"""
        handlers = list(self.listeners(event))
        if not handlers:
            return
        loop = _running_loop()
        for handler in handlers:
            try:
                result = handler(*args)
                if inspect.isawaitable(result):
                    if loop is not None:
                        loop.create_task(_await_quietly(result))
                    else:
                        _logger.debug(
                            "emit(%s): 无运行事件循环，跳过协程监听器 %s",
                            event, getattr(handler, "__name__", handler),
                        )
            except Exception:
                _logger.exception("emit 监听器异常: event=%s", event)

    async def parallel(self, event: str, *args: Any) -> None:
        """parallel 模式：并行执行全部监听器并等待。"""
        handlers = list(self.listeners(event))
        if not handlers:
            return
        results = await asyncio.gather(
            *(_invoke(h, args) for h in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                _logger.exception(
                    "parallel 监听器异常: event=%s", event,
                    exc_info=result,
                )

    async def serial(self, event: str, *args: Any) -> Any:
        """serial 模式：按注册顺序串行执行，返回最后一个返回值。"""
        result: Any = None
        for handler in list(self.listeners(event)):
            result = await _invoke(handler, args)
        return result

    async def bail(self, event: str, *args: Any) -> Any:
        """bail 模式：顺序执行，遇到首个非 None 返回值即停止并返回。"""
        for handler in list(self.listeners(event)):
            result = await _invoke(handler, args)
            if result is not None:
                return result
        return None

    async def waterfall(self, event: str, *args: Any) -> Any:
        """waterfall 模式：环绕中间件。

        监听器签名 ``(…args, next)``；调用 ``await next()`` 执行下游，
        下游返回值经 ``next()`` 返回当前层，可被包装后继续向外返回；
        不调用 ``next()`` 直接返回即短路。
        """
        handlers = list(self.listeners(event))

        async def _run(index: int) -> Any:
            if index >= len(handlers):
                return None
            handler = handlers[index]

            async def _next() -> Any:
                return await _run(index + 1)

            result = handler(*args, _next)
            if inspect.isawaitable(result):
                result = await result
            return result

        if not handlers:
            return None
        return await _run(0)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


async def _await_quietly(awaitable: Any) -> None:
    try:
        await awaitable
    except Exception:
        _logger.exception("emit 协程监听器异常")


__all__ = ["DispatchMode", "EventBus", "Listener"]
