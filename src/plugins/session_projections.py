"""会话投影插件 — 提供 ``ctx.session_projections``（投影 seam）。

对应 DeepSeek Harness 的 ``ctx.sessionProjections``：已注册单元增量折叠已
提交的会话事件，host 消费方通过 ``state_of()`` 读取单个类型化状态，载体通过
``snapshot()`` 批量取得裁剪后的客户端视图。

「一切皆插件」：内置 ``turnBoundary`` 投影（agent loop 为读取方注册的共享
状态：折叠 ``turn/start`` / ``step/*`` / ``turn/end`` 事件，产出当前轮次边界
视图）不再硬编码在本服务构造里，而是由清单中的独立条目
（``session_projection``，经 ``src.plugins.session_projection_entries``）注册；
本聚合插件按组合根注入的 ``managed_session_projections``（清单已接管的 name，
含被禁用的）抑制默认注册，使 overlay 禁用单个投影真正生效。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ..core.session_log import ProjectionRegistry
from ..core.session_log.builtin_projections import (
    builtin_projection_names,
    builtin_projection_spec,
)
from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class SessionProjectionsService(Service):
    """会话投影服务 — 占据 ``ctx.session_projections``。"""

    provide = "session_projections"
    name = "session_projections"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        self._registry = ProjectionRegistry()
        self._managed = set(cfg.get("managed_session_projections") or ())
        self._disabled = set(cfg.get("disabled_session_projections") or ())
        for name in builtin_projection_names():
            if name in self._managed or name in self._disabled:
                continue
            initial, folder = builtin_projection_spec(name)
            self._registry.register(name, folder, initial=initial)
        ctx.effect(lambda: self._registry.clear)

    @property
    def registry(self) -> ProjectionRegistry:
        return self._registry

    # ── 自省 ─────────────────────────────────────────────

    def builtin_names(self) -> list:
        """全部内置投影名（含被接管/禁用的）。"""
        return list(builtin_projection_names())

    def managed(self) -> list:
        return sorted(self._managed)

    def disabled(self) -> list:
        return sorted(self._disabled)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, name: str, folder, *, initial=None):
        """注册一个投影单元，返回注销函数（注册即副作用）。"""
        return self._registry.register(name, folder, initial=initial)

    def register_builtin(self, name: str, folder=None, initial=None):
        """按内置投影 id 注册（``folder=None`` 用内置声明）；返回注销函数。"""
        if name in builtin_projection_names():
            spec_initial, spec_folder = builtin_projection_spec(name)
            if folder is None:
                folder = spec_folder
            if initial is None:
                initial = spec_initial
        return self._registry.register(name, folder, initial=initial)

    def unregister(self, name: str) -> bool:
        return self._registry.unregister(name)

    def names(self) -> list:
        return self._registry.names()

    def has(self, name: str) -> bool:
        return self._registry.has(name)

    def state_of(self, name: str, events) -> Any:
        return self._registry.state_of(name, events)

    def snapshot(self, events) -> Dict[str, Any]:
        return self._registry.snapshot(events)

    def reset(self) -> None:
        self._registry.reset()


@plugin("session_projections", provide=["session_projections"])
def apply(ctx):
    return SessionProjectionsService(ctx, ctx.config)


__all__ = ["SessionProjectionsService", "apply"]
