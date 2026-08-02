"""subagent 卡片渲染辅助 — subagent_lines（ANSI 卡片行）→ ink TEXT 元素。

对齐 Claude Code：子代理活动渲染为**逐 agent 卡片**（``_subagent_render``
产出带边框 ANSI 行），经 ``ChatView`` 并入消息流显示（原独立 SubAgentPanel
组件已移除）。本模块提供唯一 ANSI → StyledRun 转换点（方向C 步骤8）：
``_render_children`` 经 ``ansi_to_runs`` 解析 + 按终端宽度截断 + 换行转义。
"""

from __future__ import annotations

from src.tui.ink import h, TEXT, StyledRun, truncate_runs
from src.renderer.ansi.helpers import ansi_to_runs


def _render_children(model, width: int) -> list:
    """构建 subagent 卡片子树（按行截断 + 转样式 run）。

    唯一 ANSI → StyledRun 转换点（方向C 步骤8）：subagent_lines 的
    ANSI 行经 ``ansi_to_runs`` 解析为样式 run，再按终端宽度截断。
    每行给索引 key（调和器复用 fiber，换行/样式缓存可命中）。
    """
    children = []
    for i, line in enumerate(model.subagent_lines or []):
        if not line:
            continue
        # 强制单行契约：来源字段可能含 \n/\r，直接渲染会被终端按换行拆成
        # 两行——显示前转义为字面量（与 _subagent_render.format_tool_record
        # 语义一致）。
        line = line.replace("\r", "\\r").replace("\n", "\\n")
        runs = truncate_runs(
            [StyledRun(r.text, r.style) for r in ansi_to_runs(line) if r.text],
            width,
        )
        if runs:
            children.append(h(TEXT, {"key": f"subagent-{i}", "styled": runs}))
    return children


__all__ = ["_render_children"]
