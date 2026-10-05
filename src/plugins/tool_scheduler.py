"""工具调度器插件 — 提供 ``ctx.tool_scheduler``。

「一切皆插件」：工具调度的全局单例（``ToolScheduler.default()``）在内核挂载
本插件后由内核服务解析——``ToolScheduler.default()`` 优先返回本服务持有的
调度器实例（与 ``ctx.tools`` 注册表同源），内核缺失时回退进程级单例，使
「调度器 == 内核服务」而非游离的模块级全局状态。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ToolSchedulerService(Service):
    """工具调度器服务 — 占据 ``ctx.tool_scheduler``。"""

    provide = "tool_scheduler"
    name = "tool_scheduler"
    inject = ("tools",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.tool_executor_async import ToolScheduler

        self._scheduler = ToolScheduler(registry=ctx.tools.registry)

    @property
    def scheduler(self):
        return self._scheduler

    def default(self):
        return self._scheduler


@plugin("tool_scheduler", inject=["tools"], provide=["tool_scheduler"])
def apply(ctx):
    return ToolSchedulerService(ctx)
