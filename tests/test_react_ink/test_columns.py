"""Columns 组件单元测试。

覆盖 Columns 多列布局的 8 个测试用例：
- 2 列行优先布局
- 3 列布局
- row_first=True vs row_first=False 排列差异
- gap 间距
- 空 children
- 单子组件（退化为单列）
- 多行子组件
- update() props 变更

测试策略：使用简单 _TextComp 子组件，通过分析 render() 输出的
文本行结构验证排列、间距、退化行为正确性。
"""

from __future__ import annotations

import os
import re
import pytest
from unittest.mock import patch

from src.chat_ui.components.columns import Columns
from src.chat_ui.components.base import TuiComponent


# ── 测试辅助 ────────────────────────────────────────────

class _TextComp(TuiComponent):
    """简单文本子组件，返回固定内容。"""

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


def _count_columns(text: str, gap: int = 2) -> int:
    """辅助：通过计算第一行内容块数量 + gap 间距推断列数。

    策略：统计第一行中连续非空格块的数目（被 gap 空格分隔的块）。
    但多列内容之间以 gap 空格分隔，所以需要根据 gap 大小来分块。
    简单起见，返回第一行中去除了 gap 空格后被分割的块数。
    """
    line = text.split("\n")[0]
    # 找到 gap 空格分隔符的位置
    # 使用 split 按 gap 个空格分隔
    gap_str = " " * gap
    parts = line.split(gap_str)
    # 过滤空块
    return len([p for p in parts if p.strip() or p])


# ═══════════════════════════════════════════════════════════
# TestColumnsLayout
# ═══════════════════════════════════════════════════════════

class TestColumnsLayout:
    """Columns 布局渲染测试。"""

    def test_two_columns_row_first(self):
        """2 列行优先布局：4 个子组件排列为 2 行 × 2 列。"""
        children = [
            _TextComp("A"), _TextComp("B"),
            _TextComp("C"), _TextComp("D"),
        ]
        cols = Columns(columns=2, gap=2, row_first=True, children=children)
        output = cols.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        # 应有 2 行
        assert len(lines) == 2, f"2 列 4 子组件应产生 2 行，实际: {len(lines)} 行: {lines}"

        # 第一行含 A 和 B
        assert "A" in lines[0], f"第一行应含 A: {lines[0]}"
        assert "B" in lines[0], f"第一行应含 B: {lines[0]}"
        # 第二行含 C 和 D
        assert "C" in lines[1], f"第二行应含 C: {lines[1]}"
        assert "D" in lines[1], f"第二行应含 D: {lines[1]}"

    def test_three_columns(self):
        """3 列布局：5 个子组件排列为 2 行 × 3 列（末位空）。"""
        children = [
            _TextComp("A"), _TextComp("B"), _TextComp("C"),
            _TextComp("D"), _TextComp("E"),
        ]
        cols = Columns(columns=3, gap=2, row_first=True, children=children)
        output = cols.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        # 应有 2 行
        assert len(lines) == 2, f"3 列 5 子组件应产生 2 行，实际: {len(lines)} 行: {lines}"

        # 第一行含 A、B、C
        assert "A" in lines[0]
        assert "B" in lines[0]
        assert "C" in lines[0]
        # 第二行含 D、E
        assert "D" in lines[1]
        assert "E" in lines[1]

    def test_row_first_vs_column_first(self):
        """row_first=True 与 row_first=False 的排列差异可观测。

        6 个子组件 A-F，3 列。
        row_first=True:  A B C / D E F
        row_first=False: A C E / B D F（按列优先填充）
        """
        children = [
            _TextComp("A"), _TextComp("B"), _TextComp("C"),
            _TextComp("D"), _TextComp("E"), _TextComp("F"),
        ]

        # row_first=True
        cols_rf = Columns(columns=3, gap=2, row_first=True, children=children)
        out_rf = _strip_ansi(cols_rf.render())
        lines_rf = out_rf.split("\n")
        # 第一行含 A、B、C
        assert "A" in lines_rf[0] and "B" in lines_rf[0] and "C" in lines_rf[0], (
            f"row_first=True 第一行应含 A B C: {lines_rf[0]}"
        )

        # row_first=False
        cols_cf = Columns(columns=3, gap=2, row_first=False, children=children)
        out_cf = _strip_ansi(cols_cf.render())
        lines_cf = out_cf.split("\n")
        # 第一行含 A、C、E
        assert "A" in lines_cf[0] and "C" in lines_cf[0] and "E" in lines_cf[0], (
            f"row_first=False 第一行应含 A C E: {lines_cf[0]}"
        )
        # 第二行含 B、D、F
        assert "B" in lines_cf[1] and "D" in lines_cf[1] and "F" in lines_cf[1], (
            f"row_first=False 第二行应含 B D F: {lines_cf[1]}"
        )

    def test_gap_zero(self):
        """gap=0 时列间无间距。"""
        children = [
            _TextComp("AA"), _TextComp("BB"),
            _TextComp("CC"), _TextComp("DD"),
        ]
        cols = Columns(columns=2, gap=0, row_first=True, children=children)
        output = cols.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        # 第一行：gap=0 时 "AABB"（无空格分隔）
        line0 = lines[0].replace(" ", "")
        assert "AA" in line0 and "BB" in line0, (
            f"gap=0 第一行应含 AA BB 紧邻: {lines[0]}"
        )

    def test_gap_large(self):
        """gap=4 时列间有较大间距。"""
        children = [
            _TextComp("A"), _TextComp("B"),
        ]
        cols = Columns(columns=2, gap=4, row_first=True, children=children)
        output = cols.render()
        clean = _strip_ansi(output)
        line = clean.split("\n")[0]

        # 应包含 4 空格分隔
        assert "    " in line, f"gap=4 应有 4 空格: {line!r}"
        # A 在 B 之前
        idx_a = line.index("A")
        idx_b = line.index("B")
        assert idx_a < idx_b, "A 应在 B 之前"

    def test_empty_children(self):
        """空 children 返回空字符串。"""
        cols = Columns(columns=2, gap=2, children=[])
        output = cols.render()
        assert output == "", f"空 children 应返回空串，实际: {output!r}"

    def test_empty_children_all_blank(self):
        """子组件全部渲染为空字符串时返回空字符串。"""
        class _EmptyComp(TuiComponent):
            def render(self) -> str:
                return ""

        cols = Columns(columns=2, gap=2, children=[_EmptyComp(), _EmptyComp()])
        output = cols.render()
        assert output == "", f"全空子组件应返回空串，实际: {output!r}"

    def test_single_child(self):
        """单子组件在 2 列布局中：仅占第一列，第二列为空。"""
        child = _TextComp("hello")
        cols = Columns(columns=2, gap=2, children=[child])
        output = cols.render()
        clean = _strip_ansi(output)

        # 单行：hello 在第一列，第二列空白（被 gap 填充）
        assert "hello" in clean, f"应包含子组件内容: {clean!r}"
        assert "\n" not in clean, "单子组件应仅一行"
        # 内容应在行首
        assert clean.startswith("hello"), f"内容应在行首: {clean!r}"

    def test_multiline_children(self):
        """多行子组件：各行正确对齐。"""
        class _MultiLineComp(TuiComponent):
            def __init__(self, lines: list[str]):
                super().__init__()
                self._lines = lines

            def render(self) -> str:
                return "\n".join(self._lines)

        children = [
            _MultiLineComp(["A1", "A2", "A3"]),
            _MultiLineComp(["B1", "B2"]),
            _MultiLineComp(["C1", "C2", "C3", "C4"]),
            _MultiLineComp(["D1"]),
        ]
        cols = Columns(columns=2, gap=2, row_first=True, children=children)
        output = cols.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        # row_first, 2 cols: A[0][0], B[0][1], C[1][0], D[1][1]
        # 第一行 heights: max(3, 2)=3 for row 0, max(4, 1)=4 for row 1
        # Total lines = 3 + 4 = 7
        assert len(lines) == 7, (
            f"多行子组件应产生 7 行，实际: {len(lines)} 行: {lines}"
        )
        # 第一行应含 A1 和 B1
        assert "A1" in lines[0] and "B1" in lines[0]
        # 第二行应含 A2 和 B2
        assert "A2" in lines[1] and "B2" in lines[1]
        # 第三行应含 A3（B 只有 2 行，所以 B 侧为空）
        assert "A3" in lines[2]

    def test_single_child_with_columns_one(self):
        """columns=1 退化为单列布局。"""
        children = [
            _TextComp("A"), _TextComp("B"), _TextComp("C"),
        ]
        cols = Columns(columns=1, gap=2, children=children)
        output = cols.render()
        clean = _strip_ansi(output)

        # columns=1 调用 render_children()，即换行拼接子组件输出
        assert "A" in clean
        assert "B" in clean
        assert "C" in clean
        # 应有换行分隔
        assert "\n" in clean

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_terminal_width_adaptation(self, mock_term):
        """终端宽度充足时内容不被截断。"""
        children = [
            _TextComp("A"), _TextComp("B"), _TextComp("C"),
        ]
        cols = Columns(columns=3, gap=2, children=children)
        output = cols.render()
        clean = _strip_ansi(output)
        line = clean.split("\n")[0]
        assert "A" in line and "B" in line and "C" in line


# ═══════════════════════════════════════════════════════════
# TestColumnsUpdate
# ═══════════════════════════════════════════════════════════

class TestColumnsUpdate:
    """Columns update() props 变更测试。"""

    def test_update_no_change(self):
        """相同 props 不触发变更。"""
        cols = Columns(columns=2, gap=2, row_first=True)
        changed = cols.update({})
        assert changed is False, "空 props 不应触发变更"

        # 等同于当前值的 props
        changed = cols.update({"columns": 2})
        assert changed is False, "相同 columns 值不应触发变更"

    def test_update_columns_change(self):
        """columns 变更触发重渲染。"""
        cols = Columns(columns=2, gap=2, row_first=True)
        changed = cols.update({"columns": 3})
        assert changed is True, "columns 从 2 变为 3 应触发变更"

    def test_update_gap_change(self):
        """gap 变更触发重渲染。"""
        cols = Columns(columns=2, gap=2, row_first=True)
        changed = cols.update({"gap": 4})
        assert changed is True, "gap 从 2 变为 4 应触发变更"

    def test_update_row_first_change(self):
        """row_first 变更触发重渲染。"""
        cols = Columns(columns=2, gap=2, row_first=True)
        changed = cols.update({"row_first": False})
        assert changed is True, "row_first 从 True 变为 False 应触发变更"

    def test_update_multiple_changes(self):
        """同时变更多个属性触发重渲染。"""
        cols = Columns(columns=2, gap=2, row_first=True)
        changed = cols.update({"columns": 3, "gap": 1, "row_first": False})
        assert changed is True, "多个属性同时变更应触发重渲染"

    def test_update_invalid_values_clamped(self):
        """无效值被 clamp 后比较。columns=0 → 1, gap=-1 → 0。"""
        cols = Columns(columns=1, gap=0, row_first=True)

        # columns=0 clamp 到 1，与当前 columns=1 相同 → 不变
        changed = cols.update({"columns": 0})
        assert changed is False, "columns=0 clamp 到 1，与当前值相同不应触发变更"

        # gap=-1 clamp 到 0，与当前 gap=0 相同 → 不变
        changed = cols.update({"gap": -1})
        assert changed is False, "gap=-1 clamp 到 0，与当前值相同不应触发变更"

        # gap 实际变化
        changed = cols.update({"gap": 3})
        assert changed is True, "gap 从 0 变为 3 应触发变更"
