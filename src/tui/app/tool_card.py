"""tool_card — ToolCard 工具卡片标准控件组件（React Ink 标准控件/布局表达）。

工具卡片是 Claude Code 风格的「┌─ ● ⚡ bash · detail ─…─┐」卡片（顶边框内嵌
标题 + 主体行 + 底边框内嵌状态）。本组件用 React Ink 标准控件/布局表达：

  - **Column 布局容器**（标准布局）——卡片整体纵向堆叠。
  - **顶/底边框 + 主体行 → TEXT 标准控件**（styled runs）——每行一个 TEXT。

行内容构建复用 ``model._tool_card_styled_lines``（单一真源：帧级缓存
``_tool_card_frame_cache`` / 主体行缓存 ``_tool_card_body_lines_cache`` /
per-line wrap 缓存 ``_tool_card_body_cache`` / 省略提示 / 窄屏降级 / 呼吸色），
元素树为标准控件/布局包装——输出与既有行级渲染完全等价（行宽不变量保持）。

设计约束（与 CodeBlock 同模式）：
  - 不引入自定义 host：纯函数组件（Column + TEXT），reconciler 复用 fiber。
  - ``use_memo`` 不缓存完整元素：工具卡开放期间顶边框/状态图标为时间基呼吸色
    （time_glow 0.1s 桶），缓存会冻结动效——行构建由
    ``_tool_card_styled_lines`` 帧级缓存兜底（同桶零重建），元素每帧构建
    （fiber key 复用，开销可忽略）。
  - start/stop 参数支持增量提交协同（与 ``_block_styled_lines`` 语义一致）。
"""

from __future__ import annotations

from src.tui.ink import h, TEXT, Column


def _tool_card_children(block, width: int, start: int, stop):
    """构建工具卡行元素（Column 子节点：每行一个 TEXT）。

    行内容复用 ``model._tool_card_styled_lines``（单一真源）；每行给
    key（``tc-{i}``）——调和器复用 fiber，TEXT ``_wrap_cache`` 引用级命中
    （跨帧同 runs 列表对象 → 零重建）。
    """
    from src.tui.app.model import _tool_card_styled_lines
    lines = _tool_card_styled_lines(block, width, start, stop)
    return [
        h(TEXT, {"key": f"tc-{i}", "styled": runs, "height": 1})
        for i, runs in enumerate(lines)
    ]


def ToolCard(props) -> object:
    """工具卡片标准控件组件（Column + TEXT 标准控件/布局表达）。

    Props:
        block: 工具块（ChatBlock.kind == "tool"）。
        width: 卡片总宽度（终端列宽）；<=0 时按无边框主体行防御渲染。
        start: 起始 AnsiLine 下标（增量提交协同；默认 0——从块开头渲染）。
        stop: 结束下标（不含）；None 表示到块末尾。

    Returns:
        Column 元素（顶边框 + 主体行 + 底边框；块未关闭且非最终块时无底边框）。
    """
    block = props["block"]
    width = props.get("width", 0)
    start = props.get("start", 0)
    stop = props.get("stop", None)
    return h(Column, {"width": width}, _tool_card_children(block, width, start, stop))


__all__ = ["ToolCard"]
