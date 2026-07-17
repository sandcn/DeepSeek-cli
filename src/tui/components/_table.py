"""表格组件 — Table。

提供带边框的表格渲染器，支持表头样式、行样式、列对齐、
自动/显式列宽、窄屏降级和建造者模式构建。

设计模式: 建造者 (Builder) — Table.Builder() 支持链式调用，
逐步设置 headers/rows/styles 后通过 build() 构建 Table 实例。

依赖关系：
  - 边框字符：复用 `_box.py` 的 BoxStyle 枚举 + _BOX_CHARS 映射
  - 样式系统：复用 `core/style.py` 的 Style 样式描述器
  - 终端检测：复用 `terminal/narrow.py` 的 is_narrow 检测
  - 视觉宽度：复用 `ui/ansi.py` 的 visual_width 计算（CJK 安全）
"""

from __future__ import annotations

from ..render_buffer import RenderBuffer
from ..terminal.narrow import is_narrow
from ..core.style import Style
from ..core.ansi_utils import visual_width
from ._base import TuiComponent
from ._box import BoxStyle, _BOX_CHARS

__all__ = [
    "Table",
]


# ═══════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════

# _RESET 已移除 — 使用 Style.apply() 统一管理 RESET


def _is_numeric(text: str) -> bool:
    """判断文本是否全部为数字（含小数点、负号）。

    用于列对齐决策：数字列右对齐，非数字列左对齐。
    """
    if not text:
        return False
    # 去除可能的 ANSI 转义序列
    cleaned = text
    if "\033" in cleaned:
        import re
        cleaned = re.sub(r"\033\[[0-9;]*m", "", cleaned)
    # 去除前后空白后判断是否为数字
    stripped = cleaned.strip()
    if not stripped:
        return False
    # 支持整数、小数、负数
    try:
        float(stripped)
        return True
    except ValueError:
        return False


def _col_align_class(col_idx: int, rows: list[list[str]]) -> str:
    """判断列的对其类型。

    Args:
        col_idx: 列索引。
        rows: 所有数据行。

    Returns:
        "left"（首列默认左对齐）或 "right"（全数字列右对齐）。
        其他列默认左对齐。
    """
    if col_idx == 0:
        return "left"
    # 检查该列所有行的值是否均为数字
    for row in rows:
        if col_idx < len(row) and row[col_idx]:
            if not _is_numeric(row[col_idx]):
                return "left"
    return "right"


def _ansi_wrap(color: int | None) -> tuple[str, str]:
    """生成 ANSI 颜色包裹前缀/后缀。

    Args:
        color: 256 色号，None 时返回空字符串对。

    Returns:
        (prefix, suffix) 元组。已迁移使用 Style，保留为兼容包装。
    """
    if color is not None:
        style = Style(fg=color)
        ansi = style.to_ansi()
        return (ansi, "\033[0m")
    return ("", "")


# ═══════════════════════════════════════════════════════════
# Table 组件
# ═══════════════════════════════════════════════════════════

class Table(TuiComponent):
    """表格渲染器 — 带边框、表头样式和行样式的表格。

    支持自动列宽计算、显式列宽指定、列对齐检测（数字右对齐/文字左对齐），
    以及窄屏降级（移除边框、减少列间距）。

    Attributes:
        headers: 表头列名列表。
        rows: 数据行列表（每行为字符串列表）。
        column_widths: 显式列宽列表。None 时根据内容自动计算。
        header_style: 表头样式（Style 实例）。None 时使用默认加粗样式。
        row_styles: 行样式列表（每行一个 Style 或 None），长度不足时末尾行无样式。
        border_style: 边框样式，默认 ASCII。
        separator: 列间分隔符，默认 " │ "（空格+竖线+空格）。
    """

    def __init__(
        self,
        headers: list[str],
        rows: list[list[str]],
        column_widths: list[int] | None = None,
        header_style: Style | None = None,
        row_styles: list[Style | None] | None = None,
        border_style: BoxStyle = BoxStyle.ASCII,
        separator: str = " \u2502 ",
        *,
        props: dict | None = None,
    ) -> None:
        super().__init__(props=props)
        self.headers = headers
        self.rows = rows
        self.column_widths = column_widths
        self._header_style = header_style
        self._row_styles = row_styles
        self.border_style = border_style
        self.separator = separator

    # ── 公共 API ────────────────────────────────────────────────────────

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染完整表格。

        窄屏降级：移除边框，减少列间距，仅返回表头 + 行数据。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时直接写入 buffer。

        Returns:
            str | None: 无 buffer 时返回渲染字符串；有 buffer 时返回 None。
        """
        if is_narrow():
            result = self._render_narrow()
        else:
            result = self._render_normal()
        if buffer is not None:
            if result:
                buffer.write(0, 0, result)
            return None
        return result

    # ── 列宽计算 ────────────────────────────────────────────────────────

    def _resolve_column_widths(self) -> list[int]:
        """计算每列实际宽度。

        优先使用显式指定的 column_widths，长度不足时自动补算。
        自动计算逻辑：取表头宽度与各行该列最大宽度的较大值 + 2（内边距）。

        Returns:
            各列宽度列表。
        """
        if self.column_widths is not None:
            # 补齐缺省列的宽度
            widths = list(self.column_widths)
            num_cols = max(len(self.headers), max((len(r) for r in self.rows), default=0) if self.rows else 0)
            while len(widths) < num_cols:
                widths.append(10)
            return widths

        # 自动计算
        num_cols = max(len(self.headers), max((len(r) for r in self.rows), default=0) if self.rows else 0)
        widths: list[int] = []
        for col_idx in range(num_cols):
            # 表头宽度
            hdr_vw = visual_width(self.headers[col_idx]) if col_idx < len(self.headers) else 0
            # 各行的最大宽度
            max_data_vw = hdr_vw
            for row in self.rows:
                if col_idx < len(row) and row[col_idx]:
                    vw = visual_width(row[col_idx])
                    if vw > max_data_vw:
                        max_data_vw = vw
            # 加 2 个字符的列内边距
            widths.append(max(max_data_vw + 2, 4))
        return widths

    # ── 窄屏降级 ────────────────────────────────────────────────────────

    def _render_narrow(self) -> str:
        """窄屏渲染：移除边框，减少列间距。

        格式：表头行（无边框）→ 分隔线 → 数据行。
        """
        widths = self._resolve_column_widths()

        # 窄屏下调小所有列宽
        narrow_widths = [max(3, w - 2) for w in widths]

        # 表头行
        header_parts: list[str] = []
        for i, h in enumerate(self.headers):
            w = narrow_widths[i] if i < len(narrow_widths) else 4
            h_text = h[:w].ljust(w)
            header_parts.append(h_text)
        header_line = " ".join(header_parts)

        # 分隔线
        sep_line = "-" * len(header_line)

        # 数据行
        data_lines: list[str] = []
        for row in self.rows:
            row_parts: list[str] = []
            for i, cell in enumerate(row):
                w = narrow_widths[i] if i < len(narrow_widths) else 4
                align = _col_align_class(i, self.rows)
                cell_text = cell[:w] if len(cell) <= w else cell[:w - 1] + "…"
                if align == "right":
                    cell_text = cell_text.rjust(w)
                else:
                    cell_text = cell_text.ljust(w)
                row_parts.append(cell_text)
            data_lines.append(" ".join(row_parts))

        return "\n".join([header_line, sep_line] + data_lines)

    # ── 标准渲染 ────────────────────────────────────────────────────────

    def _render_normal(self) -> str:
        """标准渲染：带边框的完整表格。"""
        widths = self._resolve_column_widths()
        chars = _BOX_CHARS[self.border_style]
        h = chars["h"]
        v = chars["v"]

        # ── 解析样式 ──
        header_style = self._header_style if self._header_style is not None else Style(bold=True)

        # ── 构建行分隔线（横线） ──
        def _h_sep(left: str, mid: str, right: str) -> str:
            """构建横分隔线，如 ───────┬───────┐ """
            parts: list[str] = [left]
            for i, w in enumerate(widths):
                parts.append(h * w)
                if i < len(widths) - 1:
                    parts.append(mid)
            parts.append(right)
            return "".join(parts)

        # ── 渲染表头行 ──
        header_cells: list[str] = []
        for i, hdr in enumerate(self.headers):
            w = widths[i] if i < len(widths) else 10
            # 居中显示表头
            hdr_vw = visual_width(hdr)
            if hdr_vw > w:
                hdr_text = hdr[:w - 1] + "…" if w > 1 else hdr[:w]
            else:
                left_pad = (w - hdr_vw) // 2
                right_pad = w - hdr_vw - left_pad
                hdr_text = " " * left_pad + hdr + " " * right_pad
            header_cells.append(hdr_text)

        header_line = (
            f"{v}"
            + f"{self.separator}".join(
                header_style.apply(c) for c in header_cells
            )
            + f"{v}"
        )

        # ── 顶部边框 ──
        top_border = _h_sep(chars["tl"], chars["h"], chars["tr"])

        # ── 表头/内容分隔线 ──
        header_sep = _h_sep(chars.get("ml", "├"), chars.get("h", "─"), chars.get("mr", "┤"))
        # 处理 BoxStyle.ASCII 的情况
        if self.border_style == BoxStyle.ASCII:
            header_sep = _h_sep("+", "-", "+")
        else:
            header_sep = _h_sep(chars.get("ml", "├"), chars.get("h", "─"), chars.get("mr", "┤"))

        # ── 渲染数据行 ──
        body_lines: list[str] = []
        for row_idx, row in enumerate(self.rows):
            # 取出该行样式
            row_style: Style | None = None
            if self._row_styles is not None and row_idx < len(self._row_styles):
                row_style = self._row_styles[row_idx]

            row_cells: list[str] = []
            for col_idx, cell in enumerate(row):
                w = widths[col_idx] if col_idx < len(widths) else 10
                align = _col_align_class(col_idx, self.rows)

                cell_vw = visual_width(cell)
                if cell_vw > w:
                    # 截断超长内容
                    display = cell[:w - 1] + "…" if w > 1 else cell[:w]
                else:
                    display = cell

                if align == "right":
                    cell_text = display.rjust(w)
                else:
                    cell_text = display.ljust(w)

                row_cells.append(cell_text)

            row_line = f"{v}" + f"{self.separator}".join(row_cells) + f"{v}"
            if row_style is not None:
                row_line = row_style.apply(row_line)
            body_lines.append(row_line)

        # ── 底部边框 ──
        bottom_border = _h_sep(chars["bl"], chars["h"], chars["br"])

        # ── 组装 ──
        if body_lines:
            return "\n".join([top_border, header_line, header_sep] + body_lines + [bottom_border])
        else:
            # 空表格：无数据行，直接顶部+表头+底部
            return "\n".join([top_border, header_line, bottom_border])

    # ═══════════════════════════════════════════════════════════════════
    # Builder 模式
    # ═══════════════════════════════════════════════════════════════════

    class Builder:
        """Table 建造者 — 链式调用构建表格实例。

        Example:
            >>> table = (
            ...     Table.Builder()
            ...     .headers(["Name", "Value"])
            ...     .add_row(["Alice", "42"])
            ...     .add_row(["Bob", "17"])
            ...     .with_header_style(Style(bold=True, fg=45))
            ...     .with_border_style(BoxStyle.ROUNDED)
            ...     .build()
            ... )
            >>> print(table.render())
        """

        def __init__(self) -> None:
            self._headers: list[str] = []
            self._rows: list[list[str]] = []
            self._column_widths: list[int] | None = None
            self._header_style: Style | None = None
            self._row_styles: list[Style | None] | None = None
            self._border_style: BoxStyle = BoxStyle.ASCII
            self._separator: str = " \u2502 "

        def headers(self, headers: list[str]) -> Table.Builder:
            """设置表头列名。

            Args:
                headers: 表头列名列表。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._headers = list(headers)
            return self

        def rows(self, rows: list[list[str]]) -> Table.Builder:
            """设置所有数据行。

            Args:
                rows: 数据行列表。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._rows = [list(r) for r in rows]
            return self

        def add_row(self, row: list[str]) -> Table.Builder:
            """添加单行数据。

            Args:
                row: 单行数据列表。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._rows.append(list(row))
            return self

        def column_widths(self, widths: list[int]) -> Table.Builder:
            """设置显式列宽。

            Args:
                widths: 各列宽度列表。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._column_widths = list(widths)
            return self

        def with_header_style(self, style: Style | None) -> Table.Builder:
            """设置表头样式。

            Args:
                style: Style 实例。None 表示无特殊样式。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._header_style = style
            return self

        def with_row_styles(self, styles: list[Style | None]) -> Table.Builder:
            """设置行样式列表（按行索引应用）。

            Args:
                styles: 行样式列表，长度不足时末尾行无样式。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._row_styles = list(styles)
            return self

        def with_border_style(self, style: BoxStyle) -> Table.Builder:
            """设置边框样式。

            Args:
                style: 边框样式枚举值。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._border_style = style
            return self

        def with_separator(self, sep: str) -> Table.Builder:
            """设置列间分隔符。

            Args:
                sep: 分隔符字符串，默认 " │ "。

            Returns:
                Builder 实例（支持链式调用）。
            """
            self._separator = sep
            return self

        def build(self) -> Table:
            """构建 Table 实例。

            Returns:
                Table 实例。
            """
            return Table(
                headers=self._headers,
                rows=self._rows,
                column_widths=self._column_widths,
                header_style=self._header_style,
                row_styles=self._row_styles,
                border_style=self._border_style,
                separator=self._separator,
            )
