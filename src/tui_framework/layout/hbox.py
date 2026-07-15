"""HBox 水平布局容器。

子元素按水平方向从左到右排列。
支持顶部/居中/底部三种垂直对齐模式。
"""

from __future__ import annotations

from typing import List

from .container import LayoutContainer
from ..widgets.base import Widget

# ANSI 重置序列 — 拼接前追加，防止 ANSI 转义序列相互干扰
_RESET = "\033[0m"


# ── 对齐模式 ────────────────────────────────────────────

class HAlign:
    """水平布局垂直对齐模式常量。"""
    TOP: str = "top"
    """顶部对齐 — 所有子元素以顶部基线对齐。"""
    MIDDLE: str = "middle"
    """居中对齐 — 所有子元素垂直居中。"""
    BOTTOM: str = "bottom"
    """底部对齐 — 所有子元素以底部基线对齐。"""


class HBox(LayoutContainer):
    """水平布局容器。

    子元素按从左到右的顺序水平排列。每个子元素可以产生多行文本，
    HBox 逐行交错渲染所有子元素的对应行。

    ## 垂直对齐

    通过 ``align`` 参数控制子元素在垂直方向的对齐方式：

    - ``"top"``（默认）: 所有子元素顶部对齐，较矮的元素下方留空。
    - ``"middle"``: 所有子元素垂直居中。
    - ``"bottom"``: 所有子元素底部对齐，较矮的元素上方留空。

    ## 超宽处理

    若渲染总宽度超过可用宽度，按从左到右顺序逐个截断子元素。
    超宽的子元素会被截断到剩余可用宽度。

    ## ANSI 安全

    每个子元素渲染行末尾自动追加 ``RESET`` 序列，防止 ANSI 转义序列
    在水平拼接时污染后续子元素的颜色/样式。
    """

    def __init__(
        self,
        spacing: int = 0,
        padding: tuple[int, int, int, int] = (0, 0, 0, 0),
        align: str = HAlign.TOP,
    ) -> None:
        super().__init__(spacing=spacing, padding=padding)
        self._align: str = align
        if self._align not in (HAlign.TOP, HAlign.MIDDLE, HAlign.BOTTOM):
            raise ValueError(
                f"无效的对齐模式: {self._align!r}，"
                f"可选: top/middle/bottom"
            )

    # ── 属性 ─────────────────────────────────────────

    @property
    def align(self) -> str:
        """垂直对齐模式。"""
        return self._align

    @align.setter
    def align(self, value: str) -> None:
        if value not in (HAlign.TOP, HAlign.MIDDLE, HAlign.BOTTOM):
            raise ValueError(
                f"无效的对齐模式: {value!r}，可选: top/middle/bottom"
            )
        self._align = value

    # ── 尺寸计算 ─────────────────────────────────────

    def get_content_width(self, max_width: int = 80) -> int:
        """返回所有子元素宽度之和 + 间距。

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
                total += child.get_content_width(max_width)
            else:
                rendered = child.render()
                if rendered:
                    total += max(len(line) for line in rendered.split('\n'))
        if visible_count > 1:
            total += (visible_count - 1) * self._spacing
        return total

    def get_content_height(self, max_height: int = 24) -> int:
        """返回所有子元素中的最大高度。

        空容器返回 0。
        """
        if self.is_empty:
            return 0
        max_h = 0
        for child in self._children:
            if not child.visible:
                continue
            if isinstance(child, LayoutContainer):
                ch = child.get_content_height(max_height)
            else:
                rendered = child.render()
                ch = rendered.count('\n') + 1 if rendered else 1
            if ch > max_h:
                max_h = ch
        return max_h

    # ── 渲染 ─────────────────────────────────────────

    def render(self) -> str:
        """水平拼接所有可见子元素的渲染结果。

        渲染策略：
        1. 收集所有可见子元素的渲染行（逐元素 splitlines）。
        2. 确定最大行高。
        3. 根据对齐模式填充短元素的行。
        4. 逐行拼接所有元素的对应行，元素间插入间距空格。

        Returns:
            ANSI 文本 — 逐行水平拼接。
        """
        if self.is_empty:
            return ""

        # 收集可见子元素的渲染行
        visible_children: List[Widget] = []
        child_lines_list: List[List[str]] = []

        for child in self._children:
            if not child.visible:
                continue
            visible_children.append(child)
            rendered = child.render()
            if rendered:
                child_lines = rendered.split('\n')
            else:
                child_lines = [""]
            child_lines_list.append(child_lines)

        if not visible_children:
            return ""

        # 确定最大行高
        max_height = max(len(lines) for lines in child_lines_list)

        # 计算每个子元素的最大宽度（用于列宽）
        child_widths: List[int] = []
        for lines in child_lines_list:
            w = max((_visible_width(line) for line in lines), default=0)
            child_widths.append(w)

        # 间距字符串
        spacer = " " * self._spacing

        # 逐行拼接
        result_lines: List[str] = []
        for row_idx in range(max_height):
            row_parts: List[str] = []
            for col_idx, lines in enumerate(child_lines_list):
                # 确定该元素的当前行内容
                if row_idx < len(lines):
                    line = lines[row_idx]
                else:
                    line = ""
                # 根据对齐模式填充
                if self._align == HAlign.TOP:
                    # 顶部对齐：超出元素高度的行留空
                    padded_line = line
                elif self._align == HAlign.BOTTOM:
                    # 底部对齐：元素高度不足时顶部留空
                    offset = max_height - len(lines)
                    if row_idx < offset:
                        padded_line = ""
                    else:
                        padded_line = lines[row_idx - offset] if (row_idx - offset) < len(lines) else ""
                else:  # MIDDLE
                    # 居中对齐：元素高度不足时上下均分留空
                    offset = (max_height - len(lines)) // 2
                    actual_idx = row_idx - offset
                    if actual_idx < 0 or actual_idx >= len(lines):
                        padded_line = ""
                    else:
                        padded_line = lines[actual_idx]

                # 填充到列宽（用空格补齐，保持对齐）
                line_visible_width = _visible_width(padded_line)
                target_width = child_widths[col_idx]
                if line_visible_width < target_width:
                    padded_line = padded_line + " " * (target_width - line_visible_width)

                # 追加 RESET 防止 ANSI 污染下一列
                if padded_line:
                    padded_line = padded_line + _RESET
                row_parts.append(padded_line)

            result_lines.append(spacer.join(row_parts))

        # 应用内边距
        result_lines = self._apply_padding(result_lines)
        return '\n'.join(result_lines)


def _visible_width(text: str) -> int:
    """估算文本的可视宽度（去除 ANSI 转义序列后的字符数）。

    简化实现：去除所有 ``\033[...m`` 序列后计算 len。
    对于复杂场景（如宽字符），此处保持简单——布局系统
    的精确宽度应由调用方通过 ``terminal_width`` 约束。

    Args:
        text: 可能含 ANSI 转义序列的文本。

    Returns:
        可视字符宽度。
    """
    if not text:
        return 0
    # 去除 ANSI CSI 序列: \033[...m
    import re
    stripped = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', text)
    return len(stripped)
