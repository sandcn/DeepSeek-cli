"""布局容器抽象基类 — LayoutContainer。

所有布局容器（VBox/HBox/Flex）均继承此基类。
提供子元素管理、间距/内边距控制、递归深度保护等通用能力。
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import List, Optional

from ..widgets.base import Widget

_logger = logging.getLogger(__name__)

# ── 递归深度上限 ────────────────────────────────────────

_MAX_LAYOUT_DEPTH: int = 32
"""布局嵌套最大深度 — 防止无限递归导致栈溢出。"""


class LayoutContainer(Widget):
    """布局容器抽象基类。

    继承自 ``Widget``，作为所有布局容器（VBox/HBox/Flex）的基类。
    子类必须实现 ``get_content_width()`` 和 ``get_content_height()``。

    ## 子元素管理

    通过 ``add_child()`` / ``remove_child()`` 管理子元素。
    ``children`` 属性返回子元素列表的只读副本。

    ## 间距与内边距

    - ``spacing``: 子元素之间的间距（行数/列数），默认为 0。
    - ``padding``: 容器内边距 (top, right, bottom, left)，默认为 (0,0,0,0)。

    ## 递归深度保护

    添加子元素时检查布局嵌套深度，超过 ``_MAX_LAYOUT_DEPTH``（32 层）
    时抛出 ``RecursionError``。
    """

    def __init__(
        self,
        spacing: int = 0,
        padding: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        super().__init__()
        self._children: List[Widget] = []
        self._spacing: int = max(0, spacing)
        self._padding: tuple[int, int, int, int] = padding

    # ── 属性 ─────────────────────────────────────────

    @property
    def children(self) -> List[Widget]:
        """子元素列表（只读副本）。"""
        return list(self._children)

    @property
    def spacing(self) -> int:
        """子元素间距。"""
        return self._spacing

    @spacing.setter
    def spacing(self, value: int) -> None:
        self._spacing = max(0, value)

    @property
    def padding(self) -> tuple[int, int, int, int]:
        """内边距 (top, right, bottom, left)。"""
        return self._padding

    @padding.setter
    def padding(self, value: tuple[int, int, int, int]) -> None:
        if len(value) != 4:
            raise ValueError("padding 必须是 (top, right, bottom, left) 四元组")
        self._padding = value

    @property
    def child_count(self) -> int:
        """子元素数量。"""
        return len(self._children)

    @property
    def is_empty(self) -> bool:
        """是否无子元素。"""
        return len(self._children) == 0

    # ── 子元素管理 ───────────────────────────────────

    def add_child(self, widget: Widget) -> None:
        """添加子元素。

        Args:
            widget: 要添加的控件实例。

        Raises:
            TypeError: 参数不是 Widget 实例。
            RecursionError: 布局嵌套深度超限。
        """
        if not isinstance(widget, Widget):
            raise TypeError(
                f"add_child() 期望 Widget 实例，收到 {type(widget).__name__}"
            )
        depth = self._compute_depth()
        if depth >= _MAX_LAYOUT_DEPTH:
            raise RecursionError(
                f"布局嵌套深度已达上限 {_MAX_LAYOUT_DEPTH} 层，"
                f"当前深度 {depth}"
            )
        self._children.append(widget)

    def remove_child(self, widget: Widget) -> None:
        """移除子元素。

        Args:
            widget: 要移除的控件实例。

        Raises:
            ValueError: 控件不在子元素列表中。
        """
        try:
            self._children.remove(widget)
        except ValueError:
            raise ValueError(f"控件 {widget.widget_id} 不在子元素列表中") from None

    def clear_children(self) -> None:
        """清空所有子元素。"""
        self._children.clear()

    # ── 尺寸计算（抽象 — 子类实现） ─────────────────

    @abstractmethod
    def get_content_width(self, max_width: int = 80) -> int:
        """计算内容宽度。

        Args:
            max_width: 可用最大宽度（列数）。

        Returns:
            内容所需宽度（列数）。
        """

    @abstractmethod
    def get_content_height(self, max_height: int = 24) -> int:
        """计算内容高度。

        Args:
            max_height: 可用最大高度（行数）。

        Returns:
            内容所需高度（行数）。
        """

    # ── 渲染 ─────────────────────────────────────────

    @abstractmethod
    def render(self) -> str:
        """渲染布局内容。

        子类必须实现此方法，返回布局渲染后的 ANSI 文本。
        """

    # ── 内部方法 ─────────────────────────────────────

    def _compute_depth(self) -> int:
        """计算当前容器的布局嵌套深度。

        从根容器开始计数（根=1），沿所有子元素链取最大深度。
        """
        return self.__class__._max_child_depth(self, 0)

    @staticmethod
    def _max_child_depth(current: Widget, depth: int) -> int:
        """递归计算从 current 开始的最大嵌套深度。

        Args:
            current: 当前检查的 widget。
            depth: 当前累积深度。

        Returns:
            从 current 开始的最大嵌套深度。
        """
        if not isinstance(current, LayoutContainer):
            return depth
        if not current._children:
            return depth + 1
        max_depth = depth
        for child in current._children:
            child_depth = LayoutContainer._max_child_depth(child, depth + 1)
            max_depth = max(max_depth, child_depth)
        return max_depth

    def _apply_padding(self, lines: List[str]) -> List[str]:
        """对渲染行应用内边距。

        - 顶部/底部 padding：插入空行。
        - 左侧 padding：每行前加空格。

        Args:
            lines: 渲染后的行列表。

        Returns:
            应用 padding 后的行列表。
        """
        pt, pr, pb, pl = self._padding
        if pt == 0 and pb == 0 and pl == 0:
            return lines
        left_pad = " " * pl
        result: List[str] = []
        # 顶部 padding
        for _ in range(pt):
            result.append(left_pad)
        # 内容行
        for line in lines:
            result.append(left_pad + line)
        # 底部 padding
        for _ in range(pb):
            result.append(left_pad)
        return result
