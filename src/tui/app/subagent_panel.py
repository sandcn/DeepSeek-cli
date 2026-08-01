"""SubAgentPanel — 子 Agent 面板组件。

将 AppModel.subagent_lines（控制器推送的 ANSI 行）渲染为 Text 元素。
行内含 ANSI 颜色序列 → 经 ansi_to_runs 解析为样式 run。

超宽行截断（不换行）：与旧版 _ansi_truncate(line, tw) 语义一致——
每行保持单行，超出终端宽度部分丢弃，避免破坏面板树形结构。
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT, StyledRun, truncate_runs
from src.renderer.ansi.helpers import ansi_to_runs


def SubAgentPanel(props) -> object:
    """渲染 subagent 面板行（超宽截断为单行）。"""
    model = props["model"]
    width = props.get("width", 80)
    children = []
    for line in model.subagent_lines or []:
        if not line:
            continue
        runs = truncate_runs(
            [StyledRun(r.text, r.style) for r in ansi_to_runs(line) if r.text],
            width,
        )
        if runs:
            children.append(h(TEXT, {"styled": runs}))
    if not children:
        return h(BOX, None, [])
    return h(BOX, None, children)


__all__ = ["SubAgentPanel"]
