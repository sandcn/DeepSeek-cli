"""extra — React Ink 风格通用组件（Transform / Static 语义辅助）。

React Ink 完整语义补充（方向4 / 方向 G）：
  - ``Transform``：react-ink ``<Transform transform={fn}>text</Transform>``
    等价物——对文本子级应用字符串变换（uppercase/lowercase/截断/正则替换等），
    变换函数 ``(text: str) -> str`` 作用于 TEXT 叶子；嵌套 Element 递归应用。
    React Ink v6：transform 签名 ``(line, index) -> str``（逐输出行处理，
    附带零基行号）——按 handler 参数数量自动适配（2 参 → (line, index)）。
  - ``Static``：react-ink ``<Static>`` 等价物——**冻结子内容**（仅首帧求值，
    use_memo deps=() 缓存），适合「一次性渲染后不再变化」的静态输出（历史
    文本/横幅/说明）。React Ink v6：支持 ``items`` 数组模式
    （``<Static items={...}>{item => ...}</Static>``）——渲染 items 并冻结
    （items 引用变化时重建，引用稳定时复用）。

依赖约束：仅依赖 element / output / core.style / hooks（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

from typing import Callable

from .element import Element, TEXT, STATIC, h
from .hooks import use_memo

__all__ = ["Transform", "Static", "Newline", "Fragment", "STATIC_TEXT"]


#: STATIC 语义标注常量（供组件显式声明静态内容；当前 diff 已增量跳过未变化行）
STATIC_TEXT = "static-text"

#: transform 变换函数参数数缓存（handler 属性——免每帧 inspect.signature）
def _transform_arity(fn: Callable) -> int:
    """返回 transform 函数的位置参数数量（>=2 → (line, index) 签名）。"""
    cached = getattr(fn, "_ink_transform_arity", None)
    if cached is not None:
        return cached
    try:
        import inspect as _inspect
        sig = _inspect.signature(fn)
        n = sum(
            1 for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
    except (TypeError, ValueError):
        n = 1
    try:
        fn._ink_transform_arity = n
    except Exception:
        pass
    return n


def _apply_transform_to_text(text: str, fn: Callable[[str], str]) -> str:
    """对文本按行应用 transform（React Ink v6 逐行签名适配）。

    transform 接受 2+ 参数时按 ``(line, index)`` 逐行调用（React Ink v6
    语义——行号供 hanging indent 等场景）；否则按 ``(line)`` 单参调用
    （既有行为）。行内异常回退原行（健壮性）。
    """
    if _transform_arity(fn) >= 2:
        lines = text.split("\n")
        out: list[str] = []
        for i, line in enumerate(lines):
            try:
                out.append(fn(line, i))
            except Exception:
                out.append(line)
        return "\n".join(out)
    try:
        return fn(text)
    except Exception:
        return text


def _apply_transform_to_element(el: Element, fn: Callable[[str], str]) -> Element:
    """递归对元素树的 TEXT 叶子应用字符串变换（返回新元素树）。

    非 TEXT 容器（BOX/function/STATIC 等）保持 props/children 结构不变，
    仅对 TEXT 叶子的 ``children`` prop 应用 ``fn``。函数组件（callable type）
    不递归（无法静态展开——由 reconciler 运行时调用；Transform 用于文本变换
    场景，函数组件子级应自行处理）。
    """
    if isinstance(el.type, str) and el.type == TEXT:
        text = str(el.props.get("children", ""))
        new_text = _apply_transform_to_text(text, fn)
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
        # React Ink v6 逐行签名：
        h(Transform, {"transform": lambda line, index: ("  " * 4 + line) if index else line}, "a\nb")
        # → "a\n        b"

    Args:
        props: ``{"transform": fn, "children": str|Element|tuple, "style": Style|None}``
            - transform: ``(text: str) -> str`` 或 ``(line, index) -> str``
              （React Ink v6 逐行；None 时原样透传）。
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
    text = _apply_transform_to_text(text, transform)
    return h(TEXT, {"children": text, "style": style})


def _style_props_to_ink(style) -> dict:
    """React Ink style 对象转 host props（常见键透传 + kebab-case 转 camelCase）。

    React Ink ``<Static style={...}>`` / ``<Box style={...}>`` 的 style 为
    CSS-like 对象（如 ``{"padding": 1, "flex-direction": "column"}``）。
    """
    if not isinstance(style, dict):
        return {}
    out: dict = {}
    for k, v in style.items():
        if not isinstance(k, str):
            continue
        if "-" in k:
            parts = k.split("-")
            camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
            out[camel] = v
        else:
            out[k] = v
    return out


def Static(props: dict) -> Element:
    """React Ink ``<Static>`` 等价物（完善 react ink）：冻结子内容。

    children 经 ``use_memo(deps=())`` 仅首帧求值——后续帧即使父级传入不同
    children 也返回首次值（Static 语义：内容挂载后不变）。子树 fiber 复用
    （key 稳定）→ 换行缓存命中 → 帧 diff 身份短路 → 静态内容零重渲染。

    React Ink v6 ``items`` 数组模式：``props["items"]`` 非 None 时渲染每个
    item（``props["children"]`` 为 ``(item, index) -> Element`` 渲染函数），
    deps 为 items 引用——items 引用变化（新 items 追加）时重建，引用稳定时
    复用（旧 items 冻结不重渲染）。

    ``style`` prop：React Ink ``<Static style={...}>`` 容器样式——合并到
    STATIC host props（padding/margin/flexDirection 等布局键）。

    用法::

        h(Static, {}, "static text")          # 字符串子级
        h(Static, {}, h(BOX, None, [...]))    # Element 子级（冻结）
        h(Static, {"children": h(TEXT, ...)}) # 显式 children prop
        # items 模式：
        h(Static, {"items": tests, "children": lambda item, index: h(BOX, {"key": index}, h(TEXT, {"children": item}))})

    Args:
        props: ``{"children": str|Element|tuple|Callable, "items": list|None,
            "style": dict|None}``——子内容（变参子级经 reconciler 注入为
            Element 元组；首帧冻结）或 items 数组 + 渲染函数。

    Returns:
        STATIC host 元素（内含冻结 children）。
    """
    style_props = _style_props_to_ink(props.get("style"))
    items = props.get("items")
    if items is not None:
        # ── items 数组模式（React Ink v6）──
        render_fn = props.get("children")
        if not callable(render_fn):
            render_fn = lambda item, index: str(item)
        items_tuple = tuple(items) if not isinstance(items, (tuple, list)) else items
        frozen = use_memo(
            lambda: tuple(
                render_fn(item, index) for index, item in enumerate(items_tuple)
            ),
            (items_tuple,),
        )
        return h(STATIC, style_props, frozen)
    children = props.get("children")
    if children is None:
        children = ()
    if isinstance(children, (tuple, list)):
        children = tuple(children)
    frozen = use_memo(lambda: children, ())
    return h(STATIC, style_props, frozen)


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
