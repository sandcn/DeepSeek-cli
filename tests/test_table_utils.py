"""test_table_utils — 对 _table_utils 模块导出函数进行全面边界测试。"""

from __future__ import annotations

from src.renderer._table_utils import (
    _has_only_chars,
    _is_table_row,
    _is_table_separator,
    _parse_table_row,
    _parse_table_alignments,
    _SAFE_SENTINEL,
)


# ── _has_only_chars ────────────────────────────────────


class TestHasOnlyChars:
    """测试 _has_only_chars(s, chars)：字符串是否仅包含指定字符集中的字符。"""

    def test_empty_string(self):
        """空字符串应返回 True"""
        assert _has_only_chars("", "abc") is True

    def test_all_chars_in_set(self):
        """全部字符都在 charset 中 → True"""
        assert _has_only_chars("abc", "abc") is True

    def test_all_chars_in_subset(self):
        """字符是 charset 的子集 → True"""
        assert _has_only_chars("aaa", "abc") is True

    def test_contains_char_outside_set(self):
        """包含 charset 之外的字符 → False"""
        assert _has_only_chars("abcd", "abc") is False

    def test_single_char_in_set(self):
        """单个字符且在 charset 中 → True"""
        assert _has_only_chars("a", "abc") is True

    def test_single_char_not_in_set(self):
        """单个字符不在 charset 中 → False"""
        assert _has_only_chars("x", "abc") is False

    def test_charset_is_empty(self):
        """charset 为空时，任何非空 s 都应返回 False"""
        assert _has_only_chars("a", "") is False

    def test_empty_charset_empty_s(self):
        """charset 和 s 都为空 → True"""
        assert _has_only_chars("", "") is True

    def test_whitespace_not_in_set(self):
        """包含空格/空白字符但不在 charset 中 → False"""
        assert _has_only_chars("a b", "ab") is False

    def test_whitespace_in_set(self):
        """空格在 charset 中 → True"""
        assert _has_only_chars("a b", "ab ") is True

    def test_only_whitespace(self):
        """只有空格，且空格不在 charset → False"""
        assert _has_only_chars("   ", "a") is False

    def test_only_whitespace_in_set(self):
        """只有空格且空格在 charset → True"""
        assert _has_only_chars("   ", " ") is True

    def test_special_chars(self):
        """特殊字符在 charset 中 → True"""
        assert _has_only_chars("@#$", "@#$") is True

    def test_special_chars_not_in_set(self):
        """特殊字符不在 charset 中 → False"""
        assert _has_only_chars("@", "abc") is False

    def test_dash_and_colon(self):
        """分隔行典型字符：`-` 和 `:` → True"""
        assert _has_only_chars("---:---", "-:") is True

    def test_mixed_valid_and_invalid(self):
        """混合有效与无效字符 → False"""
        assert _has_only_chars("---a---", "-") is False

    def test_newline_not_in_set(self):
        """换行符不在 charset 中 → False"""
        assert _has_only_chars("a\nb", "ab") is False

    def test_unicode_chars_in_set(self):
        """Unicode 字符在 charset 中 → True"""
        assert _has_only_chars("\u00e9\u00e8", "\u00e9\u00e8") is True

    def test_unicode_chars_not_in_set(self):
        """Unicode 字符不在 charset 中 → False"""
        assert _has_only_chars("\u00e9", "abc") is False


# ── _is_table_row ──────────────────────────────────────


class TestIsTableRow:
    """测试 _is_table_row(stripped)：判断是否为表格行。"""

    def test_normal_table_row(self):
        """正常表格行 `| a | b |` → True"""
        assert _is_table_row("| a | b |") is True

    def test_normal_table_row_no_spaces(self):
        """无多余空格的表格行 `|a|b|` → True"""
        assert _is_table_row("|a|b|") is True

    def test_no_pipe(self):
        """无 pipe → False"""
        assert _is_table_row("hello world") is False

    def test_single_pipe_only(self):
        """只有单个 pipe `|` → False"""
        assert _is_table_row("|") is False

    def test_separator_row(self):
        """分隔行 `|---|---|` → False"""
        assert _is_table_row("|---|---|") is False

    def test_separator_with_alignment(self):
        """带对齐标记的分隔行 `|:---|:---:|---:|` → False"""
        assert _is_table_row("|:---|:---:|---:|") is False

    def test_starts_and_ends_with_pipe(self):
        """以 `|` 开头和结尾 `| a | b | c |` → True"""
        assert _is_table_row("| a | b | c |") is True

    def test_no_leading_pipe_two_pipes(self):
        """不含开头的 `|` 但有两个以上 pipe `a|b|c` → True"""
        assert _is_table_row("a|b|c") is True

    def test_no_leading_pipe_single_pipe(self):
        """不含开头的 `|` 且只有一个 pipe `a|b` → False"""
        assert _is_table_row("a|b") is False

    def test_escaped_pipe(self):
        """转义 pipe `\\| a | b |` → 转义后的 pipe 不影响判断"""
        assert _is_table_row("\\| a | b |") is True

    def test_escaped_pipe_only(self):
        """只有转义 pipe 无其他 pipe `a \\| b` → False"""
        assert _is_table_row("a \\| b") is False

    def test_empty_cells(self):
        """空单元格 `|a||c|` → True"""
        assert _is_table_row("|a||c|") is True

    def test_all_empty_cells(self):
        """全空行 `|||` → True（有 2+ 个 pipe）"""
        assert _is_table_row("|||") is True

    def test_single_cell(self):
        """单个单元格 `| a |` → True"""
        assert _is_table_row("| a |") is True

    def test_leading_pipe_only_no_content(self):
        """仅开头有 `|` 但内容少 `|a` → False（只有一个 pipe）"""
        assert _is_table_row("|a") is False

    def test_trailing_pipe_only(self):
        """仅结尾有 `|` `a|` → False（只有一个 pipe）"""
        assert _is_table_row("a|") is False

    def test_single_pipe_with_content(self):
        """单 pipe 带内容 ` a | b ` → False（count < 2）"""
        assert _is_table_row(" a | b ") is False

    def test_many_columns(self):
        """多列表格行 `| a | b | c | d | e |` → True"""
        assert _is_table_row("| a | b | c | d | e |") is True

    def text_string_with_no_bars_after_escape(self):
        """全部 pipe 被转义后无真实 pipe `\\|\\|` → False"""
        assert _is_table_row("\\|\\|") is False

    def test_escaped_pipe_middle(self):
        """中间有转义 pipe `| a \\| b | c |` → True"""
        assert _is_table_row("| a \\| b | c |") is True

    def test_empty_string(self):
        """空字符串 → False"""
        assert _is_table_row("") is False

    def test_only_spaces(self):
        """只有空白 → False"""
        assert _is_table_row("   ") is False

    def test_separator_single_column(self):
        """单列分隔行（parts < 2） → False"""
        assert _is_table_row("|---|") is True  # count('|')=2 >=2, not separator

    def test_minimal_separator(self):
        """最小分隔行 `|-|-|` → False"""
        assert _is_table_row("|-|-|") is False  # _is_table_separator returns True


# ── _is_table_separator ────────────────────────────────


class TestIsTableSeparator:
    """测试 _is_table_separator(stripped)：判断是否为表格分隔行。"""

    def test_standard_separator(self):
        """标准分隔行 `|---|---|` → True"""
        assert _is_table_separator("|---|---|") is True

    def test_aligned_left(self):
        """左对齐分隔行 `|:---|---|` → True"""
        assert _is_table_separator("|:---|---|") is True

    def test_aligned_center(self):
        """居中对齐分隔行 `|:---:|` 只有一列 → False（parts < 2）"""
        assert _is_table_separator("|:---:|") is False

    def test_aligned_right(self):
        """右对齐分隔行 `|---:|---|` → True"""
        assert _is_table_separator("|---:|---|") is True

    def test_mixed_alignments(self):
        """混合对齐 `|:---|:---:|---:|` → True"""
        assert _is_table_separator("|:---|:---:|---:|") is True

    def test_invalid_chars(self):
        """包含非 `-` `:` `|` ` ` 之外字符 `|---a---|---|` → False"""
        assert _is_table_separator("|---a---|---|") is False

    def test_empty_string(self):
        """空字符串 → False"""
        assert _is_table_separator("") is False

    def test_less_than_two_columns(self):
        """少于2列 `|-|` → False"""
        assert _is_table_separator("|-|") is False

    def test_all_pipe_row(self):
        """全 `|` 行 → False"""
        assert _is_table_separator("|||") is False

    def test_empty_pipe_columns(self):
        """含空列 `|---||` → parts=['---'] 只1列 → False"""
        assert _is_table_separator("|---||") is False

    def test_only_colon_and_dash_valid(self):
        """只含 `:` 和 `-` 的合法组合 `|:---|:---:|---:|` → True"""
        assert _is_table_separator("|:---|:---:|---:|") is True

    def test_contains_letters(self):
        """包含字母 `|---ABC---|` → False"""
        assert _is_table_separator("|---ABC---|") is False

    def test_only_pipe_and_whitespace(self):
        """仅 `|` 和空白 `| | |` → True，空白被 strip 掉后只剩下 '-' 检查"""
        # 每个 part 只有空白，strip 后为空字符串
        # 实际上 `| | |` 拆成 ['', ' ', ' ', ''] → parts=[' '] 只有1列 <2 → False
        assert _is_table_separator("| | |") is False

    def test_three_column_separator(self):
        """三列分隔行 `|---|---|---|` → True"""
        assert _is_table_separator("|---|---|---|") is True

    def test_single_part_separator(self):
        """parts 只有1个元素 `|--|` → False"""
        assert _is_table_separator("|--|") is False

    def test_colon_only_part(self):
        """列中只有冒号 `|:|:---|` → parts=[':', ':---']..."""
        # `|:|:---|` → split('|') → ['', ':', ':---', ''] → parts=[':', ':---']
        # ':' → replace ':' → '' → not stripped_p → False
        assert _is_table_separator("|:|:---|") is False

    def test_minimal_valid_separator_two_cols(self):
        """最小2列有效分隔行 `|-|-|` → True"""
        assert _is_table_separator("|-|-|") is True

    def test_separator_with_spaces(self):
        """分隔行中包含空格 `|--- | ---|` → True"""
        assert _is_table_separator("|--- | ---|") is True

    def test_double_colon(self):
        """双冒号 `|::---|::---|` → True（冒号只被移除）"""
        # '::---' → replace ':' → '' → '---' → only '-' → True
        assert _is_table_separator("|::---|::---|") is True

    def test_only_colons(self):
        """只有冒号 `|:|:|` → parts=[':', ':'] → 空字符串 → False"""
        assert _is_table_separator("|:|:|") is False


# ── _parse_table_row ───────────────────────────────────


class TestParseTableRow:
    """测试 _parse_table_row(row_str)：解析表格行为单元格列表。"""

    def test_standard_row(self):
        """标准行 `| a | b | c |` → ['a', 'b', 'c']"""
        assert _parse_table_row("| a | b | c |") == ["a", "b", "c"]

    def test_no_outer_pipes(self):
        """无前后 pipe `a | b | c` → ['a', 'b', 'c']"""
        assert _parse_table_row("a | b | c") == ["a", "b", "c"]

    def test_empty_cells(self):
        """空单元格 `| a || c |` → ['a', '', 'c']"""
        assert _parse_table_row("| a || c |") == ["a", "", "c"]

    def test_escaped_pipe(self):
        """转义 pipe `a \\| b` → ['a | b']"""
        assert _parse_table_row("a \\| b") == ["a | b"]

    def test_escaped_pipe_in_table(self):
        """表格中转义 pipe `| a \\| b | c |` → ['a | b', 'c']"""
        assert _parse_table_row("| a \\| b | c |") == ["a | b", "c"]

    def test_leading_trailing_whitespace(self):
        """首尾有空白 `  | a | b |  ` → ['a', 'b']"""
        assert _parse_table_row("  | a | b |  ") == ["a", "b"]

    def test_single_cell(self):
        """单单元格 `| a |` → ['a']"""
        assert _parse_table_row("| a |") == ["a"]

    def test_empty_row(self):
        """空行 `|||` → ['', '']"""
        assert _parse_table_row("|||") == ["", ""]

    def test_special_characters(self):
        """包含特殊字符 `| @ | # | $ |` → ['@', '#', '$']"""
        assert _parse_table_row("| @ | # | $ |") == ["@", "#", "$"]

    def test_many_cells(self):
        """多单元格 `| a | b | c | d | e |` → 5 个元素"""
        result = _parse_table_row("| a | b | c | d | e |")
        assert result == ["a", "b", "c", "d", "e"]

    def test_empty_string_input(self):
        """空字符串 → ['']"""
        assert _parse_table_row("") == [""]

    def test_only_pipes(self):
        """多个 pipe `|||||` → ['', '', '', '']（去掉首尾 pipe 后剩 `|||`，split 得 4 个空串）"""
        assert _parse_table_row("|||||") == ["", "", "", ""]

    def test_escaped_pipe_at_start(self):
        """开头转义 pipe `\\| a | b` → ['| a', 'b']"""
        result = _parse_table_row("\\| a | b")
        assert result == ["| a", "b"]

    def test_escaped_pipe_at_end(self):
        """结尾转义 pipe `a | b \\|` → 尾部 `|` 被 strip 掉，`\\` 保留在第二个单元格中"""
        result = _parse_table_row("a | b \\|")
        assert result == ["a", "b \\"]

    def test_cell_with_inner_spaces(self):
        """单元格内部有空格 `| hello world | foo |` → ['hello world', 'foo']"""
        assert _parse_table_row("| hello world | foo |") == ["hello world", "foo"]

    def test_single_column_no_pipes(self):
        """无 pipe 的单列内容 `hello` → ['hello']"""
        assert _parse_table_row("hello") == ["hello"]

    def test_triple_pipe(self):
        """三重 pipe `|||a||` → ['', '', 'a', '']"""
        result = _parse_table_row("|||a||")
        assert result == ["", "", "a", ""]


# ── _parse_table_alignments ────────────────────────────


class TestParseTableAlignments:
    """测试 _parse_table_alignments(sep_str)：解析表格对齐方式。"""

    def test_left_aligned(self):
        """左对齐 `|:---|---|` → ['left', 'left']"""
        assert _parse_table_alignments("|:---|---|") == ["left", "left"]

    def test_right_aligned(self):
        """右对齐 `|---:|---|` → ['right', 'left']"""
        assert _parse_table_alignments("|---:|---|") == ["right", "left"]

    def test_center_aligned(self):
        """居中 `|:---:|---|` → ['center', 'left']"""
        assert _parse_table_alignments("|:---:|---|") == ["center", "left"]

    def test_mixed_alignment(self):
        """混合对齐 `|:---|:---:|---:|` → ['left', 'center', 'right']"""
        assert _parse_table_alignments("|:---|:---:|---:|") == ["left", "center", "right"]

    def test_no_colons(self):
        """无冒号 `|---|---|---|` → ['left', 'left', 'left']"""
        assert _parse_table_alignments("|---|---|---|") == ["left", "left", "left"]

    def test_partial_colons(self):
        """部分有冒号部分无 `|:---|---:|` → ['left', 'right']"""
        assert _parse_table_alignments("|:---|---:|") == ["left", "right"]

    def test_empty_column_with_alignment(self):
        """带空 `||:---:|` → ['left', 'center']"""
        assert _parse_table_alignments("||:---:|") == ["left", "center"]

    def test_colon_position_right_vs_normal(self):
        """冒号在右 `|--:|` → ['right']（单列场景）"""
        assert _parse_table_alignments("|--:|") == ["right"]

    def test_colon_position_left(self):
        """冒号在左 `|:--|` → ['left']"""
        assert _parse_table_alignments("|:--|") == ["left"]

    def test_all_three_alignments(self):
        """全部三种对齐 `|:---|:---:|---:|` → ['left', 'center', 'right']"""
        assert _parse_table_alignments("|:---|:---:|---:|") == ["left", "center", "right"]

    def test_two_column_left_right(self):
        """两列左/右 `|:---|---:|` → ['left', 'right']"""
        assert _parse_table_alignments("|:---|---:|") == ["left", "right"]

    def test_colon_only_cell(self):
        """列中纯冒号 `|::|:::|` → 冒号即同时 start/end → ['center', 'center']"""
        result = _parse_table_alignments("|::|:::|")
        assert result == ["center", "center"]

    def test_single_column_no_colon(self):
        """单列无冒号 `|---|` → ['left']"""
        assert _parse_table_alignments("|---|") == ["left"]

    def test_only_right_aligned(self):
        """全部右对齐 `|---:|---:|---:|` → ['right', 'right', 'right']"""
        assert _parse_table_alignments("|---:|---:|---:|") == ["right", "right", "right"]


# ── _SAFE_SENTINEL ─────────────────────────────────────


class TestSafeSentinel:
    """测试 _SAFE_SENTINEL 常量的正确性和转义 pipe 恢复。"""

    def test_sentinel_value(self):
        """验证 sentinel 值为 `\\uffffPIPE\\uffff`"""
        assert _SAFE_SENTINEL == "\uffffPIPE\uffff"

    def test_sentinel_length(self):
        """验证 sentinel 长度"""
        assert len(_SAFE_SENTINEL) == len("\uffff") + len("PIPE") + len("\uffff")

    def test_escaped_pipe_restored(self):
        """验证转义 pipe 在 _parse_table_row 中被正确恢复"""
        result = _parse_table_row("a \\| b | c")
        assert result == ["a | b", "c"]

    def test_escaped_pipe_not_in_normal_text(self):
        """验证 sentinel 中的 \\uffff 是 Unicode 非字符码位，不会出现在正常文本中"""
        # \uffff 属于 Unicode 平面中明确保留的非字符（Noncharacter），
        # 正常用户文本中不会出现此码位
        assert ord('\uffff') == 0xFFFF
        # 验证它被 _parse_table_row 正确处理
        result = _parse_table_row("a \\| b")
        assert result == ["a | b"]

    def test_multiple_escaped_pipes(self):
        """多个转义 pipe 被正确恢复"""
        result = _parse_table_row("| a \\| b | c \\| d |")
        assert result == ["a | b", "c | d"]

    def test_consecutive_escaped_pipes(self):
        """连续转义 pipe `a \\|\\| b` → ['a || b']"""
        result = _parse_table_row("a \\|\\| b")
        assert result == ["a || b"]

    def test_no_sentinel_in_parse_table_alignments(self):
        """_parse_table_alignments 中 sentinel 不出现"""
        aligns = _parse_table_alignments("|:---|:---:|---:|")
        for a in aligns:
            assert _SAFE_SENTINEL not in a
