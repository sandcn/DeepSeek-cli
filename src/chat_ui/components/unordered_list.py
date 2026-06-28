"""UnorderedList 组件 — React Ink 风格无序列表。

提供 <UnorderedList items={[...]} marker="•"> 组件。
逐项渲染带标记前缀的列表项，用换行符拼接。

使用示例:
    ul = UnorderedList(items=["苹果", "香蕉", "橘子"], marker="-")
    print(ul.render())  # 输出 "  - 苹果\n  - 香蕉\n  - 橘子"
"""

from __future__ import annotations
from typing import Any
from ..components.base import TuiComponent
from ..infrastructure.styled import StyledText


class UnorderedList(TuiComponent):
    """React Ink 风格 UnorderedList 组件。

    属性:
        items: 列表项文本列表。
        marker: 前缀标记字符（默认 "•"），常用值 "•" / "-" / "*"。
    """

    def __init__(self, items: list[str] | None = None, marker: str = "•",
                 children: list[TuiComponent] | None = None, **props: Any) -> None:
        super().__init__(children=children)
        self._items: list[str] = list(items) if items else []
        self._marker: str = marker

    @property
    def key(self) -> str:
        return "unordered_list"

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Args:
            props: 可能包含 'items' 和 'marker' 键的字典。

        Returns:
            True 如果 items 或 marker 发生变化。
        """
        changed = False
        if "items" in props:
            self._items = list(props["items"])
            changed = True
        if "marker" in props:
            self._marker = props["marker"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染无序列表。

        对 items 中的每一项输出 "  {marker} {item}" 格式，
        用换行符拼接。无列表项时返回空字符串。

        Returns:
            格式化后的列表文本字符串。
        """
        if not self._items:
            return ""
        lines = [f"  {self._marker} {item}" for item in self._items]
        return "\n".join(lines)
