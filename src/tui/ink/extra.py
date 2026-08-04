"""extra — React Ink 风格通用组件（Transform / Static 语义辅助）。

React Ink 完整语义补充（方向4）：
  - ``Transform``：react-ink ``<Transform transform={fn}>text</Transform>``
    等价物——对文本子级应用字符串变换（uppercase/lowercase/截断/正则替换等），
    变换函数 ``(text: str) -> str`` 作用于 TEXT 叶子；嵌套 Element 递归应用。
  - ``Static``：react-ink ``<Static>`` 等价物——**冻结子内容**（仅首帧求值，
    use_memo deps=() 缓存），适合「一次性渲染后不再变化」的静态输出（历史
    文本/横幅/说明）。后续帧子树 fiber 复用 + 换行缓存命中 + 帧 diff 身份
    短路 → 静态内容零重渲染。

依赖约束：仅依赖 element / output / core.style / hooks（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

from typing import Callable

from .element import Element, TEXT, STATIC, h
from .hooks import use_memo

__all__ = ["Transform", "Static", "Newline", "Fragment", "STATIC_TEXT"]


#: STATIC 语义标注常量（供组件显式声明静态内容；当前 diff 已增量跳过未变化行）
STATIC_TEXT = "static-text"


def _apply_transform_to_element(el: Element, fn: Callable[[str], str]) -> Element:
    """递归对元素树的 TEXT 叶子应用字符串变换（返回新元素树）。

    非 TEXT 容器（BOX/function/STATIC 等）保持 props/children 结构不变，
    仅对 TEXT 叶子的 ``children`` prop 应用 ``fn``。函数组件（callable type）
    不递归（无法静态展开——由 reconciler 运行时调用；Transform 用于文本变换
    场景，函数组件子级应自行处理）。
    """
    if isinstance(el.type, str) and el.type == TEXT:
        text = str(el.props.get("children", ""))
        try:
            new_text = fn(text)
        except Exception:
            new_text = text
        return Element(el.type, {**el.props, "children": new_text}, el.children)
    new_children = tuple(
        _apply_transform_to_element(c, fn) for c in el.children
    )
    return Element(el.type, el.props, new_children)


def Transform(props: dict) -> Element:
    """React Ink ``<Transform>`` 等价物（完善 react ink）。

    用法::

        h(Transform, {"transform": lambda s: s.upper()}, "hello")
        # → TEXT("HELLO")
        h(Transform, {"transform": lambda s: s.upper(), "children": h(TEXT, {"children":"hi"})})
        # → TEXT("HI")（递归应用到 TEXT 叶子）

    Args:
        props: ``{"transform": fn, "children": str|Element|tuple, "style": Style|None}``
            - transform: ``(text: str) -> str`` 变换函数（None 时原样透传）。
            - children: 文本字符串 / Element / 变参子级元组（reconciler 注入
              ``props["children"]`` 为 Element 元组；字符串为显式 children prop）。
            - style: 顶层 TEXT 样式（children 为 str 时应用）。

    Returns:
        TEXT 元素（children 为 str）或变换后的元素树（children 为 Element/元组）。
    """
    children = props.get("children")
    transform = props.get("transform")
    style = props.get("style")
    if isinstance(children, (tuple, list)):
        # 变参子级（reconciler 注入 Element 元组）：单元素递归应用；多元素
        # 以 Fragment 包裹（不引入额外布局盒）。
        if len(children) == 1:
            child = children[0]
            if transform is None:
                return child
            return _apply_transform_to_element(child, transform)
        transformed = tuple(
            (_apply_transform_to_element(c, transform) if isinstance(c, Element) else c)
            for c in children
        )
        return Element("fragment", {}, transformed)
    if transform is None:
        if isinstance(children, Element):
            return children
        text = "" if children is None else str(children)
        return h(TEXT, {"children": text, "style": style})
    if isinstance(children, Element):
        return _apply_transform_to_element(children, transform)
    text = "" if children is None else str(children)
    try:
        text = transform(text)
    except Exception:
        pass
    return h(TEXT, {"children": text, "style": style})


def Static(props: dict) -> Element:
    """React Ink ``<Static>`` 等价物（完善 react ink）：冻结子内容。

    children 经 ``use_memo(deps=())`` 仅首帧求值——后续帧即使父级传入不同
    children 也返回首次值（Static 语义：内容挂载后不变）。子树 fiber 复用
    （key 稳定）→ 换行缓存命中 → 帧 diff 身份短路 → 静态内容零重渲染。

    用法::

        h(Static, {}, "static text")          # 字符串子级
        h(Static, {}, h(BOX, None, [...]))    # Element 子级（冻结）
        h(Static, {"children": h(TEXT, ...)}) # 显式 children prop

    Args:
        props: ``{"children": str|Element|tuple}``——子内容（变参子级经
            reconciler 注入为 Element 元组；首帧冻结）。

    Returns:
        STATIC host 元素（内含冻结 children）。
    """
    children = props.get("children")
    if children is None:
        children = ()
    if isinstance(children, (tuple, list)):
        children = tuple(children)
    frozen = use_memo(lambda: children, ())
    return h(STATIC, None, frozen)


def Newline(props: dict) -> Element:
    """React Ink ``<Newline>`` 等价物（完善 react ink）：渲染换行。

    用法::

        h(BOX, None, [h(TEXT, {"children": "line1"}), h(Newline), h(TEXT, {"children": "line2"})])

    Args:
        props: ``{"count": int}``——换行行数（默认 1）；``count<=0`` 视为 1。

    Returns:
        TEXT 元素（children 为 ``"\\n" * count``）——``wrap_runs_by_width``
        将 ``\\n`` 作为强制换行拆行，渲染出 count 个空行。
    """
    count = props.get("count", 1)
    try:
        count = max(1, int(count))
    except (TypeError, ValueError, OverflowError):
        count = 1
    return h(TEXT, {"children": "\n" * count})


def Fragment(props: dict) -> Element:
    """Fragment 等价物（完善 react ink）：透明分组容器。

    ``h(Fragment, {}, child1, child2)`` 返回 ``fragment`` host——布局/绘制时
    子节点直接流入父容器（不引入独立布局盒），与 ``h("fragment", ...)`` 等价。
    函数组件形式便于惯用命名（``<>...</>`` 的 Python 对应）。
    """
    children = props.get("children", ())
    if isinstance(children, (tuple, list)):
        return Element("fragment", {}, tuple(children))
    if children is None:
        return Element("fragment", {}, ())
    return Element("fragment", {}, (children,))
