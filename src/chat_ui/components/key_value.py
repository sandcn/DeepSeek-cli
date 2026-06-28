"""KeyValue 组件 — React Ink 风格键值对展示。

提供 <KeyValue items={[("Name", "Alice"), ("Age", "30")]} /> 组件。
逐行渲染右对齐的键名（dim 样式）+ 冒号分隔 + 正常样式的值。

使用示例:
    kv = KeyValue(items=[("Name", "Alice"), ("Age", "30"), ("City", "Beijing")])
    print(kv.render())
    # 输出（ANSI dim 样式在键名上）:
    #     Name: Alice
    #      Age: 30
    #   City: Beijing
"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class KeyValue(TuiComponent):
    """React Ink 风格 KeyValue 组件。

    以对齐的键值对格式展示数据，键名使用 dim 样式以弱化视觉权重，
    值使用正常样式。支持自动或手动键对齐宽度。

    Props:
        items: 键值对列表 [(key, value), ...]。
        key_width: 键对齐宽度。0 表示自动取最长键宽度。
    """

    def __init__(self, items: list[tuple[str, str]] | None = None,
                 key_width: int = 0,
                 children: list[TuiComponent] | None = None) -> None:
        """初始化 KeyValue 组件。

        Args:
            items: 键值对列表，每个元素为 (key, value) 元组。
            key_width: 键的最小对齐宽度，0 表示自动计算。
            children: 子组件列表（保留兼容，当前未使用）。
        """
        super().__init__(children=children)
        self._items: list[tuple[str, str]] = list(items) if items else []
        self._key_width: int = key_width

    @property
    def key(self) -> str:
        return "key_value"

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Args:
            props: 可能包含 'items' 和 'key_width' 键的字典。

        Returns:
            True 如果 items 或 key_width 发生变化。
        """
        changed = False
        if "items" in props:
            new_items = list(props["items"])
            if new_items != self._items:
                self._items = new_items
                changed = True
        if "key_width" in props and props["key_width"] != self._key_width:
            self._key_width = props["key_width"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染键值对列表。

        1. 若 items 为空 → 返回空字符串。
        2. 计算 key_width：若为 0 则取 max(len(k) for k, v in items)，
           否则使用指定值。
        3. 每行格式: "  {key:>{width}}: {value}"，key 使用 dim 样式，
           value 使用正常样式。
        4. 通过 StyledText.assemble() 组装各段，返回 StyledText。

        Returns:
            格式化后的 StyledText 或空字符串。
        """
        if not self._items:
            return ""

        # 计算对齐宽度
        width = self._key_width
        if width <= 0:
            width = max(len(k) for k, _v in self._items)

        # 逐行组装
        segments: list[str | tuple[str, str] | StyledText] = []
        for i, (key_text, value_text) in enumerate(self._items):
            if i > 0:
                segments.append("\n")
            # "  " 前缀
            segments.append("  ")
            # key 右对齐，dim 样式
            padded_key = f"{key_text:>{width}}"
            segments.append((padded_key, "dim"))
            # ": " 分隔符
            segments.append(": ")
            # value 正常样式
            segments.append(value_text)

        return StyledText.assemble(*segments)

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。

        Returns:
            VNode(type="key_value", key="key_value", props=...)
        """
        from ..vdom.vnode import VNode
        rendered = self.render()
        vnode_props: dict[str, Any] = {
            "text": str(rendered) if rendered else "",
            "items": self._items,
            "key_width": self._key_width,
        }
        return VNode(
            type="key_value",
            key=self.key,
            props=vnode_props,
        )
