"""Panel — 带标题边框面板控件（React Ink 风格，BOX border 标准布局）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——面板
独立成模块（公共辅助经 ``_display_common`` 共享）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, BOX, Element, h
from ._display_common import _color
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_children 原本地定义（与
#   focus/layout 逐字重复）——收敛至 _widget_common 单一真源。
from ._widget_common import _children

#: Panel 边框字符元组（复用 _BORDER_CHARS 同族：顶/底角 + 横/竖线）
_PANEL_BORDER_CHARS: dict[str, tuple[str, str, str, str, str, str]] = {
    "single": ("┌", "┐", "└", "┘", "─", "│"),
    "double": ("╔", "╗", "╚", "╝", "═", "║"),
    "round": ("╭", "╮", "╰", "╯", "─", "│"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃"),
    "classic": ("+", "+", "+", "+", "-", "|"),
    "dashed": ("┌", "┐", "└", "┘", "┄", "┆"),
}


def _panel_border_style(props: dict) -> Style:
    """解析 Panel 边框样式（``borderColor``/``borderStyle``；缺省暗青 23）。

    ★ P3（review）：``borderStyle`` 为 dict（React Ink v6 自定义边框对象
    ``{topLeft, top, topRight, left, bottomLeft, bottom, bottomRight, right}``）
    时**不支持 Style 解析**——防御性忽略（仅按 ``borderColor`` 建样式）；边框
    **字符**由 BOX 层 ``_paint_border._border_chars`` 消费 dict（Panel 对
    dict 原样透传，不 str() 化——见 Panel 内 ``borderStyle`` 透传）。Style
    对象原样返回。
    """
    style = props.get("borderStyle")
    if isinstance(style, Style):
        return style
    # dict 等非 Style 边框对象：仅解析 borderColor（边框字符由 _border_chars）
    fg = _color(props.get("borderColor"), 23)
    return Style(fg=fg)


def Panel(props: dict) -> Element:
    """React Ink 风格带标题边框面板控件（基于 BOX border 标准布局）。

    Props:
        title: 顶部标题（None/空时不显示标题行）。
        status: 底部状态文本（None 时不显示状态行）。
        width: 面板总宽（默认 60）。
        borderStyle: 边框变体（single/double/round/bold/classic/dashed）
            或 Style 对象（完整样式）或 dict 自定义边框对象
            （``{topLeft, top, topRight, left, bottomLeft, bottom, bottomRight,
            right}``——React Ink v6 语义，原样透传 BOX）。
        borderColor: 边框颜色（颜色名/int；默认暗青 23）。
        titleStyle: 标题样式（默认 None）。
        statusStyle: 状态样式（默认 None）。
        paddingLeft/paddingRight: 主体左右内边距（默认 1）。
        children: 主体内容（换行到内宽）。

    Returns:
        BOX 元素（border=1 标准边框 + 内部 Column）。

    实现（标准布局）：复用 BOX ``border`` 绘制完整四边框（``_paint_border``
    ——顶/底/左/右竖线自动覆盖全部行高），内部 Column 依次渲染标题行 +
    主体内容 + 状态行。标题/状态在边框内部（与 React Ink ``<Box>`` 面板
    语义一致）。
    """
    title = props.get("title")
    title = None if title is None else str(title)
    status = props.get("status")
    status = None if status is None else str(status)
    width = props.get("width")
    try:
        width = max(1, int(width)) if width is not None else 60
    except (TypeError, ValueError, OverflowError):
        width = 60
    try:
        pad = max(0, int(props.get("padding", 1)))
    except (TypeError, ValueError, OverflowError):
        pad = 1
    border_color = props.get("borderColor")
    # ★ P3（review）：borderStyle 透传——dict 自定义边框对象原样透传（不
    #   str() 化——修复前 ``str(dict)`` 生成 ``"{'topLeft': ...}"`` 无效变体，
    #   ``_border_chars`` 无法识别，dict 边框被忽略）；Style 对象原样透传
    #   （修复前 ``str(Style(fg=5))`` 把边框前景色丢失为字符串——``_border_style``
    #   无法识别回退默认暗青）；字符串变体保持 str()。
    bs = props.get("borderStyle", "single")
    # 标题/状态文本默认样式 = 边框色（解析后）；边框绘制经 BOX borderColor
    # 透传（components._border_style 消费）
    border_style = _panel_border_style(props)
    title_style = props.get("titleStyle")
    status_style = props.get("statusStyle")
    children = _children(props)

    inner: list = []
    if title:
        inner.append(h(TEXT, {
            "children": title, "style": title_style or border_style, "height": 1,
        }))
    inner.extend(children)
    if status:
        inner.append(h(TEXT, {
            "children": status, "style": status_style or border_style, "height": 1,
        }))
    # ★ 标准布局：BOX border 绘制完整边框（竖线自动覆盖全部行高——修复了
    #   Row 拼接方案的竖线高度问题）；内部 Column 填充标题 + 内容 + 状态。
    #   边框样式经 ``borderStyle``（字符串变体或 dict 自定义对象）与
    #   ``borderColor`` 透传——components/_paint_border 消费（缺省暗青 23）。
    return h(BOX, {
        "border": 1,
        "width": width,
        "borderStyle": bs if isinstance(bs, (dict, Style)) else str(bs),
        "borderColor": border_color,
        "paddingLeft": pad,
        "paddingRight": pad,
        "paddingTop": 1 if (title or children) else 0,
        "paddingBottom": 1 if (title or children or status) else 0,
    }, inner)


__all__ = ["Panel"]
