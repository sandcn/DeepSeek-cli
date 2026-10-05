"""内核管理插件 — 提供 ``ctx.kernel_admin``（运行时可管理能力）。

「一切皆插件」：内核自身的运行时能力（启用/禁用/重载插件、依赖诊断、文件
热重载监听）作为可替换服务坐在插件树上——其它插件或工具可经
``ctx.kernel_admin`` 自省与调整插件树，而无需直接 import 内核内部结构。

注意：本服务面向**运行时编排**（运维/自省），不改变组合根的启动装配语义
（启动装配仍由 Profile/Bundle/Patch 决定）。
"""

from __future__ import annotations

from typing import Any, Callable, List

from ..kernel import Service, plugin


class KernelAdminService(Service):
    """内核管理服务 — 占据 ``ctx.kernel_admin``。"""

    provide = "kernel_admin"
    name = "kernel_admin"

    # ── 访问 ─────────────────────────────────────────────

    @property
    def kernel(self):
        return self.ctx.kernel

    # ── 插件树自省 ───────────────────────────────────────

    def plugins(self) -> List[dict]:
        return self.kernel.dependency_report()

    def enabled(self) -> List[str]:
        return self.kernel.enabled_plugins()

    def disabled(self) -> List[str]:
        return self.kernel.disabled_plugins()

    def is_enabled(self, name: str) -> bool:
        return self.kernel.is_enabled(name)

    def stats(self) -> dict:
        return self.kernel.kernel_stats()

    def diagnose(self) -> str:
        return self.kernel.diagnose()

    def why_blocked(self, name: str) -> List[str]:
        return self.kernel.why_blocked(name)

    def service_providers(self, key: str) -> List[str]:
        return self.kernel.service_providers(key)

    # ── 运行时启用 / 禁用 / 重载 ─────────────────────────

    async def enable(self, name: str) -> str:
        fiber = await self.kernel.enable(name)
        return fiber.state.value

    async def disable(self, name: str) -> str:
        fiber = await self.kernel.disable(name)
        return fiber.state.value

    async def set_enabled(self, name: str, enabled: bool) -> str:
        fiber = await self.kernel.set_enabled(name, enabled)
        return fiber.state.value

    async def reload(self, name: str) -> str:
        fiber = await self.kernel.reload(name)
        return fiber.state.value

    async def reload_file(self, path: str) -> List[str]:
        return await self.kernel.reload_file(path)

    # ── 文件热重载监听 ───────────────────────────────────

    def watch_file(self, path: str) -> Callable[[], Any]:
        return self.kernel.watch_file(path)

    def watched(self) -> List[str]:
        return self.kernel.watcher().watched()

    async def start_watching(self, interval: float = 1.0) -> None:
        await self.kernel.watcher().start(interval)

    async def stop_watching(self) -> None:
        await self.kernel.watcher().stop()


@plugin("kernel_admin", provide=["kernel_admin"])
def apply(ctx):
    return KernelAdminService(ctx)


__all__ = ["KernelAdminService", "apply"]
