"""ToolStatusHeader — 顶部工具调用状态区组件（Claude TUI parity 步骤 2.1）。

读取 ``model.active_tool``（AppModel 维护的进行中工具状态，None 时不占行）：
  - status="running"：渲染一行 open 边框 ``┌─ ⚡ 工具名 · 参数摘要 ─┐``
    （工具图标取 ``_tool_icons.TOOL_ICONS``，运行色 ``palette.tool_running``）；
  - status="done"/"fail"：该工具已关闭 → 不显示（header 仅展示「进行中」工具，
    与 Claude Code 顶部正在执行工具一致）。

位置：已从 App 组件树移除（工具运行状态改由工具卡片顶边框 ● 图标展示，
双份冗余）——本组件文件与 ``test_tool_header.py`` 保留用于隔离验证
（``model.active_tool`` 仍由 open/close_tool_box 维护）。
"""

from __future__ import annotations

from src.tui.app._theme import get_active_palette
from src.tui.core.style import Style
from src.tui.ink import TEXT, StyledRun, h, Column
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
        # ★ 阶段2（标准布局容器重构）：空状态统一返回空 TEXT（h=0 不占行），
        #   与活跃状态类型一致——避免 BOX↔TEXT 类型切换导致 fiber 销毁重建。
        return h(TEXT, {"children": ""})
    palette = get_active_palette()
    title_runs = _build_header_runs(active, palette)
    # ★ 方向1（美化/健壮性）：边框颜色用活动调色板 ``palette.border``（主题
    #   一致；dark 与硬编码 fg=23 同值，零视觉回归）；边框块各行分别渲染为
    #   独立 TEXT 行——修复前把全部行 runs 扁平进单个 height=1 的 TEXT（多行
    #   边框块会被压进一行错乱；当前 body_lines=None 仅单行标题，行为一致）。
    lines = build_border_box(
        title_runs, None, width=max(1, width - 1),
        status="open", border_style=palette.border,
    )
    children = []
    for line in lines:
        runs = [StyledRun(r.text, r.style) for r in line.runs if r.text]
        if runs:
            children.append(h(TEXT, {"styled": runs, "height": 1}))
    # ★ 阶段2（标准布局容器重构）：BOX(None) → Column（默认 flexDirection=
    #   column，输出与重构前一致）。
    return h(Column, None, children)


__all__ = ["ToolStatusHeader", "_build_header_runs"]
