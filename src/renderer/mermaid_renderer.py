"""MermaidRenderer — Mermaid 图表 → ASCII/Unicode 终端渲染器。

将 Mermaid 源码渲染为带框线字符的终端文本（Rich Text）。
支持：流程图、时序图、类图、状态图、甘特图、饼图、ER图、
      Git分支图、思维导图、时间线、用户旅程图。

架构：
  检测 diagram type (首行) → 分派到专用渲染方法 → 返回 Rich Text

拆分说明：
  - _mermaid_helpers.py                        — 辅助函数与样式常量
  - _mermaid_flowchart.py                      — 流程图 Mixin
  - _mermaid_sequence.py                       — 时序图 Mixin
  - _mermaid_class_state_gantt_pie.py          — 类图/状态图/甘特/饼图 Mixin
  - _mermaid_er_gitgraph_mindmap_timeline_journey.py — ER/Git/思维导图/时间线/旅程 Mixin
"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._mermaid_helpers import (
    _STYLE_HEADER, _STYLE_ARROW, _STYLE_NODE, _STYLE_EDGE_LABEL,
    _is_comment_line,
)
from ._mermaid_flowchart import MermaidFlowchartMixin
from ._mermaid_sequence import MermaidSequenceMixin
from ._mermaid_class_state_gantt_pie import MermaidClassStateGanttPieMixin
from ._mermaid_er_gitgraph_mindmap_timeline_journey import MermaidExtraMixin


class MermaidRenderer(
    MermaidFlowchartMixin,
    MermaidSequenceMixin,
    MermaidClassStateGanttPieMixin,
    MermaidExtraMixin,
):
    """Mermaid 图表 ASCII 渲染器。"""

    # 边关系符号映射（用于类图）
    _REL_SYMBOLS = {
        "<|--": "◁─", "*--": "◆─", "o--": "○─",
        "-->": "─▶", "--|": "─▷", "..>": "·▶", "..|>": "·▷",
    }

    def render(self, source: str) -> Text:
        if not source or not source.strip():
            return Text("  📊 (empty diagram)", style=Style(dim=True, italic=True))
        lines = [l.rstrip() for l in source.split('\n') if l.strip()]
        if not lines:
            return Text("  📊 (empty diagram)", style=Style(dim=True, italic=True))
        first = lines[0].strip()
        if first.lower().startswith("graph ") or first.lower().startswith("flowchart "):
            return self._render_flowchart(lines)
        elif first.lower().startswith("sequencediagram"):
            return self._render_sequence(lines)
        elif first.lower().startswith("classdiagram"):
            return self._render_class(lines)
        elif first.lower().startswith("statediagram"):
            return self._render_state(lines)
        elif first.lower().startswith("gantt"):
            return self._render_gantt(lines)
        elif first.lower().startswith("pie"):
            return self._render_pie(lines)
        elif first.lower().startswith("erdiagram"):
            return self._render_er(lines)
        elif first.lower().startswith("gitgraph"):
            return self._render_gitgraph(lines)
        elif first.lower().startswith("mindmap"):
            return self._render_mindmap(lines)
        elif first.lower().startswith("timeline"):
            return self._render_timeline(lines)
        elif first.lower().startswith("journey"):
            return self._render_journey(lines)
        else:
            return self._render_fallback(lines)

    # ═══════════════════════════════════════════════════════
    # Fallback
    # ═══════════════════════════════════════════════════════

    def _render_fallback(self, lines: list[str]) -> Text:
        result = Text()
        first = lines[0].strip() if lines else ""
        type_name = first.split()[0] if first else "diagram"
        result.append(f"\n  📊 {type_name}", style=_STYLE_HEADER)
        result.append("\n")
        for line in lines:
            s = line.strip()
            if s:
                result.append(f"  │ {s}", style=Style(dim=True))
                result.append("\n")
        return result
