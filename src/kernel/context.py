"""Context — 服务容器与插件上下文。

参考 Cordis 的核心概念：

- 插件是实现 ``Service`` 的对象；上下文是服务的容器，服务占据一个稳定的
  ``ctx.<key>``（如 ``ctx.tools``、``ctx.llm``、``ctx.sessions``）；
- 其他插件通过 key 查找服务，而非导入具体实现；
- 通过 ``inject`` 声明服务依赖，加载顺序由服务依赖表达；
- 注册是可逆的副作用：``ctx.on()`` / ``ctx.effect()`` / ``ctx.provide()``
  在 reload / teardown 时按预期撤销；
- ``ctx.plugin()`` 创建子 Fiber，继承父上下文但有独立生命周期。

空间可组合性（Cordis 语义）：

- ``ctx.extend()``  派生一个**作用域子上下文**：继承父级解析，可在本地
  覆盖服务而不污染父级；
- ``ctx.isolate(name)`` 派生一个**隔离子上下文**：对 ``name`` 只解析本地
  提供，不穿透到父级/内核（每个会话可以挂各自实现而不互相污染）；
- ``ctx.intercept(name, callback)`` 派生一个**拦截子上下文**：解析 ``name``
  时对结果应用回调（可包装/替换），用于按作用域改写能力。

作用域子上下文中的 ``provide`` 落在本地作用域；同一作用域重复提供同名服务
抛 ``ServiceExists``（「一个能力同一上下文只允许一个实现」）。根上下文与
Fiber 上下文保持既有全局栈语义（提供覆盖），向后兼容。

命名注意：``ctx.<key>`` 同时用于属性式服务解析与上下文自身成员。上下文自带
成员（``config``、``kernel``、``fiber``、``parent``、``name``、``service``、
``provide``、``effect``、``on``、``emit``、``plugin`` 等）优先；当服务 key
与上下文成员同名（例如内置的 ``config`` 服务）时，请用
``ctx.service("config")`` / ``ctx.consume("config")`` 显式解析。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, List, Optional

from .errors import ServiceExists, ServiceNotFound
from .plugin import Plugin, as_plugin

_logger = logging.getLogger(__name__)


class Context:
    """插件上下文 — 服务容器 + 可逆副作用注册点。"""

    def __init__(
        self,
        kernel: Any,
        fiber: Any = None,
        parent: Optional["Context"] = None,
        config: Optional[dict] = None,
        *,
        scoped: bool = False,
        isolated: Optional[set] = None,
        interceptors: Optional[List[tuple]] = None,
    ) -> None:
        self.__dict__["_kernel"] = kernel
        self.__dict__["_fiber"] = fiber
        self.__dict__["_parent"] = parent
        self.__dict__["_config"] = dict(config or {})
        self.__dict__["_extra_inject"] = []
        # 作用域状态
        self.__dict__["_scoped"] = bool(scoped)
        self.__dict__["_local"]: dict[str, Any] = {}
        self.__dict__["_isolated"]: set = set(isolated or ())
        self.__dict__["_interceptors"]: List[tuple] = list(interceptors or ())

    # ── 基本属性 ─────────────────────────────────────────

    @property
    def kernel(self) -> Any:
        return self._kernel

    @property
    def fiber(self) -> Any:
        return self._fiber

    @property
    def parent(self) -> Optional["Context"]:
        return self._parent

    @property
    def config(self) -> dict:
        return self._config

    @property
    def name(self) -> str:
        return self._fiber.name if self._fiber is not None else "root"

    @property
    def scoped(self) -> bool:
        """是否作用域上下文（extend/isolate/intercept 派生）。"""
        return self._scoped

    # ── 服务容器 ─────────────────────────────────────────

    def provide(self, key: str, value: Any) -> Callable[[], None]:
        """注册服务（占据 ``ctx.<key>``），返回撤销函数。

        作用域上下文（extend/isolate 派生）注册到本地作用域，重复注册抛
        ``ServiceExists``；根/Fiber 上下文注册到内核全局栈（向后兼容）。
        """
        if not isinstance(key, str) or not key:
            raise ValueError(f"服务 key 必须是非空字符串: {key!r}")

        if self._scoped:
            if key in self._local:
                raise ServiceExists(key)
            self._local[key] = value
            released = False

            def _release_local() -> None:
                nonlocal released
                if released:
                    return
                released = True
                self._local.pop(key, None)

            if self._fiber is not None:
                self._fiber.add_disposer(_release_local)
            return _release_local

        self._kernel._acquire_service(key, value, self._fiber)
        if self._fiber is not None:
            self._fiber.register_provide_keys([key])

        released = False

        def _release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._kernel._release_service(key, value)

        if self._fiber is not None:
            self._fiber.add_disposer(_release)
        return _release

    def service(self, key: str, default: Any = None) -> Any:
        """按 key 解析服务（含作用域/拦截器），缺失时返回默认值。"""
        value, found = self._resolve(key)
        return default if not found else value

    def consume(self, key: str) -> Any:
        """按 key 解析服务（含作用域/拦截器），缺失时抛 ``ServiceNotFound``。"""
        value, found = self._resolve(key)
        if not found:
            raise ServiceNotFound(key)
        return value

    def has(self, key: str) -> bool:
        _, found = self._resolve(key)
        return found

    # ── 空间可组合性 ─────────────────────────────────────

    def extend(self) -> "Context":
        """派生作用域子上下文：继承父级解析，可在本地覆盖服务。"""
        return self._derive(scoped=True)

    def isolate(self, name: str) -> "Context":
        """派生隔离子上下文：``name`` 只解析本地提供，不穿透父级/内核。"""
        if not isinstance(name, str) or not name:
            raise ValueError(f"isolate 的服务 key 必须是非空字符串: {name!r}")
        child = self._derive(scoped=True, isolated={name})
        return child

    def intercept(self, name: str, callback: Callable[[Any], Any]) -> "Context":
        """派生拦截子上下文：解析 ``name`` 时对结果应用 callback。"""
        if not callable(callback):
            raise TypeError(f"intercept 回调必须可调用: {callback!r}")
        return self._derive(scoped=True, interceptors=[(name, callback)])

    def _derive(
        self,
        *,
        scoped: bool,
        isolated: Optional[set] = None,
        interceptors: Optional[List[tuple]] = None,
    ) -> "Context":
        child = Context(
            self._kernel,
            fiber=self._fiber,
            parent=self,
            config=self._config,
            scoped=scoped,
            isolated=isolated,
            interceptors=interceptors,
        )
        if self._fiber is not None:
            self._fiber.add_disposer(lambda: self._detach_child(child))
        return child

    def _detach_child(self, child: "Context") -> None:
        child._local.clear()

    def inject(self, *keys: str) -> None:
        """动态声明服务依赖（触发依赖重规划）。"""
        if self._fiber is not None:
            for key in keys:
                if key not in self._fiber._extra_inject:
                    self._fiber._extra_inject.append(key)
        else:
            for key in keys:
                if key not in self._extra_inject:
                    self._extra_inject.append(key)
        self._kernel._request_replan()

    # ── 可逆副作用 ───────────────────────────────────────

    def effect(self, callback: Callable[[], Any]) -> Callable[[], Any]:
        """执行 callback 并登记其返回的 disposer；卸载时撤销。

        callback 可以返回：

        - 一个同步/异步清理函数；
        - 一个 awaitable，其解析结果为清理函数；
        - ``None``（无清理）。
        """
        try:
            result = callback()
        except Exception:
            _logger.exception("effect 回调执行失败: %s", self.name)
            raise

        disposer = _make_disposer(result)
        if self._fiber is not None:
            return self._fiber.owned_disposer(disposer)
        return disposer

    def on(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        prepend: bool = False,
        priority: int = 0,
    ) -> Callable[[], None]:
        """注册事件监听；卸载时自动移除。"""
        remove = self._kernel.bus.on(
            event, handler, prepend=prepend, priority=priority
        )
        if self._fiber is not None:
            return self._fiber.owned_disposer(remove)
        return remove

    # ── 事件分发 ─────────────────────────────────────────

    async def emit(self, event: str, *args: Any) -> None:
        await self._kernel.bus.emit(event, *args)

    async def parallel(self, event: str, *args: Any) -> None:
        await self._kernel.bus.parallel(event, *args)

    async def serial(self, event: str, *args: Any) -> Any:
        return await self._kernel.bus.serial(event, *args)

    async def bail(self, event: str, *args: Any) -> Any:
        return await self._kernel.bus.bail(event, *args)

    async def waterfall(self, event: str, *args: Any) -> Any:
        return await self._kernel.bus.waterfall(event, *args)

    # ── 子插件 ───────────────────────────────────────────

    def plugin(self, target: Any, config: Optional[dict] = None) -> Any:
        """挂载子插件，返回其 Fiber（继承父上下文，独立生命周期）。"""
        definition = as_plugin(target)
        return self._kernel.mount(definition, parent_ctx=self, config=config)

    async def dispose(self) -> None:
        if self._fiber is not None:
            await self._fiber.dispose()

    # ── 服务解析（作用域 + 拦截器） ──────────────────────

    def _resolve(self, key: str) -> tuple[Any, bool]:
        value, found = self._resolve_raw(key)
        if not found:
            return _MISSING, False
        return self._apply_interceptors(key, value), True

    def _resolve_raw(self, key: str) -> tuple[Any, bool]:
        if key in self._local:
            return self._local[key], True
        if key in self._isolated:
            return _MISSING, False
        if self._parent is not None:
            return self._parent._resolve_raw(key)
        value = self._kernel.resolve_service(key)
        if value is _MISSING:
            return _MISSING, False
        return value, True

    def _apply_interceptors(self, key: str, value: Any) -> Any:
        if self._parent is not None:
            value = self._parent._apply_interceptors(key, value)
        for name, callback in self._interceptors:
            if name == key or name == "*":
                value = callback(value)
        return value

    # ── 属性式服务解析 ───────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        kernel = self.__dict__.get("_kernel")
        if kernel is None:
            raise AttributeError(name)
        value, found = self._resolve(name)
        if not found:
            raise AttributeError(
                f"上下文无属性/服务: {name!r}（可用服务: {sorted(kernel.service_keys())}）"
            )
        return value

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<Context {self.name!r} services={sorted(self._kernel.service_keys())}>"

    # ── 内部 ─────────────────────────────────────────────

    def _finish_apply(self, result: Any) -> None:
        """apply 返回后的收尾（兼容 Service 返回实例但未自注册的情况）。"""
        if result is None:
            return
        keys = getattr(self._fiber.definition, "provide", ()) if self._fiber else ()
        for key in keys:
            if not self.has(key):
                self.provide(key, result)


# 服务缺失哨兵
_MISSING = object()


def _make_disposer(result: Any) -> Callable[[], Any]:
    """把 effect 回调的返回值归一化为 disposer。"""
    if result is None:
        return _noop

    if inspect.isawaitable(result):
        async def _await_disposer() -> None:
            resolved = await result
            if callable(resolved):
                cleanup = resolved()
                if inspect.isawaitable(cleanup):
                    await cleanup
        return _await_disposer

    if callable(result):
        def _sync_disposer() -> Any:
            cleanup = result()
            return cleanup
        return _sync_disposer

    return _noop


def _noop() -> None:
    return None


__all__ = ["Context"]
