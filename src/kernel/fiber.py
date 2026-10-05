"""Fiber — 插件的生命周期作用域（状态机）。

状态机（引用 Cordis 的公开语义）::

    PENDING → LOADING → ACTIVE
                   ↘ FAILED
    ACTIVE → UNLOADING → DISPOSED

- ``PENDING``   已声明，依赖未就绪；
- ``LOADING``   依赖就绪，正在执行 apply；
- ``ACTIVE``    插件运行中；
- ``FAILED``    apply 抛出异常；
- ``UNLOADING`` 正在卸载并释放资源；
- ``DISPOSED``  已完全卸载。

依赖驱动的加载与卸载：声明了 ``inject`` 的插件等待所有必需服务就绪；
服务消失时插件自动卸载（ACTIVE→DISPOSED），服务恢复后可重新加载。
每个 Fiber 拥有的注册（服务、事件监听、effect）在卸载时全部撤销。
"""

from __future__ import annotations

import inspect
import logging
from enum import Enum
from typing import Any, List, Optional

from .errors import FiberStateError
from .plugin import Plugin

_logger = logging.getLogger(__name__)


class FiberState(str, Enum):
    PENDING = "PENDING"
    LOADING = "LOADING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    UNLOADING = "UNLOADING"
    DISPOSED = "DISPOSED"


class Fiber:
    """插件作用域 — 管理插件从加载到卸载的完整生命周期。"""

    def __init__(
        self,
        kernel: Any,
        definition: Plugin,
        parent_ctx: Any,
        config: Optional[dict] = None,
    ) -> None:
        self.kernel = kernel
        self.definition = definition
        self.config = dict(config if config is not None else definition.config)
        self._parent_ctx = parent_ctx
        self.state: FiberState = FiberState.PENDING
        self.error: Optional[BaseException] = None
        self.ctx: Any = None
        self._disposers: List[Any] = []
        self._children: List[Fiber] = []
        self._manual_dispose = False
        self._user_disabled = False
        self._provide_keys: List[str] = []
        self._extra_inject: List[str] = []

    # ── 属性 ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def inject(self) -> tuple[str, ...]:
        keys = list(self.definition.inject)
        for key in self._extra_inject:
            if key not in keys:
                keys.append(key)
        return tuple(keys)

    @property
    def active(self) -> bool:
        return self.state == FiberState.ACTIVE

    @property
    def disposed(self) -> bool:
        return self.state == FiberState.DISPOSED

    @property
    def disabled(self) -> bool:
        """是否被运行时显式禁用（可经 ``enable`` 恢复）。"""
        return self._user_disabled

    # ── 依赖 ─────────────────────────────────────────────

    def missing_dependencies(self) -> list[str]:
        return [key for key in self.inject if not self.kernel.has_service(key)]

    def deps_ready(self) -> bool:
        return not self.missing_dependencies()

    # ── 注册追踪 ─────────────────────────────────────────

    def add_disposer(self, disposer: Any) -> None:
        """记录一个清理回调（同步或异步）。"""
        self._disposers.append(disposer)

    def owned_disposer(self, disposer: Any) -> Any:
        """注册并返回 disposer（带一次性语义）。"""
        called = False

        def _once() -> Any:
            nonlocal called
            if called:
                return None
            called = True
            return disposer()

        self.add_disposer(_once)
        return _once

    # ── 生命周期 ─────────────────────────────────────────

    async def start(self) -> None:
        """按依赖就绪状态启动插件。"""
        if self._manual_dispose:
            return
        if self.state in (FiberState.LOADING, FiberState.ACTIVE):
            return
        if self.state == FiberState.FAILED:
            return
        if not self.deps_ready():
            self.state = FiberState.PENDING
            return

        self.state = FiberState.LOADING
        self.error = None
        self.ctx = self.kernel._make_context(self, self._parent_ctx, self.config)
        from .current import activate_kernel

        restore_kernel = activate_kernel(self.kernel)
        try:
            result = self.definition.instantiate(self.ctx)
            if inspect.isawaitable(result):
                result = await result
            self.ctx._finish_apply(result)
            # 动态依赖（ctx.inject）可能在 apply 期间声明；若此刻不满足，
            # 回退到 PENDING，等待依赖就绪后重新加载。
            if not self.deps_ready():
                await self._teardown()
                self.state = FiberState.PENDING
                return
        except BaseException as exc:  # noqa: BLE001 - 记录后置为 FAILED
            self.error = exc
            self.state = FiberState.FAILED
            _logger.exception("插件加载失败: %s", self.definition.name)
            await self._run_disposers()
            return
        finally:
            restore_kernel()
        self.state = FiberState.ACTIVE

    async def unload(self) -> None:
        """依赖消失时的自动卸载（可被重新加载）。"""
        if self.state in (FiberState.PENDING, FiberState.DISPOSED, FiberState.FAILED):
            return
        await self._teardown()

    async def restart(self) -> None:
        """重启插件：先完整卸载（释放全部注册），再重新执行 apply。"""
        if self.state in (FiberState.ACTIVE, FiberState.FAILED, FiberState.UNLOADING):
            await self._teardown()
        self._manual_dispose = False
        self._user_disabled = False
        await self.start()

    async def disable(self) -> None:
        """运行时禁用插件：撤销全部注册与子插件，保留重新启用的能力。

        与 :meth:`dispose` 的区别：禁用是**可逆**的（``enable`` 重新执行
        apply）；dispose 是永久卸载。禁用的 Fiber 不参与依赖重规划（不会被
        自动重新加载），直到被显式 enable。
        """
        self._manual_dispose = True
        self._user_disabled = True
        if self.state in (FiberState.DISPOSED, FiberState.PENDING) and not self._children:
            self.state = FiberState.DISPOSED
            return
        await self._teardown()

    async def enable(self) -> None:
        """重新启用被禁用的插件（重新执行 apply；依赖不满足则停在 PENDING）。"""
        self._manual_dispose = False
        self._user_disabled = False
        await self.start()

    async def dispose(self) -> None:
        """永久卸载：递归清理子插件与全部注册，且不再重载。"""
        self._manual_dispose = True
        if self.state == FiberState.DISPOSED and not self._children:
            return
        await self._teardown()

    async def _teardown(self) -> None:
        if self.state == FiberState.UNLOADING:
            return
        self.state = FiberState.UNLOADING

        # 子插件逆序卸载
        for child in reversed(self._children):
            try:
                await child.dispose()
            except Exception:
                _logger.exception("子插件卸载失败: %s", child.name)
        self._children.clear()

        await self._run_disposers()

        # 移除本 Fiber 提供的服务（兜底：正常路径已由 disposer 撤销）
        if self._provide_keys:
            self.kernel._release_services(self._provide_keys, owner=self)
            self._provide_keys.clear()

        self.ctx = None
        self.state = FiberState.DISPOSED

    async def _run_disposers(self) -> None:
        # 逆序清理；多个异步 disposer 并发执行（顺序依赖须放进同一个 effect）
        pending = []
        while self._disposers:
            disposer = self._disposers.pop()
            try:
                result = disposer()
            except Exception:
                _logger.exception("清理回调失败: %s", self.definition.name)
                continue
            if inspect.isawaitable(result):
                pending.append(result)
        if pending:
            import asyncio

            results = await asyncio.gather(*pending, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    _logger.exception("异步清理回调失败: %s", self.definition.name, exc_info=result)

    def add_child(self, child: "Fiber") -> None:
        self._children.append(child)

    def register_provide_keys(self, keys: List[str]) -> None:
        self._provide_keys.extend(keys)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<Fiber {self.name!r} {self.state.value}>"


def ensure_transition_allowed(current: FiberState, target: FiberState) -> None:
    """校验状态迁移合法性（防御性辅助）。"""
    allowed = {
        FiberState.PENDING: {FiberState.LOADING, FiberState.DISPOSED, FiberState.FAILED},
        FiberState.LOADING: {FiberState.ACTIVE, FiberState.FAILED},
        FiberState.ACTIVE: {FiberState.UNLOADING, FiberState.DISPOSED},
        FiberState.FAILED: {FiberState.DISPOSED},
        FiberState.UNLOADING: {FiberState.DISPOSED},
        FiberState.DISPOSED: {FiberState.LOADING, FiberState.PENDING},
    }
    if target not in allowed.get(current, set()):
        raise FiberStateError(f"非法状态迁移: {current.value} → {target.value}")


__all__ = ["Fiber", "FiberState", "ensure_transition_allowed"]
