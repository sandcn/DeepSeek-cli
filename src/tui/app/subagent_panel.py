"""subagent 卡片渲染辅助 — subagent_lines（ink Line 行）→ ink TEXT 元素。

对齐 Claude Code：子代理活动渲染为**逐 agent 卡片**（``_subagent_render``
产出带边框 Line 行），经 ``ChatView`` 并入消息流显示（原独立 SubAgentPanel
组件已移除）。

★ 标准 React Ink 组件化（2026-08-05）：subagent_lines 数据格式从「ANSI
字符串行」迁移为「ink Line 行」（StyledRun）——本模块不再 ``ansi_to_runs``
解析 ANSI 字符串，直接 ``Line.runs`` 转 TEXT 标准组件（方向C 步骤8 唯一
ANSI → StyledRun 转换点已消除）；按终端宽度截断 + 换行转义语义保留。
"""

from __future__ import annotations

from src.tui.ink import h, TEXT, StyledRun, truncate_runs, use_memo
from src.tui._format import single_line


def _render_children(model, width: int) -> list:
    """构建 subagent 卡片子树（按行截断 + 样式 run）。

    subagent_lines 为 ``Line`` 行列表（_subagent_render 产出）：直接复用
    ``Line.runs``（StyledRun 行）按终端宽度截断，不再经 ANSI 解析。
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
        # 防御：行可能为纯文本（非 Line）——取 runs（Line）或按纯文本归一。
        runs = getattr(line, "runs", None)
        if runs is None:
            runs = [StyledRun(str(line), None)]
        # 强制单行契约：来源字段可能含 \n/\r，直接渲染会被终端按换行拆成
        # 两行——显示前转义为字面量（_subagent_render.format_tool_record
        # 已在源头转义；此处防御兜底）。
        text_runs = []
        for r in runs:
            if not r.text:
                continue
            if "\n" in r.text or "\r" in r.text:
                text_runs.append(StyledRun(single_line(r.text), r.style))
            else:
                text_runs.append(r)
        runs = truncate_runs(text_runs, width)
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
