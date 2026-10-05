"""Kernel — 无特权内核。

内核只做一件事：加载插件、卸载插件、解析依赖。它对模型、工具、甚至
「Agent 是什么」都不带任何观点——所有核心行为都是坐在内核之上的插件。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from .context import Context, _MISSING
from .errors import PluginError
from .events import EventBus
from .fiber import Fiber, FiberState
from .plugin import Plugin, as_plugin

_logger = logging.getLogger(__name__)


class Kernel:
    """插件内核：服务容器 + Fiber 生命周期 + 依赖驱动加载。"""

    def __init__(self, name: str = "root", profile: str = "") -> None:
        self.name = name
        #: 构建本内核的 Profile 名（组合根写入；空串=未记录，自省/展示回退默认）
        self.profile = profile
        self.bus = EventBus()
        # key -> [(value, owner_fiber)] 栈式覆盖
        self._services: dict[str, list[tuple[Any, Optional[Fiber]]]] = {}
        self._fibers: List[Fiber] = []
        self._booted = False
        self._settling = False
        self._settle_task: Optional[asyncio.Task] = None
        self.root = Context(self, fiber=None, parent=None, config={})

    # ── 服务容器 ─────────────────────────────────────────

    def resolve_service(self, key: str) -> Any:
        stack = self._services.get(key)
        if not stack:
            return _MISSING
        return stack[-1][0]

    def has_service(self, key: str) -> bool:
        return bool(self._services.get(key))

    def service_keys(self) -> list[str]:
        return [key for key, stack in self._services.items() if stack]

    def _acquire_service(self, key: str, value: Any, owner: Optional[Fiber]) -> None:
        self._services.setdefault(key, []).append((value, owner))
        self._request_replan()

    def _release_service(self, key: str, value: Any) -> None:
        stack = self._services.get(key)
        if not stack:
            return
        for index in range(len(stack) - 1, -1, -1):
            if stack[index][0] is value:
                stack.pop(index)
                break
        if not stack:
            self._services.pop(key, None)
        self._request_replan()

    def _release_services(self, keys: List[str], owner: Optional[Fiber] = None) -> None:
        for key in keys:
            stack = self._services.get(key)
            if not stack:
                continue
            if owner is None:
                self._services.pop(key, None)
                continue
            self._services[key] = [entry for entry in stack if entry[1] is not owner]
            if not self._services[key]:
                self._services.pop(key, None)
        if keys:
            self._request_replan()

    def provide(self, key: str, value: Any) -> Any:
        """在根上下文注册一个全局服务。"""
        return self.root.provide(key, value)

    def require(self, key: str) -> Any:
        """解析服务，缺失抛 ServiceNotFound。"""
        return self.root.consume(key)

    def get(self, key: str, default: Any = None) -> Any:
        value = self.resolve_service(key)
        return default if value is _MISSING else value

    # ── 插件挂载 ─────────────────────────────────────────

    def mount(
        self,
        target: Any,
        *,
        parent_ctx: Optional[Context] = None,
        config: Optional[dict] = None,
    ) -> Fiber:
        """挂载插件，返回其 Fiber。"""
        definition = as_plugin(target)
        parent = parent_ctx if parent_ctx is not None else self.root
        merged = dict(definition.config)
        if config:
            merged.update(config)
        fiber = Fiber(self, definition, parent, merged)
        self._fibers.append(fiber)
        if parent.fiber is not None:
            parent.fiber.add_child(fiber)
        self._request_replan()
        return fiber

    def plugin(self, target: Any, config: Optional[dict] = None) -> Fiber:
        return self.mount(target, parent_ctx=self.root, config=config)

    def mount_file(self, path: str, config: Optional[dict] = None) -> Fiber:
        """从文件加载并挂载插件（热挂载单文件插件）。"""
        from .loader import load_module, plugins_from_module

        module = load_module(path)
        plugins = plugins_from_module(module)
        if not plugins:
            raise PluginError(f"文件未定义插件: {path}")
        fibers = [self.mount(plug, parent_ctx=self.root, config=config) for plug in plugins]
        return fibers[0]

    async def reload(self, name: str) -> Fiber:
        """按名重启插件（完整卸载后重新加载）。"""
        fiber = self.fiber(name)
        if fiber is None:
            raise PluginError(f"未找到插件: {name!r}")
        await fiber.restart()
        await self.settle()
        return fiber

    def fibers(self) -> list[Fiber]:
        return list(self._fibers)

    def fiber(self, name: str) -> Optional[Fiber]:
        """按名返回插件 Fiber。

        同名 Fiber 可能因热重载/重复挂载而存在多个历史实例——优先返回最近
        挂载且未 DISPOSED 的一个（热卸载/自省指向活动实例），全已卸载时返回
        最后一个。
        """
        matches = [fiber for fiber in self._fibers if fiber.name == name]
        if not matches:
            return None
        for fiber in reversed(matches):
            if fiber.state is not FiberState.DISPOSED:
                return fiber
        return matches[-1]

    # ── 依赖重规划 ───────────────────────────────────────

    def _request_replan(self) -> None:
        if not self._booted or self._settling:
            return
        loop = _running_loop()
        if loop is None:
            return
        if self._settle_task is None or self._settle_task.done():
            self._settle_task = loop.create_task(self._safe_settle())

    async def _safe_settle(self) -> None:
        try:
            await self.settle()
        except Exception:
            _logger.exception("内核重规划失败")

    async def settle(self) -> None:
        """反复迁移 Fiber 状态直到稳定（依赖驱动加载/卸载）。"""
        self._settling = True
        try:
            for _ in range(100000):
                acted = False
                for fiber in list(self._fibers):
                    if fiber._manual_dispose:
                        continue
                    if fiber.state in (FiberState.PENDING, FiberState.DISPOSED) and fiber.deps_ready():
                        await fiber.start()
                        acted = True
                    elif fiber.state == FiberState.ACTIVE and not fiber.deps_ready():
                        await fiber.unload()
                        acted = True
                if not acted:
                    break
            else:  # pragma: no cover - 防御
                _logger.warning("内核 settle 达到迭代上限，可能存在依赖抖动")
        finally:
            self._settling = False
            self._booted = True

    # ── 上下文 ───────────────────────────────────────────

    def _make_context(
        self,
        fiber: Optional[Fiber],
        parent: Optional[Context],
        config: dict,
    ) -> Context:
        return Context(self, fiber=fiber, parent=parent, config=config)

    # ── 关闭 ─────────────────────────────────────────────

    async def dispose(self) -> None:
        """卸载全部插件（逆序）并清空内核状态。"""
        for fiber in reversed(list(self._fibers)):
            try:
                await fiber.dispose()
            except Exception:
                _logger.exception("插件卸载失败: %s", fiber.name)
        self._fibers.clear()
        self._services.clear()
        self.bus.clear()
        self._booted = False
        if self._settle_task is not None:
            self._settle_task.cancel()
            self._settle_task = None

    async def __aenter__(self) -> "Kernel":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.dispose()


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# ── 进程级内核访问点（组合根写入、运行时组件读取） ──────────
#
# 实现下沉到 ``.current``（不依赖 Kernel/Context，避免 kernel ↔ fiber 循环
# 依赖）；此处 re-export 保持既有 ``from .kernel import ...`` 用法兼容。

from .current import (  # noqa: E402 - 置于文件末尾，语义为访问点 re-export
    activate_kernel as _activate_kernel,
    current_context,
    get_current_kernel,
    set_current_kernel,
)


__all__ = [
    "Kernel",
    "set_current_kernel",
    "get_current_kernel",
    "current_context",
]
