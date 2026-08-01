"""ToolStatusHeader — 顶部工具调用状态区组件（Claude TUI parity 步骤 2.1）。

读取 ``model.active_tool``（AppModel 维护的进行中工具状态，None 时不占行）：
  - status="running"：渲染一行 open 边框 ``┌─ ⚡ 工具名 · 参数摘要 ─┐``
    （工具图标取 ``_tool_icons.TOOL_ICONS``，运行色 ``palette.tool_running``）；
  - status="done"/"fail"：该工具已关闭 → 不显示（header 仅展示「进行中」工具，
    与 Claude Code 顶部正在执行工具一致）。

位置：App 组件树 ChatView 之后、SubAgentPanel 之前（live 区尾部）→ 工具状态
变化时首差异行在 live 区头部，聊天缓存行不被重写，维持 O(live+新增)。
"""

from __future__ import annotations

from src.tui.app._theme import get_active_palette
from src.tui.core.style import Style
from src.tui.ink import BOX, TEXT, StyledRun, h
from src.tui.ink.helpers import build_border_box


def _tool_icon(tool_name: str) -> str:
    from src.tui._tool_icons import TOOL_ICONS
    return TOOL_ICONS.get(tool_name, "\u2699")  # 缺省齿轮


def _build_header_runs(active_tool: dict, palette) -> list[StyledRun]:
    """构建标题 runs：图标 + 工具名（+ 参数摘要）。"""
    icon = _tool_icon(active_tool.get("tool_name", ""))
    running_style = palette.tool_running
    runs = [StyledRun(f"{icon} ", running_style)]
    runs.append(StyledRun(active_tool.get("name", "工具"), Style(fg=252)))
    detail = active_tool.get("detail", "")
    if detail:
        runs.append(StyledRun(f" \u00b7 {detail}", palette.dim))
    return runs


def ToolStatusHeader(props) -> object:
    """顶部工具调用状态区组件（active_tool=None 时不占行）。"""
    model = props["model"]
    width = props.get("width", 80)
    active = model.active_tool
    if active is None or active.get("status") != "running":
        return h(BOX, None, [])
    palette = get_active_palette()
    title_runs = _build_header_runs(active, palette)
    lines = build_border_box(title_runs, None, width=max(1, width - 1), status="open")
    runs = [StyledRun(r.text, r.style) for line in lines for r in line.runs if r.text]
    return h(BOX, None, [h(TEXT, {"styled": runs, "height": 1})])


__all__ = ["ToolStatusHeader", "_build_header_runs"]
