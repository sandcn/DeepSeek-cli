"""OrderedList 组件 — React Ink 风格有序列表。

提供 <OrderedList items={[...]} start={1}> 组件。
逐项渲染带递增数字前缀的列表项，用换行符拼接。

使用示例:
    ol = OrderedList(items=["第一步", "第二步", "第三步"], start=1)
    print(ol.render())  # 输出 "  1. 第一步\n  2. 第二步\n  3. 第三步"
"""

from __future__ import annotations
from typing import Any
from ..components.base import TuiComponent
from ..infrastructure.styled import StyledText


class OrderedList(TuiComponent):
    """React Ink 风格 OrderedList 组件。

    属性:
        items: 列表项文本列表。
        start: 起始编号（默认 1）。
    """

    def __init__(self, items: list[str] | None = None, start: int = 1,
                 children: list[TuiComponent] | None = None, **props: Any) -> None:
        super().__init__(children=children)
        self._items: list[str] = list(items) if items else []
        self._start: int = start

    @property
    def key(self) -> str:
        return "ordered_list"

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Args:
            props: 可能包含 'items' 和 'start' 键的字典。

        Returns:
            True 如果 items 或 start 发生变化。
        """
        changed = False
        if "items" in props:
            self._items = list(props["items"])
            changed = True
        if "start" in props:
            self._start = props["start"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染有序列表。

        对 items 中的每一项输出 "  {i}. {item}" 格式（i 从 start 递增），
        用换行符拼接。无列表项时返回空字符串。

        Returns:
            格式化后的列表文本字符串。
        """
        if not self._items:
            return ""
        lines = [
            f"  {i}. {item}"
            for i, item in enumerate(self._items, start=self._start)
        ]
        return "\n".join(lines)
