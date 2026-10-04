"""显示事件发布门面 — 统一发布入口（核心层）。

基础设施层（api/tools）与核心层经此发布 DisplayEvent，不再反向依赖
表现层 ``tui.events.publish``（表现层仅 re-export 兼容）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .display_types import OutputEvent, ToolSummaryEvent

if TYPE_CHECKING:
    from .display_bus import DisplayEventBus
    from .display_types import DisplayEvent


def default_bus() -> "DisplayEventBus":
    """获取默认发布总线（进程级单例，惰性获取）。"""
    from .display_bus import DisplayEventBus
    return DisplayEventBus.get_default()


def emit(event: "DisplayEvent", *, bus: "Optional[DisplayEventBus]" = None) -> None:
    """类型化发布显示事件（统一发布入口）。bus=None 走默认总线。"""
    target = bus if bus is not None else default_bus()
    target.publish(event)


def publish_output(text: str, level: str = "info", source: str = "") -> None:
    """便捷函数：发布 OutputEvent 到默认总线。"""
    emit(OutputEvent(text=text, level=level, source=source))


def publish_tool_summary(
    successful_tools: list,
    failed_tools: list,
    source: str = "",
) -> None:
    """便捷函数：发布工具执行汇总事件到默认总线。"""
    emit(
        ToolSummaryEvent(
            successful_tools=tuple(successful_tools),
            failed_tools=tuple(failed_tools),
            source=source,
        )
    )


__all__ = ["emit", "default_bus", "publish_output", "publish_tool_summary"]
