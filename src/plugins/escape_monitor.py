"""Escape 监看插件 — 提供 ``ctx.escape_monitor``。

「一切皆插件」：活跃 EscapeMonitor 的单例状态（原 ``_monitor`` 模块全局）上移
为内核服务 + ``_registry`` 单例注册表；监视器的创建（``EscapeMonitor``）与
活跃实例管理（查询/停止）经本服务统一接入，可按 Profile/Patch 禁用或替换。

模块级 ``get_active_monitor`` / ``stop_active_monitor`` 优先经本服务解析
（内核缺失时回退 ``_registry`` 进程级单例），保持既有调用点兼容。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class EscapeMonitorService(Service):
    """Escape 监看服务 — 占据 ``ctx.escape_monitor``。"""

    provide = "escape_monitor"
    name = "escape_monitor"

    def active(self):
        """当前活跃监视器（无则 None）。"""
        from ..api.escape_monitor._registry import raw_active_monitor

        return raw_active_monitor()

    def stop(self) -> None:
        """停止当前活跃监视器。"""
        from ..api.escape_monitor._registry import raw_stop_monitor

        raw_stop_monitor()

    def create(self, input_instance):
        """创建一个 EscapeMonitor（仅构造，不启动）。"""
        from ..api.escape_monitor import EscapeMonitor

        return EscapeMonitor(input_instance=input_instance)

    def set_active(self, monitor) -> None:
        from ..api.escape_monitor._registry import set_active_monitor

        set_active_monitor(monitor)

    def clear_active(self, monitor=None) -> None:
        from ..api.escape_monitor._registry import clear_active_monitor

        clear_active_monitor(monitor)


@plugin("escape_monitor", provide=["escape_monitor"])
def apply(ctx):
    return EscapeMonitorService(ctx, ctx.config)


__all__ = ["EscapeMonitorService", "apply"]
