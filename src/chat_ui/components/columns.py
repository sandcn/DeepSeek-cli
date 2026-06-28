"""Columns 组件 — React Ink 风格多列布局。

提供 <Columns> 组件，将子组件按行优先或列优先排列为多列布局。
支持自定义列数、列间距、终端宽度自适应截断。

使用示例:
    cols = Columns(columns=3, gap=2, row_first=True, children=[
        Text("A"), Text("B"), Text("C"), Text("D"),
    ])
    print(cols.render())
    # A  B  C
    # D
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from typing import TYPE_CHECKING

from ..components.base import TuiComponent
from ..components.box import _visual_width, _strip_ansi

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


def _pad_right(text: str, width: int) -> str:
    """将文本右填充到目标视觉宽度。

    Args:
        text: 原始文本（可能含 ANSI 序列）。
        width: 目标视觉宽度。

    Returns:
        填充空格后的文本，已足够宽时原样返回。
    """
    vw = _visual_width(text)
    if vw >= width:
        return text
    return text + " " * (width - vw)


def _truncate_left(text: str, max_width: int) -> str:
    """从左侧截断文本到目标视觉宽度。

    Args:
        text: 原始文本（可能含 ANSI 序列）。
        max_width: 最大视觉宽度。

    Returns:
        截断后的文本，未超出时原样返回。
    """
    if max_width <= 0:
        return ""
    clean = _strip_ansi(text)
    vw = _visual_width(clean)
    if vw <= max_width:
        return text

    # 从右向左保留 max_width 个视觉宽度字符
    kept = ""
    kept_w = 0
    for ch in reversed(clean):
        ch_w = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if kept_w + ch_w > max_width:
            break
        kept = ch + kept
        kept_w += ch_w

    # 如果原文本含 ANSI，需要重新包裹
    ansi_prefix = ""
    ansi_suffix = ""
    stripped = _strip_ansi(text)
    if stripped != text:
        # 提取 ANSI 前缀和重置后缀
        m = re.match(r'^(\033\[[\d;]*m)', text)
        if m:
            ansi_prefix = m.group(1)
        if text.endswith('\033[0m'):
            ansi_suffix = '\033[0m'

    return ansi_prefix + kept + ansi_suffix


class Columns(TuiComponent):
    """React Ink 风格的多列布局组件。

    将子组件按指定列数排列为网格布局，支持行优先和列优先两种排列策略。

    Props:
        columns: 列数（默认 2，至少为 1）。
        gap: 列间空格数（默认 2）。
        row_first: True=行优先（先填第一行所有列），False=列优先（先填第一列所有行）。
    """

    def __init__(
        self,
        columns: int = 2,
        gap: int = 2,
        row_first: bool = True,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._columns: int = max(1, columns)
        self._gap: int = max(0, gap)
        self._row_first: bool = row_first

    @property
    def key(self) -> str:
        return "columns"

    # ── update ──────────────────────────────────────────

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Returns:
            True 当 columns/gap/row_first 任一变更时。
        """
        changed = False

        if "columns" in props:
            new_cols = max(1, props["columns"])
            if new_cols != self._columns:
                self._columns = new_cols
                changed = True

        if "gap" in props:
            new_gap = max(0, props["gap"])
            if new_gap != self._gap:
                self._gap = new_gap
                changed = True

        if "row_first" in props:
            new_rf = bool(props["row_first"])
            if new_rf != self._row_first:
                self._row_first = new_rf
                changed = True

        return changed

    # ── 排列策略 ────────────────────────────────────────

    def _build_grid(
        self, outputs: list[str], num_cols: int
    ) -> list[list[list[str]]]:
        """将子组件输出按排列策略分配到二维网格。

        grid[r][c] = 该位置子组件的行列表（split by \\n）。

        Args:
            outputs: 各子组件 render() 的输出字符串列表。
            num_cols: 列数。

        Returns:
            二维网格，grid[row][col] = list[str]（该子组件的各行），
            空位为 [""]。
        """
        num_children = len(outputs)
        num_rows = (num_children + num_cols - 1) // num_cols

        # 初始化为空网格
        grid: list[list[list[str]]] = [
            [[""] for _ in range(num_cols)] for _ in range(num_rows)
        ]

        for i, output in enumerate(outputs):
            if self._row_first:
                # 行优先: child i → row = i // num_cols, col = i % num_cols
                r, c = divmod(i, num_cols)
            else:
                # 列优先: child i → col = i // num_rows, row = i % num_rows
                c = i // num_rows
                r = i % num_rows

            lines = output.split("\n") if output else [""]
            grid[r][c] = lines

        return grid

    # ── render ──────────────────────────────────────────

    def render(self) -> str:
        """渲染多列布局。

        流程:
            1. 逐个子组件 render() 获取输出
            2. 无子组件 → 返回 ""
            3. columns=1 → 退化为 render_children()
            4. 构建网格，计算各列最大宽度和各行最大行数
            5. 获取终端宽度，必要时按比例截断列宽
            6. 逐行逐列拼接输出

        Returns:
            多列格式化后的文本字符串。
        """
        children = self._ensure_children()
        if not children:
            return ""

        # 逐个子组件渲染
        outputs: list[str] = []
        for child in children:
            result = child.render()
            if isinstance(result, str):
                outputs.append(result)
            else:
                outputs.append(str(result))

        if not outputs or all(o == "" for o in outputs):
            return ""

        num_cols = self._columns

        # columns=1 → 退化为单列
        if num_cols == 1:
            return self.render_children()

        # 构建网格
        grid = self._build_grid(outputs, num_cols)
        num_rows = len(grid)

        # 计算每行最大行数
        row_heights: list[int] = []
        for r in range(num_rows):
            max_h = 0
            for c in range(num_cols):
                max_h = max(max_h, len(grid[r][c]))
            row_heights.append(max_h)

        # 计算每列最大视觉宽度
        col_widths: list[int] = [0] * num_cols
        for r in range(num_rows):
            for c in range(num_cols):
                for line in grid[r][c]:
                    vw = _visual_width(line)
                    if vw > col_widths[c]:
                        col_widths[c] = vw

        # 计算总宽度（含 gap）
        total_gap = self._gap * (num_cols - 1)
        total_width = sum(col_widths) + total_gap

        # 终端宽度自适应
        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80
        if term_width <= 0:
            term_width = 80

        # 超出终端宽度时按比例截断列宽
        if total_width > term_width:
            available = term_width - total_gap
            if available < num_cols:
                # 极小终端：每列至少 1 字符
                col_widths = [1] * num_cols
                # 调整到 available
                while sum(col_widths) > available and any(w > 1 for w in col_widths):
                    for c in range(num_cols):
                        if col_widths[c] > 1 and sum(col_widths) > available:
                            col_widths[c] -= 1
            else:
                # 按比例缩放
                original_total = sum(col_widths)
                if original_total > 0:
                    # 先按比例分配
                    new_widths: list[int] = []
                    allocated = 0
                    for c in range(num_cols):
                        w = int(col_widths[c] * available / original_total)
                        new_widths.append(w)
                        allocated += w
                    # 分配剩余像素（从宽列开始）
                    remaining = available - allocated
                    # 按原宽度降序分配
                    order = sorted(range(num_cols), key=lambda i: col_widths[i], reverse=True)
                    for idx in range(remaining):
                        c = order[idx % num_cols]
                        new_widths[c] += 1
                    col_widths = [max(1, w) for w in new_widths]

        # 逐行构建输出
        result_lines: list[str] = []
        for r in range(num_rows):
            row_h = row_heights[r]
            for line_idx in range(row_h):
                row_parts: list[str] = []
                for c in range(num_cols):
                    cell_lines = grid[r][c]
                    if line_idx < len(cell_lines):
                        line = cell_lines[line_idx]
                    else:
                        line = ""
                    # 截断超过列宽的行
                    vw = _visual_width(line)
                    if vw > col_widths[c]:
                        line = _truncate_left(line, col_widths[c])
                    # 填充到列宽
                    line = _pad_right(line, col_widths[c])
                    row_parts.append(line)
                result_lines.append((" " * self._gap).join(row_parts))

        return "\n".join(result_lines)

    # ── render_vnode ────────────────────────────────────

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染入口。"""
        from ..vdom.vnode import VNode
        result = self.render()
        return VNode(
            type="columns",
            key=self.key,
            props={
                "text": str(result) if result else "",
                "columns": self._columns,
                "gap": self._gap,
                "row_first": self._row_first,
            },
        )
