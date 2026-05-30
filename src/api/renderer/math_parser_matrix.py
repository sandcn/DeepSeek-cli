"""math_parser_matrix — MathParser 矩阵与多行环境 Mixin。

包含 \begin{matrix}/\begin{align}/\begin{cases} 等环境的相关方法。
"""

from __future__ import annotations

from typing import List, Tuple

from rich.text import Text

from .math_parser_helpers import (
    _STYLE_SUBSCRIPT,
    re_split_rows, _extract_braced_group, _skip_spaces,
)


class MathParserMatrixMixin:
    """MathParser 矩阵与多行环境 Mixin。

    提供以下方法，供 MathParser 通过多继承使用：
      _parse_matrix_env()
      _render_matrix()
      _render_align_env()
      _render_gather_env()
      _render_cases_env()
      _render_rcases_env()
    """

    # ── 矩阵环境 ────────────────────────────────────────

    def _parse_matrix_env(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\begin{env}...\\end{env} 或 TeX 简写 \\begin{env}...\\endenv 环境。

        支持：matrix, pmatrix, bmatrix, Bmatrix, vmatrix, Vmatrix,
              align, align*, aligned, gather, gather*, gathered,
              cases, dcases, rcases,
              array, smallmatrix, split, alignat, flalign, multline

        兼容 TeX 简写：\\end{aligned} 和 \\endaligned 均可识别。
        """
        try:
            env_raw, i = _extract_braced_group(s, i)
            env_name = env_raw.strip().lower()
        except Exception:
            return Text("\\begin"), i

        # ── array 环境：提取列格式参数 {lcr} ─────────
        col_format = ""
        if env_name == "array":
            i = _skip_spaces(s, i, n)
            if i < n and s[i] == '{':
                col_format, i = _extract_braced_group(s, i)

        # 查找 \\end{env_name}（标准形式）或 \\endenv_name（TeX 简写）
        end_marker_std = f"\\end{{{env_raw}}}"
        end_marker_short = f"\\end{env_raw}"  # TeX 简写 \endenvname
        end_pos = s.find(end_marker_std, i)
        end_marker_used = end_marker_std

        if end_pos == -1:
            end_pos = s.find(end_marker_short, i)
            end_marker_used = end_marker_short

        # 如果两种形式都找不到，可能是 \end 后无括号环境名的情况
        if end_pos == -1:
            # 尝试匹配 \end 后跟字母作为环境名
            end_idx = s.find("\\end", i)
            if end_idx != -1:
                # 检查 \\end 后是否紧跟字母（无 {）
                j = end_idx + 4  # 跳过 \end
                if j < len(s) and s[j].isalpha():
                    start_en = j
                    while j < len(s) and s[j].isalpha():
                        j += 1
                    short_env = s[start_en:j]
                    # 比较 short_env 是否与环境名匹配（不区分大小写）
                    if short_env.lower() == env_name:
                        end_pos = end_idx
                        end_marker_used = s[end_idx:j]

        if end_pos == -1:
            return Text(f"\\begin{{{env_raw}}}"), i

        content_raw = s[i:end_pos].strip()
        i = end_pos + len(end_marker_used)

        # ── 处理矩阵后的外部标签，如 \begin{bmatrix}...\end{bmatrix}_{(n+2)\times(n+2)} ──
        trailing_label = None
        j = i
        # 跳过空白
        while j < n and s[j] == ' ':
            j += 1
        if j < n and s[j] == '_':
            script_end = j + 1
            if script_end < n and s[script_end] == '{':
                label_raw, label_i = _extract_braced_group(s, script_end)
                if label_raw:
                    trailing_label = label_raw
                    i = label_i  # 更新位置到 } 之后

        try:
            result = self._render_matrix(content_raw, env_name, col_format)
            if trailing_label:
                # 外部标签以正常大小渲染（非下标样式），与矩阵右括号保持间距
                label_text = self.parse(trailing_label)
                result.append(" ")
                result.append_text(label_text)
            return result, i
        except Exception:
            return Text(f"[{env_name}:{content_raw}]"), i

    def _render_matrix(self, content: str, env_name: str,
                       col_format: str = "") -> Text:
        """将矩阵/多行环境内容渲染为网格 Text。"""
        # ── 处理带星号的变体 ────────────────────────────
        base_name = env_name.rstrip('*')

        # ── 多行对齐环境 ──
        if base_name in ("align", "aligned", "split", "alignat", "flalign"):
            return self._render_align_env(content)
        # ── 多行居中环境 ──
        if base_name in ("gather", "gathered", "multline"):
            return self._render_gather_env(content)
        # ── 分段函数环境 ──
        if base_name in ("cases", "dcases"):
            return self._render_cases_env(content)
        if base_name in ("rcases", "drcases"):
            return self._render_rcases_env(content)

        # ── 矩阵系列 ──
        is_small = (env_name == "smallmatrix")

        # ── 解析列对齐格式（用于 array 环境） ────────
        col_aligns = []
        if col_format:
            for ch in col_format:
                if ch in ('l', 'c', 'r'):
                    col_aligns.append(ch)

        left_bracket = ""
        right_bracket = ""
        if env_name == "pmatrix":
            left_bracket, right_bracket = "(", ")"
        elif env_name == "bmatrix":
            left_bracket, right_bracket = "[", "]"
        elif env_name == "Bmatrix":
            left_bracket, right_bracket = "{", "}"
        elif env_name == "vmatrix":
            left_bracket, right_bracket = "|", "|"
        elif env_name == "Vmatrix":
            left_bracket, right_bracket = "‖", "‖"
        elif env_name == "smallmatrix":
            pass  # 无括号

        # 分割行（忽略行尾可选参数 \\[2pt]）
        rows_raw = re_split_rows(content)
        parsed_rows: List[List[Text]] = []
        col_count = 0

        for row_str in rows_raw:
            row_str = row_str.strip()
            if not row_str:
                continue
            cells = [c.strip() for c in row_str.split("&")]
            parsed_cells: List[Text] = []
            for cell in cells:
                cell_text = self.parse(cell)
                parsed_cells.append(cell_text)
            if parsed_cells:
                col_count = max(col_count, len(parsed_cells))
                parsed_rows.append(parsed_cells)

        if not parsed_rows:
            return Text(f"{left_bracket}{right_bracket}")

        # 计算每列最大宽度
        col_widths = [0] * col_count
        for row in parsed_rows:
            for j, cell in enumerate(row):
                w = cell.cell_len
                if w > col_widths[j]:
                    col_widths[j] = w

        # 渲染网格（列对齐）
        result = Text()
        if left_bracket:
            result.append(left_bracket)
            if not is_small:
                result.append(" ")  # 左括号后统一空一格，与后续行对齐

        # 使用列对齐格式（矩阵环境默认居中对齐，视觉更对称）
        if not col_aligns:
            col_aligns = ['center'] * col_count
        # 补齐到 col_count
        while len(col_aligns) < col_count:
            col_aligns.append('center')

        sep = ("   " if col_count > 4 else "  ") if not is_small else " "
        for ri, row in enumerate(parsed_rows):
            if ri > 0:
                if getattr(self, '_is_block', False):
                    result.append("\n  ")
                else:
                    result.append(sep if not is_small else " ")
            for j, cell in enumerate(row):
                if j > 0:
                    result.append(sep)
                align = col_aligns[j] if j < len(col_aligns) else 'left'
                cell_w = cell.cell_len
                if align == 'right':
                    padding = col_widths[j] - cell_w
                    if padding > 0:
                        result.append(" " * padding)
                    result.append_text(cell)
                elif align == 'center':
                    padding = col_widths[j] - cell_w
                    left_pad = padding // 2
                    right_pad = padding - left_pad
                    if left_pad > 0:
                        result.append(" " * left_pad)
                    result.append_text(cell)
                    if right_pad > 0:
                        result.append(" " * right_pad)
                else:
                    # left
                    result.append_text(cell)
                    padding = col_widths[j] - cell_w
                    if padding > 0:
                        result.append(" " * padding)

        if right_bracket:
            if not is_small:
                result.append(" ")  # 右括号前统一空一格
            result.append(right_bracket)

        return result

    # ── align 多行对齐环境 ─────────────────────────────

    def _render_align_env(self, content: str) -> Text:
        """渲染 align / aligned 多行对齐环境。

        按 & 对齐各列，奇数列(0,2,4...)右对齐、偶数列(1,3,5...)左对齐。
        行尾可选参数 \\[2pt] 被忽略。
        """
        rows_raw = re_split_rows(content)
        parsed_rows: List[List[Text]] = []
        col_count = 0

        for row_str in rows_raw:
            row_str = row_str.strip()
            if not row_str:
                continue
            cells = [c.strip() for c in row_str.split("&")]
            parsed_cells = [self.parse(cell) for cell in cells]
            col_count = max(col_count, len(parsed_cells))
            parsed_rows.append(parsed_cells)

        if not parsed_rows:
            return Text()

        col_widths = [0] * col_count
        for row in parsed_rows:
            for j, cell in enumerate(row):
                w = cell.cell_len
                if w > col_widths[j]:
                    col_widths[j] = w

        result = Text()
        for ri, row in enumerate(parsed_rows):
            if ri > 0:
                if getattr(self, '_is_block', False):
                    result.append("\n  ")
                else:
                    result.append("  ")
            for j, cell in enumerate(row):
                if j > 0:
                    result.append("  " if col_count <= 4 else "   ")
                if j % 2 == 0:
                    # 奇数列右对齐
                    padding = col_widths[j] - cell.cell_len
                    if padding > 0:
                        result.append(" " * padding)
                    result.append_text(cell)
                else:
                    # 偶数列左对齐
                    result.append_text(cell)
                    padding = col_widths[j] - cell.cell_len
                    if padding > 0:
                        result.append(" " * padding)
        return result

    # ── gather 多行居中环境 ────────────────────────────

    def _render_gather_env(self, content: str) -> Text:
        """渲染 gather / gathered 多行居中环境。"""
        rows_raw = re_split_rows(content)
        parsed_rows: List[Text] = []

        for row_str in rows_raw:
            row_str = row_str.strip()
            if not row_str:
                continue
            line_text = self.parse(row_str)
            parsed_rows.append(line_text)

        if not parsed_rows:
            return Text()

        max_width = max(t.cell_len for t in parsed_rows)

        result = Text()
        for ri, line in enumerate(parsed_rows):
            if ri > 0:
                if getattr(self, '_is_block', False):
                    result.append("\n  ")
                else:
                    result.append("  ")
            padding = (max_width - line.cell_len) // 2
            if padding > 0:
                result.append(" " * padding)
            result.append_text(line)
        return result

    # ── cases 分段函数环境 ─────────────────────────────

    def _render_cases_env(self, content: str) -> Text:
        """渲染 cases / dcases 分段函数环境。

        两列布局，左列右对齐、右列左对齐，前缀 {。
        """
        rows_raw = re_split_rows(content)
        parsed_rows: List[List[Text]] = []

        for row_str in rows_raw:
            row_str = row_str.strip()
            if not row_str:
                continue
            cells = [c.strip() for c in row_str.split("&")]
            parsed_cells = [self.parse(cells[0])] if cells else [Text()]
            if len(cells) > 1:
                parsed_cells.append(self.parse(cells[1]))
            parsed_rows.append(parsed_cells)

        if not parsed_rows:
            return Text("{")

        col_widths = [0, 0]
        for row in parsed_rows:
            if len(row) > 0:
                col_widths[0] = max(col_widths[0], row[0].cell_len)
            if len(row) > 1:
                col_widths[1] = max(col_widths[1], row[1].cell_len)

        result = Text("{")
        for ri, row in enumerate(parsed_rows):
            if ri > 0:
                if getattr(self, '_is_block', False):
                    result.append("\n  ")  # 2-space block prefix + 1 space = 3 spaces total
                else:
                    result.append("\n ")
            if len(row) > 0:
                padding = col_widths[0] - row[0].cell_len
                if padding > 0:
                    result.append(" " * padding)
                result.append_text(row[0])
            if len(row) > 1:
                result.append(" ")
                result.append_text(row[1])
                padding = col_widths[1] - row[1].cell_len
                if padding > 0:
                    result.append(" " * padding)
        return result

    # ── rcases 右花括号环境 ───────────────────────────

    def _render_rcases_env(self, content: str) -> Text:
        """渲染 rcases 分段函数环境（右花括号）。"""
        rows_raw = re_split_rows(content)
        parsed_rows: List[List[Text]] = []

        for row_str in rows_raw:
            row_str = row_str.strip()
            if not row_str:
                continue
            cells = [c.strip() for c in row_str.split("&")]
            parsed_cells = [self.parse(cells[0])] if cells else [Text()]
            if len(cells) > 1:
                parsed_cells.append(self.parse(cells[1]))
            parsed_rows.append(parsed_cells)

        if not parsed_rows:
            return Text("}")

        col_widths = [0, 0]
        for row in parsed_rows:
            if len(row) > 0:
                col_widths[0] = max(col_widths[0], row[0].cell_len)
            if len(row) > 1:
                col_widths[1] = max(col_widths[1], row[1].cell_len)

        result = Text()
        for ri, row in enumerate(parsed_rows):
            if ri > 0:
                if getattr(self, '_is_block', False):
                    result.append("\n  ")  # 2-space block prefix
                else:
                    result.append("\n")
            if len(row) > 0:
                padding = col_widths[0] - row[0].cell_len
                if padding > 0:
                    result.append(" " * padding)
                result.append_text(row[0])
            if len(row) > 1:
                result.append("  ")
                result.append_text(row[1])
        result.append("}")
        return result
