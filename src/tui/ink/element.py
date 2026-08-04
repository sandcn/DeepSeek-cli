"""Element — 不可变组件元素（React Ink 风格）。

元素（Element）是组件树的基本构建单元：type 可以是 host 标签
（BOX/TEXT/STATIC/SPACER/APP）或 function component（Callable），
props 为样式/布局属性字典，children 为子元素元组。

与 React 元素一致：Element 是不可变数据，由 ``h()`` 工厂创建，
每次渲染（reconcile）都会基于最新 props 重新创建元素树。

零依赖：仅依赖 typing / dataclasses（Layer 0）。
"""

from __future__ import annotations

from src._compat import dataclass
from dataclasses import field
from typing import Any, Callable, Mapping, Sequence, Union

# ── host 组件标签常量（唯一真源） ──────────────────────────
BOX = "box"
TEXT = "text"
STATIC = "static"
SPACER = "spacer"
APP = "app"
#: Fragment — 透明分组容器（React Fragment 等价）。不产生独立布局盒，
#: 子节点直接流入父容器布局（layout_children 扁平化）。用于组件返回
#: 多个兄弟而不引入额外嵌套盒（BOX 会引入 padding/border/自身 box）。
FRAGMENT = "fragment"

#: 元素类型 — host 标签字符串或 function component。
ElementType = Union[str, Callable[..., Any]]

#: 子元素 — Element 或纯字符串（字符串自动转为 Text 元素）。
Child = Union["Element", str]


@dataclass(frozen=True)
class Element:
    """不可变组件元素。

    Attributes:
        type: host 标签（BOX/TEXT/STATIC/SPACER/APP）或 function component。
        props: 属性字典（副本，不可变）。
        children: 子元素元组（副本，不可变）。
    """

    type: ElementType
    props: Mapping[str, Any]
    children: tuple["Element", ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """规范化 props/children 为不可变副本。"""
        object.__setattr__(self, "props", dict(self.props) if self.props else {})
        object.__setattr__(self, "children", tuple(self.children))

    @property
    def key(self) -> str:
        """元素 key（用于调和；无 key 时以 type 为兜底）。"""
        key = self.props.get("key")
        if key is None:
            return _type_key(self.type)
        return str(key)


def _type_key(type_: ElementType) -> str:
    """将元素 type 转为字符串 key（函数组件带模块限定，消除跨模块同名冲突）。"""
    if isinstance(type_, str):
        return f"host:{type_}"
    mod = getattr(type_, "__module__", "?")
    name = getattr(type_, "__name__", repr(type_))
    return f"fn:{mod}.{name}"


def _as_element(child: Any) -> Element:
    """将子元素规范化为 Element（字符串 → Text 元素）。

    None/True/False 子级（React null/boolean 语义）转为空 Text 元素——
    ``_normalize_children`` 已在上游过滤（不创建 fiber）；此处兜底防御。
    bytes 子级解码为文本（``b"abc"`` → ``"abc"``）——修复前 ``str(b"abc")``
    渲染出 ``"b'abc'"``（repr 污染文本内容）。
    """
    if isinstance(child, Element):
        return child
    if child is None or child is True or child is False:
        return Element(TEXT, {"children": ""}, ())
    if isinstance(child, bytes):
        try:
            return Element(TEXT, {"children": child.decode("utf-8")}, ())
        except UnicodeDecodeError:
            return Element(TEXT, {"children": child.decode("utf-8", "replace")}, ())
    return Element(TEXT, {"children": str(child)}, ())


def _normalize_children(children: Sequence[Any]) -> tuple[Element, ...]:
    """扁平化子元素序列：list/tuple/生成器/迭代器子级展开，其余转为 Element。

    React 语义（方向1）：``None/True/False`` 子级渲染为空（不产生内容）。
    修复前 None 子级转为 Text "None"（如 ``h(BOX, None, [el, None, el2])``
    中间多出一行 "None" 文本）——条件式 children 的常见误用。

    ★ BUG-50（review 方向）：生成器/迭代器子级展开——修复前仅扁平化
    list/tuple，生成器（``h(BOX, None, (h(...) for ...))``）被 ``str()`` 转为
    ``<generator object ...>`` 文本静默渲染错误内容。非 str/bytes 的可迭代对象
    一律扁平展开（str/bytes 是 Iterable 但作为文本处理）。

    ★ 性能（PERF-18）：快速路径——仅对 ``tuple``（``h()`` 变参接收恒为
    tuple；生成器/迭代器/list 无 ``__len__`` 或需扁平化，不走快速路径）：
    空 children 直接返回 ``()``（免 ``tuple([])`` 新元组分配）；单 Element
    children（``h(Comp, {}, el)`` 变参——ChatView 每帧 1000+ 行
    ``h(TEXT, {...})`` 无子级、``h(Column, None, children_list)`` 单 list
    参数等场景）直接返回单元素元组（免 list 分配 + 遍历 + _as_element 调用）。
    子级为 list/生成器（需要扁平化）时回退完整路径。

    ★ 性能（PERF-23）：全 Element 子级快速路径——children 为 list/tuple 且
    **全部子级已是 Element**（如 ``h(Column, None, [el1, el2, ...])`` 大列表）
    时直接复用（list 转 tuple；tuple 原样返回），免逐元素 isinstance 检查 +
    ``_as_element`` 调用 + out 列表分配（1000 元素子级每帧省 1000 次
    ``_as_element`` 函数调用）。
    """
    if isinstance(children, (list, tuple)) and children:
        if all(isinstance(c, Element) for c in children):
            return children if isinstance(children, tuple) else tuple(children)
    if isinstance(children, tuple):
        if not children:
            return ()
        if len(children) == 1 and isinstance(children[0], Element):
            return (children[0],)
    out: list[Element] = []
    for c in children:
        if c is None or c is True or c is False:
            continue
        if isinstance(c, (list, tuple)):
            out.extend(_normalize_children(c))
        elif isinstance(c, (str, bytes)) or not hasattr(c, "__iter__"):
            out.append(_as_element(c))
        else:
            # 生成器/迭代器等通用 Iterable → 扁平展开（BUG-50）
            out.extend(_normalize_children(c))
    return tuple(out)


def h(type_: ElementType, props: Mapping[str, Any] | None = None, *children: Child) -> Element:
    """创建 Element（React ``createElement`` 等价）。

    Args:
        type_: host 标签或 function component。
        props: 属性字典（可选）。
        children: 子元素（Element / 字符串 / list / tuple，可变参数）。
            list/tuple 子级会被扁平展开。

    Returns:
        新的 Element 实例。
    """
    normalized = _normalize_children(children)
    return Element(type_, props or {}, normalized)


__all__ = [
    "BOX",
    "TEXT",
    "STATIC",
    "SPACER",
    "APP",
    "Element",
    "ElementType",
    "Child",
    "h",
]
