"""layout — React Ink 风格布局容器组件（Row / Column / Center / Stack / Grid / ZStack）。

基于 BOX 布局原语组合的语义容器：
  - Row / Column   — 显式 flexDirection 的 BOX 别名（SwiftUI 风格命名）。
  - Center         — 双轴居中容器（justifyContent=center + alignItems=center）。
  - Stack          — 纵向堆叠容器（默认 column + gap，SwiftUI VStack 风格）。
  - HStack / VStack— Stack 的横向/纵向变体。
  - Grid           — CSS Grid 风格栅格（``columns`` 列数 + ``gap`` 间距；
    子节点按行分组，每行 flexGrow=1 等宽填充，复用 flexbox 布局引擎）。
  - ZStack         — 层叠容器（所有子节点 position="absolute" 叠放，配合
    绝对定位第二遍布局；Z 顺序 = 元素声明顺序，后者在上）。

依赖约束：仅依赖 element（Layer 0）。
"""

from __future__ import annotations

from ..element import BOX, TEXT, Element, h
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_children 原本地定义（与
#   focus/_panel 逐字重复）——收敛至 _widget_common 单一真源。
from ._widget_common import _children

__all__ = [
    "Row", "Column", "Box", "Text", "Flex", "Spacer",
    "Center", "Stack", "HStack", "VStack", "Grid", "ZStack",
]


def Row(props: dict) -> Element:
    """横向布局容器（flexDirection="row"）。子节点水平排列。"""
    p = dict(props)
    p["flexDirection"] = "row"
    return h(BOX, p, *_children(props))


def Column(props: dict) -> Element:
    """纵向布局容器（flexDirection="column"）。子节点垂直排列。"""
    p = dict(props)
    p["flexDirection"] = "column"
    return h(BOX, p, *_children(props))


def Box(props: dict) -> Element:
    """React Ink ``<Box>`` 门面：flexbox 布局容器（默认 column，与 Flex 一致）。

    与 ``h(BOX, ...)`` 等价——提供 React Ink 生态命名（``<Box>`` 是
    react-ink 最常用容器组件）。props 透传（flexDirection 由调用方决定，
    缺省 column——React Ink 默认）。Row/Column 为固定方向便捷门面，
    Box/Flex 为通用容器。
    """
    p = dict(props)
    p.setdefault("flexDirection", "column")
    return h(BOX, p, *_children(props))


def _text_children_value(children) -> str:
    """从 Text props 的 children 提取渲染文本（字符串 or reconciler 注入元组）。

    React Ink 语义：``<Text>`` 的 children 属于 props，通常为字符串
    （``h(Text, {"children": "a"})``）。变参用法（``h(Text, None, "a")``）
    经 reconciler 注入为 Element 元组（``(TEXT('a'),)``）——归一化为首元素
    文本（单文本场景）；空/无子级回退空串。
    """
    if children is None or children == "":
        return ""
    if isinstance(children, (tuple, list)):
        if not children:
            return ""
        first = children[0]
        if isinstance(first, str):
            return first
        if isinstance(first, Element) and first.type == TEXT:
            return str(first.props.get("children", ""))
        return str(first)
    return children


def Text(props: dict) -> Element:
    """React Ink ``<Text>`` 门面：文本组件（等价 ``h(TEXT, ...)``）。

    提供 React Ink 生态命名（``<Text>`` 是 react-ink 文本组件）。
    文本内容经 ``props["children"]``（字符串）或变参（``h(Text, None, "a")``）
    传递——门面统一归一化为字符串。props 透传（children/style/styled/
    textWrap/transform 等完整语义）。

    用法::

        h(Text, {"children": "hello", "style": Style(fg=45)})
        h(Text, {"styled": [StyledRun("hi", Style(fg=1))]})
    """
    p = dict(props)
    p["children"] = _text_children_value(p.get("children", ""))
    return h(TEXT, p)


def Flex(props: dict) -> Element:
    """React Ink ``<Box>`` 显式门面：flexbox 布局容器。

    与 Row/Column 的区别：``flexDirection`` 由调用方经 props 显式指定
    （缺省 column——React Ink 默认）。Row/Column 为固定方向的便捷门面，
    Flex 为通用 flexbox 容器（动态方向/完整 flexbox props 场景）。

    Props:
        flexDirection: "column"（默认）| "row"。
        （其余 flexbox props 与 BOX 一致：justifyContent/alignItems/
        flexGrow/flexShrink/gap/padding/border/margin/width/height 等）

    Returns:
        BOX 元素（flexDirection 由 props 决定）。
    """
    p = dict(props)
    p.setdefault("flexDirection", "column")
    return h(BOX, p, *_children(props))


def Spacer(props: dict) -> Element:
    """React Ink ``<Spacer>`` 等价物：占位撑开组件。

    Row 容器中 ``flexGrow=1`` 撑开剩余水平空间（把前后内容推到两端/按
    justifyContent 分布）；Column 容器中撑开剩余纵向空间（高度由容器
    约束决定）。等价 ``h(SPACER, {"flexGrow": 1, **props})``。

    Props:
        flexGrow: 拉伸权重（默认 1）；0 表示不拉伸。
        （其余 props 与 SPACER 元素一致：width/height 固定占位等）

    Returns:
        SPACER 元素（默认 flexGrow=1）。
    """
    from ..element import SPACER
    p = dict(props)
    p.setdefault("flexGrow", 1)
    return h(SPACER, p)


def Center(props: dict) -> Element:
    """双轴居中容器（justifyContent="center" + alignItems="center"）。"""
    p = dict(props)
    p["justifyContent"] = "center"
    p["alignItems"] = "center"
    return h(BOX, p, *_children(props))


def Stack(props: dict) -> Element:
    """纵向堆叠容器（默认 column + gap 间距；SwiftUI VStack 风格）。"""
    p = dict(props)
    p.setdefault("flexDirection", "column")
    p.setdefault("gap", 0)
    return h(BOX, p, *_children(props))


def HStack(props: dict) -> Element:
    """横向堆叠容器（row + gap 间距；SwiftUI HStack 风格）。"""
    p = dict(props)
    p["flexDirection"] = "row"
    p.setdefault("gap", 0)
    return h(BOX, p, *_children(props))


def VStack(props: dict) -> Element:
    """纵向堆叠容器（column + gap 间距；SwiftUI VStack 风格）。"""
    p = dict(props)
    p["flexDirection"] = "column"
    p.setdefault("gap", 0)
    return h(BOX, p, *_children(props))


def Grid(props: dict) -> Element:
    """CSS Grid 风格栅格容器。

    Props:
        columns: 每行列数（默认 1）。
        gap: 单元格间距（行/列通用，默认 0）。
        children: 子元素（按行填充；不足一行的补齐空格）。

    实现：子节点按 ``columns`` 分组为行 BOX（flexDirection=row + gap），
    每个 cell 包 ``flexGrow=1``（等宽填充列宽）；行间经外层 column BOX
    的 gap 分隔。复用 flexbox 布局引擎，无需自定义 host。
    """
    children = _children(props)
    try:
        columns = max(1, int(props.get("columns", 1)))
    except (TypeError, ValueError, OverflowError):
        columns = 1
    try:
        gap = max(0, int(props.get("gap", 0)))
    except (TypeError, ValueError, OverflowError):
        gap = 0
    rows = []
    for i in range(0, len(children), columns):
        row_children = children[i:i + columns]
        cells = []
        for child in row_children:
            if isinstance(child, Element):
                cp = dict(child.props)
                cp["flexGrow"] = 1
                cp["flexShrink"] = 1
                cells.append(Element(child.type, cp, child.children))
            else:
                cells.append(child)
        # 行 BOX 显式占满可用宽度（width="100%"）→ 内部 cell flexGrow 等宽
        # ★ 阶段2（标准布局容器重构）：row BOX → Row（语义化门面，输出等价）。
        rows.append(h(Row, {"gap": gap, "width": "100%"}, *cells))
    p = dict(props)
    p.pop("children", None)
    p.pop("columns", None)
    p["flexDirection"] = "column"
    p["gap"] = gap
    return h(BOX, p, *rows)


def ZStack(props: dict) -> Element:
    """层叠容器（SwiftUI ZStack 风格）：所有子节点绝对定位叠放。

    每个子节点包 ``position="absolute"``（left=0/top=0 缺省），容器自身
    ``position="relative"`` 作为定位基准。Z 顺序 = 元素声明顺序（后者绘制
    在上，与树遍历 paint 顺序一致）。

    Props:
        children: 子元素（Element 被包装为 absolute；纯字符串转为 TEXT）。
    """
    children = _children(props)
    wrapped = []
    for child in children:
        if isinstance(child, Element):
            cp = dict(child.props)
            cp["position"] = "absolute"
            cp.setdefault("left", 0)
            cp.setdefault("top", 0)
            wrapped.append(Element(child.type, cp, child.children))
        else:
            wrapped.append(child)
    p = dict(props)
    p.pop("children", None)
    p["position"] = "relative"
    return h(BOX, p, *wrapped)
