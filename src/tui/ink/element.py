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
    """将元素 type 转为字符串 key。"""
    if isinstance(type_, str):
        return f"host:{type_}"
    return f"fn:{getattr(type_, '__name__', repr(type_))}"


def _as_element(child: Any) -> Element:
    """将子元素规范化为 Element（字符串 → Text 元素）。"""
    if isinstance(child, Element):
        return child
    return Element(TEXT, {"children": str(child)}, ())


def _normalize_children(children: Sequence[Any]) -> tuple[Element, ...]:
    """扁平化子元素序列：list/tuple 子级展开，其余转为 Element。"""
    out: list[Element] = []
    for c in children:
        if isinstance(c, (list, tuple)):
            out.extend(_normalize_children(c))
        else:
            out.append(_as_element(c))
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
