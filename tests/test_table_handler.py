"""test_table_handler — 表格处理与渲染边界测试。

测试策略：
- _table_utils 中的解析函数通过直接断言验证返回值
- TableHandler._handle_table 通过 MockEngine 验证列对齐、行数、异常安全
- 不依赖实际终端输出（Rich Console），全部通过内存断言完成
"""

from __future__ import annotations

import pytest
from rich.text import Text

from src.renderer.types import Token, TokenType
from src.renderer.handlers.table import TableHandler
from src.renderer._table_utils import (
    _has_only_chars,
    _is_table_row,
    _is_table_separator,
    _parse_table_row,
    _parse_table_alignments,
    _SAFE_SENTINEL,
)


# ═══════════════════════════════════════════════════════════
# Mock Engine — 替代 RenderEngine，捕获输出但不渲染到终端
# ═══════════════════════════════════════════════════════════

class MockEngine:
    """模拟 RenderEngine，提供 TableHandler 所需的 render_inline、print 和 write_line。"""

    def __init__(self):
        self.last_renderable = None
        self.last_writeline_called = False

    @property
    def output_width(self) -> int:
        return 80

    def render_inline(self, text: str) -> Text:
        """简单返回 Rich Text（不处理内联 Markdown）。"""
        return Text(text)

    def print(self, renderable, *args, **kwargs):
        self.last_renderable = renderable

    def write_line(self, text: str = ""):
        self.last_writeline_called = True


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def handler() -> TableHandler:
    """返回独立的 TableHandler 实例。"""
    return TableHandler()


@pytest.fixture
def engine() -> MockEngine:
    """返回 MockEngine 实例。"""
    return MockEngine()


# ═══════════════════════════════════════════════════════════
# TestTableHandlerBehavior
# ═══════════════════════════════════════════════════════════

class TestTableHandlerBehavior:
    """测试 TableHandler.handle() 和 _handle_table() 的边界情况。"""

    def test_empty_rows_no_crash(self, handler: TableHandler, engine: MockEngine):
        """空 rows 不崩溃：when rows=[]，handler 直接返回，不调用 print。"""
        token = Token(TokenType.TABLE, "", {"rows": [], "alignments": ["left"]})
        handler.handle(token, engine)
        # 空 rows → 提前返回，不输出任何内容
        assert engine.last_renderable is None

    def test_missing_alignments(self, handler: TableHandler, engine: MockEngine):
        """alignments 数量少于列数时，剩余列使用 left 默认值。

        验证 (alignments + ['left'] * num_cols)[:num_cols] 的 padding 行为。
        """
        token = Token(TokenType.TABLE, "", {
            "rows": [["Name", "Age"], ["Alice", "30"]],
            "alignments": ["left"],          # 只有 1 个对齐，第 2 列自动补 left
        })
        handler.handle(token, engine)
        table = engine.last_renderable
        assert table is not None
        assert len(table.columns) == 2
        assert table.columns[0].justify == "left"
        assert table.columns[1].justify == "left"   # 默认补 left

    def test_uneven_row_lengths(self, handler: TableHandler, engine: MockEngine):
        """rows 中列数不一致时 padding/cut 行为。

        - "short" 行不足 3 列 → 补空字符串
        - "too/many/cols/extra" 行超 3 列 → 截断
        """
        token = Token(TokenType.TABLE, "", {
            "rows": [
                ["A", "B", "C"],
                ["short"],                       # 缺列 → 补 ''
                ["too", "many", "cols", "extra"],  # 多列 → 截断
            ],
            "alignments": ["left", "center", "right"],
        })
        handler.handle(token, engine)
        table = engine.last_renderable
        assert table is not None
        assert len(table.columns) == 3
        assert table.columns[0].justify == "left"
        assert table.columns[1].justify == "center"
        assert table.columns[2].justify == "right"
        # 表头 1 行 + 2 数据行
        assert table.row_count == 2

    def test_single_row(self, handler: TableHandler, engine: MockEngine):
        """只有表头行无数据行：rows=[headers]，data_rows 为空。"""
        token = Token(TokenType.TABLE, "", {
            "rows": [["Header1", "Header2"]],
            "alignments": ["left", "right"],
        })
        handler.handle(token, engine)
        table = engine.last_renderable
        assert table is not None
        assert len(table.columns) == 2
        # 无数据行，table.row_count == 0
        assert table.row_count == 0

    def test_cjk_width_calculation(self):
        """中文字符宽度计算：cjk_display_width 对 CJK 字符返回 2，ASCII 返回 1。"""
        from src.renderer._utils import cjk_display_width

        # 纯 CJK
        assert cjk_display_width("你好") == 4      # 2 + 2
        assert cjk_display_width("测试") == 4      # 2 + 2
        # 纯 ASCII
        assert cjk_display_width("a") == 1
        assert cjk_display_width("ab") == 2
        # 混合
        assert cjk_display_width("a你好b") == 6    # 1 + 2 + 2 + 1
        # 边界
        assert cjk_display_width("") == 0
        assert cjk_display_width(" ") == 1

    def test_br_in_cell(self, handler: TableHandler, engine: MockEngine):
        """<br> 在单元格中被拆分为多行。

        验证 _split_by_br 正确拆分含 <br> 标签的单元格，
        且拆分后的各部分通过 _render_inline 渲染后拼入同一单元格。
        """
        token = Token(TokenType.TABLE, "", {
            "rows": [
                ["Item", "Description"],
                ["Line1<br>Line2<br>Line3", "Single"],
            ],
            "alignments": ["left", "left"],
        })
        handler.handle(token, engine)
        table = engine.last_renderable
        assert table is not None
        assert len(table.columns) == 2
        assert table.row_count == 1

    def test_alignments_more_than_columns(self, handler: TableHandler, engine: MockEngine):
        """alignments 比列数多，多余的对齐被截断。

        验证 (alignments + ...)[:num_cols] 的截断行为。
        """
        token = Token(TokenType.TABLE, "", {
            "rows": [["A", "B"], ["1", "2"]],
            "alignments": ["left", "center", "right", "left"],
        })
        handler.handle(token, engine)
        table = engine.last_renderable
        assert table is not None
        assert len(table.columns) == 2
        assert table.columns[0].justify == "left"
        assert table.columns[1].justify == "center"

    def test_alignments_less_than_columns(self, handler: TableHandler, engine: MockEngine):
        """alignments 比列数少，剩余列使用 left 作为默认值。"""
        token = Token(TokenType.TABLE, "", {
            "rows": [["A", "B", "C"], ["1", "2", "3"]],
            "alignments": ["right"],
        })
        handler.handle(token, engine)
        table = engine.last_renderable
        assert table is not None
        assert len(table.columns) == 3
        assert table.columns[0].justify == "right"
        assert table.columns[1].justify == "left"
        assert table.columns[2].justify == "left"


# ═══════════════════════════════════════════════════════════
# TestTableParserUtils
# ═══════════════════════════════════════════════════════════

class TestTableParserUtils:
    """测试 _table_utils.py 中的辅助函数（纯函数，无 Mock）。"""

    # ── _has_only_chars ──────────────────────────────────

    def test_has_only_chars_all_match(self):
        """字符串完全由指定字符集构成。"""
        assert _has_only_chars("---", "-") is True
        assert _has_only_chars(":--:", ":-") is True
        assert _has_only_chars("|||", "|") is True

    def test_has_only_chars_not_match(self):
        """字符串包含字符集之外的字符。"""
        assert _has_only_chars("-a-", "-") is False
        assert _has_only_chars("a", "-") is False

    def test_has_only_chars_empty(self):
        """空字符串返回 True（无违禁字符）。"""
        assert _has_only_chars("", "-") is True

    def test_has_only_chars_single_char(self):
        """单字符匹配。"""
        assert _has_only_chars("-", "-") is True
        assert _has_only_chars(":", "-:") is True

    # ── _is_table_separator ──────────────────────────────

    def test_is_table_separator_basic(self):
        """标准分隔行。"""
        assert _is_table_separator("|---|---|") is True
        assert _is_table_separator("|:---|:---:|") is True

    def test_is_table_separator_not(self):
        """非分隔行（普通文本行）。"""
        assert _is_table_separator("hello") is False

    def test_is_table_separator_no_pipe(self):
        """无 | 字符的行不是分隔行。"""
        assert _is_table_separator("---") is False

    def test_is_table_separator_less_than_two_parts(self):
        """分隔后不足 2 段则判定为否。"""
        assert _is_table_separator("|---|") is False   # 只有 1 个非空段

    def test_is_table_separator_left_right_align(self):
        """左对齐 / 右对齐分隔行。"""
        assert _is_table_separator("|:---|---:|") is True
        assert _is_table_separator("|---|:---") is True

    # ── _is_table_row ───────────────────────────────────

    def test_is_table_row_basic(self):
        """标准表格行。"""
        assert _is_table_row("| a | b |") is True
        assert _is_table_row("|a|b|") is True

    def test_is_table_row_separator_excluded(self):
        """分隔行不被判定为表格行。"""
        assert _is_table_row("|---|---|") is False

    def test_is_table_row_no_pipe(self):
        """无 | 的不是表格行。"""
        assert _is_table_row("hello world") is False

    def test_is_table_row_single_pipe(self):
        """只有一个 | 的不是表格行（需要至少 2 个 |）。"""
        assert _is_table_row("|hello") is False

    def test_is_table_row_escaped_pipe(self):
        """转义竖线 \\| 不影响表格行检测。"""
        assert _is_table_row(r"| a \| b |") is True

    def test_is_table_row_leading_pipe(self):
        """以 | 开头的表格行。"""
        assert _is_table_row("| x | y | z |") is True

    # ── _parse_table_row ────────────────────────────────

    def test_parse_table_row_basic(self):
        """解析标准表格行。"""
        result = _parse_table_row("| a | b | c |")
        assert result == ["a", "b", "c"]

    def test_parse_table_row_no_leading_trailing_pipe(self):
        """无首尾竖线的表格行。"""
        result = _parse_table_row("a | b | c")
        assert result == ["a", "b", "c"]

    def test_parse_table_row_trim_spaces(self):
        """单元格内容被 strip。"""
        result = _parse_table_row("|  hello  |  world  |")
        assert result == ["hello", "world"]

    def test_parse_table_row_escaped_pipe(self):
        """转义竖线 \\| 被正确处理。"""
        result = _parse_table_row(r"| a \| b | c |")
        assert result == ["a | b", "c"]

    def test_parse_table_row_empty_cells(self):
        """空单元格。"""
        result = _parse_table_row("|| hello |||")
        assert result == ["", "hello", "", ""]

    # ── _parse_table_alignments ──────────────────────────

    def test_parse_table_alignments_all_left(self):
        """全默认（无冒号）→ left。"""
        result = _parse_table_alignments("|---|---|---|")
        assert result == ["left", "left", "left"]

    def test_parse_table_alignments_mixed(self):
        """混合对齐方式。"""
        result = _parse_table_alignments("|:---|:---:|---:|")
        assert result == ["left", "center", "right"]

    def test_parse_table_alignments_all_center(self):
        """全部居中。"""
        result = _parse_table_alignments("|:---:|:---:|")
        assert result == ["center", "center"]

    def test_parse_table_alignments_single(self):
        """单列对齐。"""
        result = _parse_table_alignments("|---:|")
        assert result == ["right"]

    # ── _SAFE_SENTINEL ──────────────────────────────────

    def test_safe_sentinel_not_in_normal_text(self):
        """_SAFE_SENTINEL 是特殊标记，不应出现在正常文本中。"""
        assert _SAFE_SENTINEL == "\uffffPIPE\uffff"
        # 确认不会误匹配普通文本
        text = "normal | text"
        assert _SAFE_SENTINEL not in text
