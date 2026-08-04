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

from ..element import BOX, Element, h

__all__ = ["Row", "Column", "Center", "Stack", "HStack", "VStack", "Grid", "ZStack"]


def _children(props: dict):
    """读取 reconciler 注入的 children（Element 元组；无子级时空元组）。"""
    children = props.get("children", ())
    if children is None:
        return ()
    if isinstance(children, (list, tuple)):
        return tuple(children)
    return (children,)


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
    except (TypeError, ValueError):
        columns = 1
    try:
        gap = max(0, int(props.get("gap", 0)))
    except (TypeError, ValueError):
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
        rows.append(h(BOX, {"flexDirection": "row", "gap": gap, "width": "100%"}, *cells))
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
