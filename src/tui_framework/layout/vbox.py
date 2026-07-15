"""VBox 垂直布局容器。

子元素按垂直方向从上到下排列。
支持 flex 权重分配剩余空间。
"""

from __future__ import annotations

from typing import List

from .container import LayoutContainer
from ..widgets.base import Widget

# ANSI 重置序列 — 防止转义序列在拼接时相互干扰
_RESET = "\033[0m"


class VBox(LayoutContainer):
    """垂直布局容器。

    子元素按从上到下的顺序垂直排列。每个子元素占据其 render() 返回的
    完整行数。子元素之间以 ``spacing`` 空行分隔。

    ## 尺寸计算

    - ``get_content_width()``: 返回所有子元素中最宽者。
    - ``get_content_height()``: 返回子元素行数之和 + 间距行数。

    ## flex 权重分配

    若子元素为 ``LayoutContainer`` 且设置了 ``flex_weight`` 属性
    （通过后续 Flex 包装），则按权重分配剩余垂直空间。

    ## 渲染

    渲染结果 = 顶部 padding 行 + 子元素行（逐元素拼接）+ 底部 padding 行。
    每个子元素之间插入 ``spacing`` 空行。
    """

    def __init__(
        self,
        spacing: int = 0,
        padding: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        super().__init__(spacing=spacing, padding=padding)

    # ── 尺寸计算 ─────────────────────────────────────

    def get_content_width(self, max_width: int = 80) -> int:
        """返回所有子元素中的最大宽度。

        空容器返回 0。
        """
        if self.is_empty:
            return 0
        max_w = 0
        for child in self._children:
            if not child.visible:
                continue
            if isinstance(child, LayoutContainer):
                cw = child.get_content_width(max_width)
            else:
                rendered = child.render()
                cw = max(len(line) for line in rendered.split('\n')) if rendered else 0
            if cw > max_w:
                max_w = cw
        return max_w

    def get_content_height(self, max_height: int = 24) -> int:
        """返回子元素行数之和 + 间距行数。

        空容器返回 0。
        """
        if self.is_empty:
            return 0
        total = 0
        visible_count = 0
        for child in self._children:
            if not child.visible:
                continue
            visible_count += 1
            if isinstance(child, LayoutContainer):
                total += child.get_content_height(max_height)
            else:
                rendered = child.render()
                total += rendered.count('\n') + 1 if rendered else 1
        # 间距 = (可见子元素数 - 1) * spacing
        if visible_count > 1:
            total += (visible_count - 1) * self._spacing
        return total

    # ── 渲染 ─────────────────────────────────────────

    def render(self) -> str:
        """垂直拼接所有可见子元素的渲染结果。

        不可见子元素（visible=False）被跳过，不占用空间。
        空容器返回空字符串。

        Returns:
            ANSI 文本 — 逐行垂直拼接。
        """
        if self.is_empty:
            return ""

        lines: List[str] = []
        first_visible = True

        for child in self._children:
            if not child.visible:
                continue
            if not first_visible:
                # 子元素间插入间距空行
                for _ in range(self._spacing):
                    lines.append("")
            first_visible = False

            rendered = child.render()
            if rendered:
                lines.extend(rendered.split('\n'))
            else:
                lines.append("")

        # 应用内边距
        lines = self._apply_padding(lines)
        return '\n'.join(lines)
