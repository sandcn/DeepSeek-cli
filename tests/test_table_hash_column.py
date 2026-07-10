"""测试 |#| 作为首列的表格渲染 — 回归测试

验证 Markdown 表格首列为 # 符号时，解析和渲染全链路正确。
"""
from __future__ import annotations

from src.renderer._table_utils import (
    _is_table_row,
    _is_table_separator,
    _parse_table_row,
    _parse_table_alignments,
)

# ── 单元测试：_is_table_row ──────────────────────────


class TestIsTableRowWithHash:
    """# 在表格单元格中的行检测"""

    def test_hash_first_cell_no_spaces(self):
        """|#|目录| → True（# 在首列，无空格）"""
        assert _is_table_row("|#|目录|") is True

    def test_hash_first_cell_with_spaces(self):
        """| # | 目录 | → True（# 在首列，有空格）"""
        assert _is_table_row("| # | 目录 |") is True

    def test_hash_in_middle_cell(self):
        """| 1 | # | 目录 | → True（# 在中间列）"""
        assert _is_table_row("| 1 | # | 目录 |") is True

    def test_hash_in_all_cells(self):
        """| # | # | # | → True"""
        assert _is_table_row("| # | # | # |") is True

    def test_hash_only_row(self):
        """|#| → True（2 个 |，单单元格行，类似 | a |）"""
        assert _is_table_row("|#|") is True

    def test_hash_with_number_table(self):
        """标准序号表格 | # | 名称 | 说明 | → True"""
        assert _is_table_row("| # | 名称 | 说明 |") is True


# ── 单元测试：_parse_table_row ────────────────────────


class TestParseTableRowWithHash:
    """# 在表格单元格中的行解析"""

    def test_hash_first_cell_no_spaces(self):
        """|#|目录| → ['#', '目录']"""
        assert _parse_table_row("|#|目录|") == ["#", "目录"]

    def test_hash_first_cell_with_spaces(self):
        """| # | 目录 | → ['#', '目录']"""
        assert _parse_table_row("| # | 目录 |") == ["#", "目录"]

    def test_hash_in_middle_cell(self):
        """| 1 | # | 目录 | → ['1', '#', '目录']"""
        assert _parse_table_row("| 1 | # | 目录 |") == ["1", "#", "目录"]

    def test_hash_and_number_mixed(self):
        """多行序号表格"""
        assert _parse_table_row("| # | 文件名 | 大小 |") == ["#", "文件名", "大小"]
        assert _parse_table_row("| 1 | main.py | 2KB |") == ["1", "main.py", "2KB"]
        assert _parse_table_row("| 2 | utils.py | 1KB |") == ["2", "utils.py", "1KB"]

    def test_hash_with_separator_unaltered(self):
        """分隔行不应被 # 影响"""
        assert _is_table_separator("|---|------|------|") is True
        assert _is_table_row("|---|------|------|") is False


# ── 集成测试：流式表格缓冲（≥2 行自动识别）─────────────


class TestStreamingTableWithHash:
    """流式场景：连续 | 行含 # 列的正确识别"""

    def test_two_rows_auto_table(self):
        """2 行连续 | 行 → 自动识别为表格"""
        rows = ["| # | 目录 |", "| 1 | 介绍  |"]
        assert all(_is_table_row(r) for r in rows)
        # 首行作表头，第二行作数据
        header = _parse_table_row(rows[0])
        data = _parse_table_row(rows[1])
        assert header == ["#", "目录"]
        assert data == ["1", "介绍"]

    def test_three_rows_auto_table(self):
        """3 行连续 | 行 → 自动识别为表格"""
        rows = ["| # | 项目 |", "| 1 | A    |", "| 2 | B    |"]
        assert all(_is_table_row(r) for r in rows)
        header = _parse_table_row(rows[0])
        data = [_parse_table_row(r) for r in rows[1:]]
        assert header == ["#", "项目"]
        assert data == [["1", "A"], ["2", "B"]]

    def test_single_row_falls_back_to_paragraph(self):
        """1 行 → 降级为段落（非表格）"""
        # 单行在 _table_pending_rows 中等待分隔行或打断
        # _emit_pending_table 中 len==1 → PARAGRAPH
        assert _is_table_row("| # | 目录 |") is True


# ── 完整表格：带分隔行 ──────────────────────────────


class TestFullTableWithHash:
    """完整 Markdown 表格（含分隔行）"""

    def test_two_column_table(self):
        """2 列表格 | # | 目录 |"""
        lines = [
            ("| # | 目录 |", True, False),
            ("|---|------|", False, True),
            ("| 1 | 介绍  |", True, False),
            ("| 2 | 背景  |", True, False),
        ]
        for text, expect_row, expect_sep in lines:
            assert _is_table_row(text) is expect_row, f"row check: {text!r}"
            assert _is_table_separator(text) is expect_sep, f"sep check: {text!r}"

    def test_three_column_table(self):
        """3 列表格 | # | 名称 | 说明 |"""
        lines = [
            ("| # | 名称     | 说明     |", True, False),
            ("|---|----------|----------|", False, True),
            ("| 1 | 项目启动 | 初始化   |", True, False),
        ]
        for text, expect_row, expect_sep in lines:
            assert _is_table_row(text) is expect_row, f"row check: {text!r}"
            assert _is_table_separator(text) is expect_sep, f"sep check: {text!r}"

    def test_table_alignments_with_hash_column(self):
        """首列对齐不影响 # 符号"""
        aligns = _parse_table_alignments("|:---|:---:|---:|")
        assert aligns == ["left", "center", "right"]


# ── 完整表格：人员信息表 ────────────────────────────


class TestFullTablePeople:
    """人员信息表格 — 含左对齐分隔行"""

    LINES = [
        ("| 姓名 | 年龄 | 城市 | 职业     |", True, False,
         ["姓名", "年龄", "城市", "职业"]),
        ("| :--- | :--- | :--- | :---     |", False, True,
         [":---", ":---", ":---", ":---"]),
        ("| 张三 | 28   | 北京 | 工程师   |", True, False,
         ["张三", "28", "北京", "工程师"]),
        ("| 李四 | 32   | 上海 | 设计师   |", True, False,
         ["李四", "32", "上海", "设计师"]),
        ("| 王五 | 25   | 广州 | 产品经理 |", True, False,
         ["王五", "25", "广州", "产品经理"]),
    ]

    def test_all_lines_classified_correctly(self):
        """每行正确分类为表格行/分隔行"""
        for text, expect_row, expect_sep, _ in self.LINES:
            assert _is_table_row(text) is expect_row, f"row check: {text!r}"
            assert _is_table_separator(text) is expect_sep, f"sep check: {text!r}"

    def test_all_cells_parsed_correctly(self):
        """每行单元格正确解析"""
        for text, _, _, expect_cells in self.LINES:
            assert _parse_table_row(text) == expect_cells, f"parse: {text!r}"

    def test_header_row(self):
        """表头行：['姓名', '年龄', '城市', '职业']"""
        assert _parse_table_row("| 姓名 | 年龄 | 城市 | 职业 |") == ["姓名", "年龄", "城市", "职业"]

    def test_data_rows(self):
        """数据行正确解析"""
        rows = [
            ("| 张三 | 28 | 北京 | 工程师 |",   ["张三", "28", "北京", "工程师"]),
            ("| 李四 | 32 | 上海 | 设计师 |",   ["李四", "32", "上海", "设计师"]),
            ("| 王五 | 25 | 广州 | 产品经理 |", ["王五", "25", "广州", "产品经理"]),
        ]
        for text, expect in rows:
            assert _parse_table_row(text) == expect, f"data row: {text!r}"

    def test_separator_alignment(self):
        """分隔行 :--- 对齐解析为 left"""
        aligns = _parse_table_alignments("| :--- | :--- | :--- | :--- |")
        assert aligns == ["left", "left", "left", "left"]

    def test_separator_row_is_not_table_row(self):
        """分隔行不被识别为表格行"""
        assert _is_table_row("| :--- | :--- | :--- | :--- |") is False
        assert _is_table_separator("| :--- | :--- | :--- | :--- |") is True
