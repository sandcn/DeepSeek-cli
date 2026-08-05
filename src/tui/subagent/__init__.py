"""SubAgent 面板子域聚合门面（架构归类，2026-08-05）。

将 SubAgent 面板的「控制器 + 渲染 + 状态」三模块归入同一子域，提供统一
导入入口：``from src.tui.subagent import SubAgentPanelController``。

设计说明（方向C：顶层模块归类）：
  - **不移动实现文件**（保持 ``src/tui/_subagent_panel.py`` 等顶层路径）——
    原因：① 既有测试大量 ``patch("src.tui._subagent_panel.time.monotonic")``
    依赖模块路径（移动后 patch 目标失效）；② ``_assembly``/``_consumer``/
    ``core.parallel_executor``/``events.event_bus`` 等跨层引用面大，移动
    收益 < 风险。本门面作为逻辑归类入口，为未来逐步迁移铺路。
  - 导出面 = 控制器 + 状态建模 + 帧渲染三域公开符号。
"""

from __future__ import annotations

from src.tui._subagent_state import StateStore, _AgentSlot, _ToolRecord
from src.tui._subagent_render import (
    _SPINNER_FRAMES,
    _get_tool_color,
    render_frame,
    build_agent_lines,
    format_tool_record,
)
from src.tui._subagent_panel import SubAgentPanelController

__all__ = [
    "SubAgentPanelController",
    "StateStore",
    "_AgentSlot",
    "_ToolRecord",
    "render_frame",
    "build_agent_lines",
    "format_tool_record",
    "_get_tool_color",
    "_SPINNER_FRAMES",
]
