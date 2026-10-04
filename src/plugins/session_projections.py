"""会话投影插件 — 提供 ``ctx.session_projections``（投影 seam）。

对应 DeepSeek Harness 的 ``ctx.sessionProjections``：已注册单元增量折叠
已提交的会话事件，host 消费方通过 ``state_of()`` 读取单个类型化状态，载体
通过 ``snapshot()`` 批量取得裁剪后的客户端视图。

内置 ``turnBoundary`` 投影（agent loop 为读取方注册的共享状态）：折叠
``turn/start`` / ``step/*`` / ``turn/end`` 事件，产出当前轮次边界视图。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ..core.events.agent_types import SessionEventType
from ..core.session_log import ProjectionRegistry
from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


def _turn_boundary_initial() -> Dict[str, Any]:
    return {"turn": 0, "open": False, "steps": 0, "interrupted": False}


def _turn_boundary_folder(state: Dict[str, Any], event: Any) -> Dict[str, Any]:
    if event.type == SessionEventType.TURN_START:
        state = {"turn": state.get("turn", 0) + 1, "open": True, "steps": 0, "interrupted": False}
    elif event.type == SessionEventType.STEP_START:
        state = {**state, "steps": state.get("steps", 0) + 1}
    elif event.type == SessionEventType.TURN_END:
        state = {**state, "open": False, "interrupted": bool((event.data or {}).get("interrupted", False))}
    return state


class SessionProjectionsService(Service):
    """会话投影服务 — 占据 ``ctx.session_projections``。"""

    provide = "session_projections"
    name = "session_projections"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._registry = ProjectionRegistry()
        self._registry.register("turnBoundary", _turn_boundary_folder, initial=_turn_boundary_initial)
        ctx.effect(lambda: self._registry.clear)

    @property
    def registry(self) -> ProjectionRegistry:
        return self._registry

    def register(self, name: str, folder, *, initial=None):
        """注册一个投影单元，返回注销函数（注册即副作用）。"""
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
    return SessionProjectionsService(ctx)
