"""subagent 卡片渲染 — React Ink 标准组件 SubAgentCard。

对齐 Claude Code：子代理活动渲染为**逐 agent 卡片**（``_subagent_render``
产出带边框 Line 行），经 ``ChatView`` 并入消息流显示（原独立 SubAgentPanel
组件已移除）。

★ 标准 React Ink 组件化（2026-08-05 收尾，无例外）：subagent 卡片数据
（``subagent_lines``，ink Line 行）经**标准函数组件** ``SubAgentCard`` 渲染
——组件树表达 ``h(SubAgentCard, {"lines": ..., "width": ...})``（与
``ToolCard`` / ``OpenBlockLines`` 同模式）。内部 ``use_memo`` 缓存行 TEXT
元素列表（deps = ``(lines, width)`` 引用级——控制器推送新列表时引用变化
自动重算；无变化帧返回同一 children 元组 → reconciler props 引用级命中）。
组件内 hook 占用子 fiber 槽位，不再消耗 ChatView 的 hook 顺序。组件卸载/
挂载由 ``model.subagent_lines`` 空/非空自动驱动。

subagent_lines 数据格式为「ink Line 行」（StyledRun），本模块直接复用
``Line.runs`` 转 TEXT 标准组件；按终端宽度截断 + 换行转义语义保留。

2026-08-05 死代码清理：旧兼容辅助 ``_render_children`` /
``use_subagent_children``（deprecated，生产无调用方）已删除——既有测试
``test_bugfix_round12.py::test_subagent_children_memoized`` 已迁移为
验证 ``SubAgentCard`` 的 use_memo 缓存行为。
"""

from __future__ import annotations

from src.tui.ink import h, TEXT, StyledRun, FRAGMENT, truncate_runs, use_memo
from src.tui._format import single_line

__all__ = ["SubAgentCard"]


def _lines_to_children(lines, width: int) -> list:
    """构建 subagent 卡片子树（按行截断 + 样式 run）。

    subagent_lines 为 ``Line`` 行列表（_subagent_render 产出）：直接复用
    ``Line.runs``（StyledRun 行）按终端宽度截断，不再经 ANSI 解析。
    每行给索引 key（调和器复用 fiber，换行/样式缓存可命中）。

    本函数为**纯计算**（无 hook）；组件内用 ``use_memo`` 按
    ``(lines, width)`` 引用缓存结果（BUG-42：修复前每帧重建全部
    StyledRun/Element → TEXT ``_wrap_cache`` 身份键恒 miss，活跃 subagent
    期间每帧重包裹）。
    """
    children = []
    for i, line in enumerate(lines or []):
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


def SubAgentCard(props: dict) -> object:
    """React Ink 标准组件：SubAgent 活动卡片（边框行 + 内容行）。

    Props:
        lines: list[Line] — subagent_lines（_subagent_render 产出，带边框）。
        width: 布局宽度（截断宽度同源）。

    Returns:
        Fragment（透明分组容器）——行 TEXT 子元素直接流入父容器布局
        （与 OpenBlockLines 同模式；不引入额外布局盒/高度）。

    ★ 性能（PERF-26 同族）：use_memo 缓存 children（deps = lines 引用 +
    width）——无变化帧返回同一 children 元组 → reconciler props 引用级命中
    → 免每帧重建 N 行 TEXT Element；控制器推送新列表（引用变化）自动重算。
    """
    lines = props.get("lines") or []
    width = props.get("width", 0)
    children = use_memo(
        lambda: _lines_to_children(lines, width),
        (lines, width),
    )
    return h(FRAGMENT, None, children)
