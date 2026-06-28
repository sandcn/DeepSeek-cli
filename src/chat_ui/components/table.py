"""Table 组件 — React Ink 风格表格组件。

提供 <Table> 组件，使用 Unicode box-drawing 字符渲染表格。
支持自动列宽计算、对齐方式（左/中/右）、动态数据更新。

边框字符集：
  ┌──┬──┐  上边框（top-left, horizontal, top-mid, horizontal, top-right）
  ├──┼──┤  表头分隔（left-mid, horizontal, mid-mid, horizontal, right-mid）
  └──┴──┘  下边框（bottom-left, horizontal, bottom-mid, horizontal, bottom-right）
  │        竖线

使用示例:
    table = Table(
        headers=["Name", "Age"],
        rows=[["Alice", "30"], ["Bob", "25"]],
        align=["left", "right"],
    )
    print(table.render())
    # ┌────────┬──────┐
    # │ Name   │ Age  │
    # ├────────┼──────┤
    # │ Alice  │   30 │
    # │ Bob    │   25 │
    # └────────┴──────┘
"""

from __future__ import annotations

from .base import TuiComponent
from .box import _visual_width, _strip_ansi
from ..infrastructure.ansi import ANSI_BOLD, ANSI_RESET
from ..vdom.vnode import VNode


class Table(TuiComponent):
    """React Ink 风格的表格组件。

    Props:
        headers: list[str] — 表头列名
        rows: list[list[str]] — 数据行
        widths: list[int] | None — 列宽（None 时自动计算，取每列最宽字符串视觉宽度 + 2）
        align: list[str] — 对齐方式 ("left"/"center"/"right")，默认全 left
    """

    def __init__(
        self,
        headers: list[str] | None = None,
        rows: list[list[str]] | None = None,
        widths: list[int] | None = None,
        align: list[str] | None = None,
        children=None,
    ):
        super().__init__(children=children)
        self._headers: list[str] = list(headers) if headers else []
        self._rows: list[list[str]] = [list(row) for row in rows] if rows else []
        self._widths: list[int] | None = list(widths) if widths else None
        self._align: list[str] = list(align) if align else []

    @property
    def key(self) -> str:
        return "table"

    # ── update ──────────────────────────────────────────

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Returns:
            True 当 headers/rows/widths/align 任一变更时。
        """
        changed = False

        if "headers" in props:
            new_headers = list(props["headers"])
            if new_headers != self._headers:
                self._headers = new_headers
                changed = True

        if "rows" in props:
            new_rows = [list(row) for row in props["rows"]]
            if new_rows != self._rows:
                self._rows = new_rows
                changed = True

        if "widths" in props:
            new_widths = list(props["widths"]) if props["widths"] is not None else None
            if new_widths != self._widths:
                self._widths = new_widths
                changed = True

        if "align" in props:
            new_align = list(props["align"]) if props.get("align") else []
            if new_align != self._align:
                self._align = new_align
                changed = True

        return changed

    # ── 列宽计算 ────────────────────────────────────────

    def _get_widths(self, num_cols: int) -> list[int]:
        """计算列宽。

        若用户已提供 widths，直接返回。
        否则取 headers 和所有 rows 中每列最宽字符串的视觉宽度 + 2（左右各 1 空格 padding）。

        Args:
            num_cols: 列数。

        Returns:
            每列的视觉宽度列表。
        """
        if self._widths is not None and len(self._widths) >= num_cols:
            return list(self._widths[:num_cols])

        widths = [0] * num_cols

        # 检查 headers
        for i, h in enumerate(self._headers):
            if i < num_cols:
                widths[i] = max(widths[i], _visual_width(h))

        # 检查所有 rows
        for row in self._rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    widths[i] = max(widths[i], _visual_width(str(cell)))

        # padding = 左右各 1 空格
        widths = [w + 2 for w in widths]

        return widths

    # ── 对齐方式 ────────────────────────────────────────

    def _get_aligns(self, num_cols: int) -> list[str]:
        """获取每列对齐方式，不足部分默认 "left"。

        Args:
            num_cols: 列数。

        Returns:
            对齐方式列表，长度等于 num_cols。
        """
        result = list(self._align) if self._align else []
        while len(result) < num_cols:
            result.append("left")
        return result[:num_cols]

    # ── 单元格填充 ──────────────────────────────────────

    def _pad_cell(self, text: str, width: int, align: str) -> str:
        """将单元格内容按对齐方式填充到目标视觉宽度。

        使用空格填充，考虑 ANSI 序列剥离后的视觉宽度。

        Args:
            text: 单元格原始文本（可能含 ANSI 序列）。
            width: 目标视觉宽度（列宽）。
            align: 对齐方式 ("left"/"center"/"right")。

        Returns:
            填充后的字符串。
        """
        visual_w = _visual_width(_strip_ansi(text))
        padding = width - visual_w
        if padding <= 0:
            return text

        if align == "right":
            return " " * padding + text
        elif align == "center":
            left_pad = padding // 2
            right_pad = padding - left_pad
            return " " * left_pad + text + " " * right_pad
        else:  # left（默认）
            return text + " " * padding

    # ── 行构建 ──────────────────────────────────────────

    def _render_separator(
        self, widths: list[int], left: str, mid: str, right: str, h_char: str
    ) -> str:
        """渲染水平分隔线。

        Args:
            widths: 各列宽度。
            left: 左端字符（如 ┌、├、└）。
            mid: 列间字符（如 ┬、┼、┴）。
            right: 右端字符（如 ┐、┤、┘）。
            h_char: 水平线字符（─）。

        Returns:
            完整的分隔线字符串。
        """
        parts: list[str] = [left]
        for i, w in enumerate(widths):
            if i > 0:
                parts.append(mid)
            parts.append(h_char * w)
        parts.append(right)
        return "".join(parts)

    def _render_row(
        self,
        cells: list[str],
        widths: list[int],
        aligns: list[str],
        bold: bool = False,
    ) -> str:
        """渲染数据行。

        Args:
            cells: 该行各列单元格文本。
            widths: 各列宽度。
            aligns: 各列对齐方式。
            bold: 是否加粗（表头使用）。

        Returns:
            带竖线边框的完整行字符串。
        """
        parts: list[str] = ["│"]
        for i, (cell, w, a) in enumerate(zip(cells, widths, aligns)):
            if i > 0:
                parts.append("│")
            text = str(cell) if cell is not None else ""
            padded = self._pad_cell(text, w, a)
            if bold:
                parts.append(f"{ANSI_BOLD}{padded}{ANSI_RESET}")
            else:
                parts.append(padded)
        parts.append("│")
        return "".join(parts)

    # ── render ──────────────────────────────────────────

    def render(self) -> str:
        """渲染完整表格。

        流程:
            1. 确定列数
            2. 计算列宽
            3. 获取对齐方式
            4. 逐行构建：上边框 → 表头 → 分隔线 → 数据行 → 下边框

        边界条件:
            - headers=[] 且 rows=[] → 返回空字符串
            - headers=[] 但 rows 有数据 → 仅渲染数据行（无表头/分隔线）
            - rows=[] 但 headers 有数据 → 仅渲染表头 + 下边框（无分隔线）

        Returns:
            含 Unicode box-drawing 字符的完整表格字符串。
        """
        # 确定列数：取 headers 和第一行 row 的最大列数
        num_cols = len(self._headers) if self._headers else 0
        if self._rows:
            num_cols = max(num_cols, max(len(row) for row in self._rows))

        if num_cols == 0:
            return ""

        widths = self._get_widths(num_cols)
        aligns = self._get_aligns(num_cols)

        has_headers = bool(self._headers)
        has_rows = bool(self._rows)

        lines: list[str] = []

        # 上边框
        lines.append(self._render_separator(widths, "┌", "┬", "┐", "─"))

        if has_headers:
            # 表头行（加粗）
            header_cells = list(self._headers)
            while len(header_cells) < num_cols:
                header_cells.append("")
            lines.append(
                self._render_row(header_cells[:num_cols], widths, aligns, bold=True)
            )

            if has_rows:
                # 表头与数据之间的分隔线
                lines.append(self._render_separator(widths, "├", "┼", "┤", "─"))
        elif has_rows:
            # 无表头，直接渲染数据行（上边框已在上面）
            pass

        # 数据行
        for row in self._rows:
            cells = list(row) if row else []
            while len(cells) < num_cols:
                cells.append("")
            lines.append(self._render_row(cells[:num_cols], widths, aligns))

        # 下边框
        lines.append(self._render_separator(widths, "└", "┴", "┘", "─"))

        return "\n".join(lines)

    # ── render_vnode ────────────────────────────────────

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染入口。"""
        return VNode(
            type="table",
            key=self.key,
            props={
                "headers": self._headers,
                "rows": self._rows,
                "widths": self._widths,
                "align": self._align,
            },
        )
