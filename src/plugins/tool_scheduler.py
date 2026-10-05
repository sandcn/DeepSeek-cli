"""工具调度器插件 — 提供 ``ctx.tool_scheduler``。

「一切皆插件」：工具调度的全局单例（``ToolScheduler.default()``）在内核挂载
本插件后由内核服务解析——``ToolScheduler.default()`` 优先返回本服务持有的
调度器实例（与 ``ctx.tools`` 注册表同源），内核缺失时回退进程级单例，使
「调度器 == 内核服务」而非游离的模块级全局状态。

工具批次**执行引擎**（``dag`` / ``serial`` / ``parallel`` 及扩展引擎）经
``src.core.tool_engines`` 注册表解析——每个引擎是清单中的独立插件条目，可被
Patch/Overlay 禁用或替换；本聚合插件处理组合根注入的 ``managed_tool_engines``
与 config ``disabled_tool_engines``；config ``engine`` 指定当前引擎，config
``scheduler`` 可整体替换调度器实现。
"""

from __future__ import annotations

import logging
from typing import Any

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


def _import_attr(dotted: str):
    from .tool_plugin import import_attr

    return import_attr(dotted)


class ToolSchedulerService(Service):
    """工具调度器服务 — 占据 ``ctx.tool_scheduler``。"""

    provide = "tool_scheduler"
    name = "tool_scheduler"
    inject = ("tools",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_tool_engines") or ()
        if managed:
            from ..core.tool_engines import set_managed_builtin_tool_engines

            undo_managed = set_managed_builtin_tool_engines(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_tool_engines") or ()
        if disabled:
            from ..core.tool_engines import disable_builtin_tool_engines

            undo_disabled = disable_builtin_tool_engines(disabled)
            ctx.effect(lambda: undo_disabled)
        self._scheduler = self._construct_scheduler(cfg)

    def _construct_scheduler(self, cfg: dict):
        ref = cfg.get("scheduler")
        if ref:
            cls = _import_attr(ref)
            return cls(registry=self.ctx.tools.registry)
        from ..core.tool_executor_async import ToolScheduler

        return ToolScheduler(registry=self.ctx.tools.registry, engine=cfg.get("engine") or "")

    @property
    def scheduler(self):
        return self._scheduler

    def default(self):
        return self._scheduler

    # ── 执行引擎（自省 / 切换） ──────────────────────────

    def engine(self) -> str:
        getter = getattr(self._scheduler, "engine_name", None)
        return getter() if callable(getter) else ""

    def set_engine(self, name: str) -> str:
        setter = getattr(self._scheduler, "set_engine", None)
        if not callable(setter):
            raise AttributeError("调度器不支持 set_engine")
        return setter(name)

    def engines(self) -> list:
        from ..core.tool_engines import engine_names

        return engine_names()


@plugin("tool_scheduler", inject=["tools"], provide=["tool_scheduler"])
def apply(ctx):
    return ToolSchedulerService(ctx, ctx.config)


__all__ = ["ToolSchedulerService", "apply"]
