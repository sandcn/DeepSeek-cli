"""单次模式 Agent 工厂 — 独立模块（打破 app_loop 循环依赖）

从 ``_single.py`` 提取 ``_make_event_agent``：``_session_setup`` 与 ``_single``
均可依赖本模块，消除 ``_session_setup ↔ _single`` 循环。
"""

from __future__ import annotations

from ..core.agent import Agent


def _make_event_agent():
    """创建通过 EventBus 发布事件的 Agent 实例。"""
    from ..core.adapters.display import DefaultDisplayAdapter
    from ..core.adapters.events import DisplayEventBusAdapter
    from ..core.adapters.output import DefaultOutputAdapter
    return Agent(
        display_port=DefaultDisplayAdapter(source="agent"),
        event_port=DisplayEventBusAdapter(source="agent"),
        output_port=DefaultOutputAdapter(),
    )


__all__ = ["_make_event_agent"]
