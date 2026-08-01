"""SubAgentPanel — 子 Agent 面板组件。

将 AppModel.subagent_lines（控制器推送的 ANSI 行）渲染为 Text 元素。
行内含 ANSI 颜色序列 → 经 ansi_to_runs 解析为样式 run。

样式互换契约（方向C 步骤8 收敛）：
  ANSI 字符串为「控制器→模型→组件」互换契约——模型 ``subagent_lines``
  存 ANSI 行（由 ``_subagent_render.render_frame`` 产生，ANSI 字符串
  是唯一序列化形式）；组件侧转换点收敛到本模块 ``_render_children`` 的
  ``ansi_to_runs`` 一处（删除任何重复 ANSI 解析/样式拼接）。

超宽行截断（不换行）：与旧版 _ansi_truncate(line, tw) 语义一致——
每行保持单行，超出终端宽度部分丢弃，避免破坏面板树形结构。

PERF-3：内部用 ``use_memo`` 缓存子树（deps = subagent_lines 内容 + 状态
活跃标志），组件树重建时对未变更 live 区短路。
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT, StyledRun, truncate_runs, use_memo
from src.renderer.ansi.helpers import ansi_to_runs


def _render_children(model, width: int) -> list:
    """构建面板子树（按行截断 + 转样式 run）。

    唯一 ANSI → StyledRun 转换点（方向C 步骤8）：subagent_lines 的
    ANSI 行经 ``ansi_to_runs`` 解析为样式 run，再按终端宽度截断。
    """
    children = []
    for line in model.subagent_lines or []:
        if not line:
            continue
        # 强制单行契约：来源字段（description/model_info/parse_info/tool detail
        # 等）可能含 \n/\r，直接渲染会被终端按换行拆成两行——显示前转义为
        # 字面量（与 _subagent_render.format_tool_record 语义一致）。
        line = line.replace("\r", "\\r").replace("\n", "\\n")
        runs = truncate_runs(
            [StyledRun(r.text, r.style) for r in ansi_to_runs(line) if r.text],
            width,
        )
        if runs:
            children.append(h(TEXT, {"styled": runs}))
    return children


def SubAgentPanel(props) -> object:
    """渲染 subagent 面板行（超宽截断为单行）。

    PERF-3：``use_memo`` 缓存子树——deps 用 subagent_lines 内容
    （列表变化时重建）+ 状态活跃标志，未变化时调和期返回缓存元素。
    """
    model = props["model"]
    width = props.get("width", 80)
    children = use_memo(
        lambda: _render_children(model, width),
        # P3-9：deps 补 width——_render_children 截断宽度依赖 width；修复前
        # 仅 (subagent_lines, status_active)，终端 resize 且内容未变时面板行
        # 按旧宽度截断（陈旧）。
        (tuple(model.subagent_lines or ()), model.status.status_active, width),
    )
    if not children:
        return h(BOX, None, [])
    return h(BOX, None, children)


__all__ = ["SubAgentPanel"]
