"""subagent 卡片渲染辅助 — subagent_lines（ANSI 卡片行）→ ink TEXT 元素。

对齐 Claude Code：子代理活动渲染为**逐 agent 卡片**（``_subagent_render``
产出带边框 ANSI 行），经 ``ChatView`` 并入消息流显示（原独立 SubAgentPanel
组件已移除）。本模块提供唯一 ANSI → StyledRun 转换点（方向C 步骤8）：
``_render_children`` 经 ``ansi_to_runs`` 解析 + 按终端宽度截断 + 换行转义。
"""

from __future__ import annotations

from src.tui.ink import h, TEXT, StyledRun, truncate_runs, use_memo
from src.renderer.ansi.helpers import ansi_to_runs


def _render_children(model, width: int) -> list:
    """构建 subagent 卡片子树（按行截断 + 转样式 run）。

    唯一 ANSI → StyledRun 转换点（方向C 步骤8）：subagent_lines 的
    ANSI 行经 ``ansi_to_runs`` 解析为样式 run，再按终端宽度截断。
    每行给索引 key（调和器复用 fiber，换行/样式缓存可命中）。

    本函数为**纯计算**（无 hook）；组件内请用 ``use_subagent_children``
    按 ``(subagent_lines, width)`` 引用缓存结果（BUG-42：修复前 ChatView
    每帧直接调用本函数重建全部 StyledRun/Element → TEXT ``_wrap_cache``
    身份键恒 miss，活跃 subagent 期间每帧重包裹）。
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


def use_subagent_children(model, width: int) -> list:
    """use_memo 缓存 subagent 卡片元素列表（subagent_lines 引用不变时零重建）。

    供 ChatView 无条件调用（hook 顺序稳定——即使 subagent_lines 为空也占用
    同一个 memo 槽位；``model.subagent_lines`` 为引用 deps——控制器推送新行
    列表时引用变化 → 缓存失效重算）。
    """
    return use_memo(
        lambda: _render_children(model, width),
        (getattr(model, "subagent_lines", None), width),
    )


__all__ = ["_render_children", "use_subagent_children"]
