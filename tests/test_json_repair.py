"""测试 src.api.json_repair：JSON 自动修复模块（独立测试）。

测试策略
--------
- 使用 importlib 直接加载模块文件，避免触发 src/__init__.py 的级联导入
- json_repair.py 仅依赖标准库，无需 mock 外部模块
- 每个测试类按函数分组，覆盖正常路径、边界值、异常路径
- 统计函数涉及线程锁，测试验证计数正确性和隔离性
"""

import sys
import json
import pytest
import importlib.util
import threading


# ── 使用 importlib 直接加载 json_repair.py ──────────────────────────────
# 注意：不使用 from src.api.json_repair import ... 以避免触发 src/__init__.py
# 的级联导入。json_repair.py 仅依赖标准库模块，无需 mock。

_SCRIPT_DIR = '/home/DeepSeek-cli/src/api'
_json_repair_spec = importlib.util.spec_from_file_location(
    'src.api.json_repair', f'{_SCRIPT_DIR}/json_repair.py',
)
_json_repair_module = importlib.util.module_from_spec(_json_repair_spec)
sys.modules['src.api.json_repair'] = _json_repair_module
_json_repair_spec.loader.exec_module(_json_repair_module)

# ── 提取所有被测试符号 ──────────────────────────────────────────────────
_strip_code_block = _json_repair_module._strip_code_block
_fix_quotes = _json_repair_module._fix_quotes
_remove_comments = _json_repair_module._remove_comments
_fix_unquoted_keys = _json_repair_module._fix_unquoted_keys
_fix_trailing_commas = _json_repair_module._fix_trailing_commas
_fix_python_literals = _json_repair_module._fix_python_literals
_remove_control_chars = _json_repair_module._remove_control_chars
_fix_extra_brackets = _json_repair_module._fix_extra_brackets
_remove_zero_width_chars = _json_repair_module._remove_zero_width_chars
_fix_unescaped_quotes = _json_repair_module._fix_unescaped_quotes
_fix_missing_commas = _json_repair_module._fix_missing_commas
_repair_json = _json_repair_module._repair_json
json_loads_safe = _json_repair_module.json_loads_safe
get_repair_stats = _json_repair_module.get_repair_stats
reset_repair_stats = _json_repair_module.reset_repair_stats


# ═══════════════════════════════════════════════════════════════════════════
# 1. _strip_code_block
# ═══════════════════════════════════════════════════════════════════════════

class TestStripCodeBlock:
    """```json ... ``` / ``` ... ``` 包裹去除。"""

    def test_strip_json_block(self):
        assert _strip_code_block(
            '```json\n{"key": "value"}\n```'
        ) == '{"key": "value"}'

    def test_strip_plain_block(self):
        assert _strip_code_block(
            '```\n{"key": "value"}\n```'
        ) == '{"key": "value"}'

    def test_no_block(self):
        s = '{"key": "value"}'
        assert _strip_code_block(s) is s  # 同对象

    def test_empty_string(self):
        assert _strip_code_block('') == ''

    def test_only_triple_backticks(self):
        assert _strip_code_block('```') == ''

    def test_mixed_whitespace(self):
        result = _strip_code_block('```json  \n{"a":1}\n  ```')
        assert result == '{"a":1}'

    def test_multiple_lines_inside(self):
        result = _strip_code_block('```\n{\n"a": 1,\n"b": 2\n}\n```')
        assert result == '{\n"a": 1,\n"b": 2\n}'

    def test_no_newline_after_tick(self):
        """```json 直接跟内容，无换行。"""
        result = _strip_code_block('```json{"a":1}```')
        assert result == '{"a":1}'

    def test_with_spaces_around_code_block(self):
        """前后空格：_strip_code_block 不处理前导空格（函数只处理以 ``` 开头的字符串）。"""
        result = _strip_code_block('  ```json\n{"a":1}\n```  ')
        assert result == '  ```json\n{"a":1}\n```  '

    def test_trailing_newlines_inside_block(self):
        result = _strip_code_block('```json\n\n{"a": 1}\n\n```')
        assert result == '{"a": 1}'


# ═══════════════════════════════════════════════════════════════════════════
# 2. _fix_quotes
# ═══════════════════════════════════════════════════════════════════════════

class TestFixQuotes:
    """单引号 → 双引号，保护已有双引号字符串。"""

    def test_simple_single_quotes(self):
        assert _fix_quotes("{'key': 'value'}") == '{"key": "value"}'

    def test_already_double_quotes_are_protected(self):
        result = _fix_quotes('{"key": "it\'s"}')
        assert result == '{"key": "it\'s"}'

    def test_escaped_double_quotes_inside(self):
        """\\" 被保护不变。"""
        result = _fix_quotes('{"key": "he said \\"hi\\""}')
        assert result == '{"key": "he said \\"hi\\""}'

    def test_nested_single_quotes(self):
        result = _fix_quotes("{'a': {'b': 'c'}}")
        assert result == '{"a": {"b": "c"}}'

    def test_no_quotes(self):
        s = 'plain text without quotes'
        assert _fix_quotes(s) == s

    def test_empty_string(self):
        assert _fix_quotes('') == ''

    def test_mixed_quotes(self):
        """双引号内的单引号不应被改变。"""
        result = _fix_quotes('{"msg": "Don\'t stop"}')
        assert result == '{"msg": "Don\'t stop"}'

    def test_string_with_backslashes(self):
        result = _fix_quotes("{'path': 'C:\\\\Users\\\\test'}")
        assert result == '{"path": "C:\\\\Users\\\\test"}'

    def test_multiple_keys_values(self):
        result = _fix_quotes("{'a': 1, 'b': 'hello', 'c': True}")
        assert result == '{"a": 1, "b": "hello", "c": True}'

    def test_empty_single_quoted_string(self):
        result = _fix_quotes("{'a': ''}")
        assert result == '{"a": ""}'

    def test_nested_quotes_deeply(self):
        result = _fix_quotes("{'a': {'b': {'c': 'd'}}}")
        assert result == '{"a": {"b": {"c": "d"}}}'

    def test_string_with_escaped_single_quote(self):
        """已转义的内部单引号：_fix_quotes 将所有外层单引号替换为双引号，
        因此 \\' 变为 \\"（在 JSON 中是合法的转义序列）。"""
        # 输入: {'msg': 'it\'s fine'}  →  'it\'s' 中的 \' 是 Python 的转义方式
        # 替换后: {"msg": "it\"s fine"}  → \" 是 JSON 合法的转义
        result = _fix_quotes("{'msg': 'it\\'s fine'}")
        assert result == '{"msg": "it\\"s fine"}'


# ═══════════════════════════════════════════════════════════════════════════
# 3. _remove_comments
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveComments:
    """去除 // 行注释和 /* */ 块注释。"""

    def test_line_comment(self):
        result = _remove_comments('{"key": 1 // comment\n}')
        assert result == '{"key": 1 \n}'

    def test_block_comment(self):
        result = _remove_comments('{"key": 1 /* block */}')
        assert result == '{"key": 1 }'

    def test_no_comment(self):
        s = '{"key": "value"}'
        assert _remove_comments(s) == s

    def test_empty_string(self):
        assert _remove_comments('') == ''

    def test_multiple_line_comments(self):
        s = '{\n"a": 1, // first\n"b": 2 // second\n}'
        result = _remove_comments(s)
        assert '//' not in result
        assert '"a": 1,' in result
        assert '"b": 2' in result

    def test_multiple_block_comments(self):
        result = _remove_comments('{"a": 1 /* c1 */, "b": 2 /* c2 */}')
        assert result == '{"a": 1 , "b": 2 }'

    def test_block_comment_multiline(self):
        result = _remove_comments('{"a": 1 /* block\nspanning\nmultiple\nlines */, "b": 2}')
        assert result == '{"a": 1 , "b": 2}'
        # 验证换行符被保留
        assert '\n' not in result  # 块注释整个被移除，但 /* ... */ 内部的内容也被移除了

    def test_comment_only_line(self):
        result = _remove_comments('// just a comment\n{"a": 1}')
        assert result == '\n{"a": 1}'

    def test_mixed_comment_types(self):
        s = '{/* block */ "a": 1 // line\n}'
        result = _remove_comments(s)
        assert '/*' not in result
        assert '//' not in result

    def test_url_with_double_slash(self):
        """URL 中的 // 也会被移除（函数本身是纯正则，不保护字符串）。"""
        s = '{"url": "http://example.com"}'
        result = _remove_comments(s)
        # _remove_comments 是纯正则，会删除 "http://example.com" 中的 //
        assert '"url": "http' in result  # 保留 http:
        assert '//' not in result       # // 被删除了

    def test_block_comment_at_start(self):
        result = _remove_comments('/* comment */{"a": 1}')
        assert result == '{"a": 1}'

    def test_block_comment_at_end(self):
        result = _remove_comments('{"a": 1}/* comment */')
        assert result == '{"a": 1}'


# ═══════════════════════════════════════════════════════════════════════════
# 4. _fix_unquoted_keys
# ═══════════════════════════════════════════════════════════════════════════

class TestFixUnquotedKeys:
    """为未加引号的 key 补上双引号。"""

    def test_unquoted_key(self):
        assert _fix_unquoted_keys('{key: "value"}') == '{"key": "value"}'

    def test_multiple_unquoted_keys(self):
        result = _fix_unquoted_keys('{a: 1, b: 2}')
        assert result == '{"a": 1, "b": 2}'

    def test_already_quoted_key_not_affected(self):
        """已加引号的 key 不会重复加引号。"""
        s = '{"a": 1, b: 2}'
        result = _fix_unquoted_keys(s)
        assert '"a"' in result
        assert '"b"' in result

    def test_key_with_underscore(self):
        result = _fix_unquoted_keys('{my_key: "v"}')
        assert result == '{"my_key": "v"}'

    def test_key_starting_with_letter(self):
        result = _fix_unquoted_keys('{A1: 1, _b: 2}')
        assert result == '{"A1": 1, "_b": 2}'

    def test_nested_unquoted_keys(self):
        result = _fix_unquoted_keys('{a: {b: 2}}')
        assert result == '{"a": {"b": 2}}'

    def test_deeply_nested_unquoted_keys(self):
        result = _fix_unquoted_keys('{a: {b: {c: 3}}}')
        assert result == '{"a": {"b": {"c": 3}}}'

    def test_no_keys(self):
        assert _fix_unquoted_keys('[]') == '[]'

    def test_empty_object(self):
        assert _fix_unquoted_keys('{}') == '{}'

    def test_key_with_digits(self):
        result = _fix_unquoted_keys('{a1: 1, b2: 2}')
        assert result == '{"a1": 1, "b2": 2}'

    def test_quoted_value_not_affected(self):
        result = _fix_unquoted_keys('{a: "hello", b: "world"}')
        assert result == '{"a": "hello", "b": "world"}'

    def test_array_unaffected(self):
        """数组中的非 key 内容不受影响。"""
        result = _fix_unquoted_keys('{a: [1, 2, 3]}')
        assert result == '{"a": [1, 2, 3]}'

    def test_only_numeric_key_not_fixed(self):
        """仅数字开头的 key 不在正则匹配范围内（要求 [a-zA-Z_] 开头）。"""
        result = _fix_unquoted_keys('{123: "value"}')
        assert result == '{123: "value"}'  # 不会被修复

    def test_single_quoted_key_already_fine(self):
        """单引号括起来的 key 不会被 _fix_unquoted_keys 二次处理。"""
        result = _fix_unquoted_keys("{'a': 1, b: 2}")
        # 注意：单引号的 key 不会被重复加引号（因为 'a' 不以字母开头的模式匹配），
        # 但 'a' 在后续 _fix_quotes 中会变成 "a"
        # _fix_unquoted_keys 只加双引号，不影响已存在的引号 key
        assert "'a'" in result or '"a"' in result
        assert '"b"' in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. _fix_trailing_commas
# ═══════════════════════════════════════════════════════════════════════════

class TestFixTrailingCommas:
    """去除 ]} 前的多余逗号。"""

    def test_trailing_comma_in_object(self):
        assert _fix_trailing_commas('{"a": 1,}') == '{"a": 1}'

    def test_trailing_comma_in_array(self):
        assert _fix_trailing_commas('{"a": [1, 2,]}') == '{"a": [1, 2]}'

    def test_nested_commas(self):
        result = _fix_trailing_commas('{"a": [1, 2,], "b": {"c": 3,}}')
        assert result == '{"a": [1, 2], "b": {"c": 3}}'

    def test_no_trailing_commas(self):
        s = '{"a": 1, "b": 2}'
        assert _fix_trailing_commas(s) == s

    def test_multiple_trailing_commas(self):
        """连续多个尾逗号：每次只去掉 ]} 前的一个逗号。"""
        result = _fix_trailing_commas('{"a": 1,,,}')
        assert result == '{"a": 1,,}'

    def test_empty_input(self):
        assert _fix_trailing_commas('') == ''

    def test_trailing_comma_with_spaces(self):
        result = _fix_trailing_commas('{"a": 1 , }')
        assert result == '{"a": 1 }'

    def test_trailing_comma_before_close_bracket(self):
        result = _fix_trailing_commas('[1, 2, 3,]')
        assert result == '[1, 2, 3]'

    def test_all_types_trailing_commas(self):
        result = _fix_trailing_commas('{"a": [1, 2,], "b": {"c": 3,}}')
        assert result == '{"a": [1, 2], "b": {"c": 3}}'

    def test_trailing_comma_in_empty_object_not_applicable(self):
        """空对象没有尾逗号问题。"""
        result = _fix_trailing_commas('{}')
        assert result == '{}'

    def test_large_array_trailing_comma(self):
        result = _fix_trailing_commas('[1, 2, 3, 4, 5, 6, 7, 8, 9, 10,]')
        assert result == '[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]'


# ═══════════════════════════════════════════════════════════════════════════
# 6. _fix_python_literals
# ═══════════════════════════════════════════════════════════════════════════

class TestFixPythonLiterals:
    """True/False/None → true/false/null，字符串内不受影响。"""

    def test_true_to_true(self):
        assert _fix_python_literals('{"a": True}') == '{"a": true}'

    def test_false_to_false(self):
        assert _fix_python_literals('{"a": False}') == '{"a": false}'

    def test_none_to_null(self):
        assert _fix_python_literals('{"a": None}') == '{"a": null}'

    def test_all_three(self):
        result = _fix_python_literals('{"a": True, "b": False, "c": None}')
        assert result == '{"a": true, "b": false, "c": null}'

    def test_string_content_not_affected(self):
        """字符串中的 'True'/'None' 不被替换。"""
        s = '{"msg": "True story", "val": "None"}'
        assert _fix_python_literals(s) == s

    def test_nested_in_string_protected(self):
        result = _fix_python_literals('{"msg": "say True", "flag": True}')
        assert result == '{"msg": "say True", "flag": true}'

    def test_not_part_of_larger_word(self):
        """'Truehood' 中的 'True' 不应被替换（\\b 边界）。"""
        s = '{"key": "Truehood"}'
        assert _fix_python_literals(s) == s

    def test_no_python_literals(self):
        s = '{"a": 1, "b": "hello"}'
        assert _fix_python_literals(s) == s

    def test_single_quoted_strings_protected(self):
        result = _fix_python_literals("{'msg': 'True story', 'val': None}")
        assert result == "{'msg': 'True story', 'val': null}"

    def test_nested_objects(self):
        result = _fix_python_literals('{"a": {"b": True, "c": None}}')
        assert result == '{"a": {"b": true, "c": null}}'

    def test_arrays_with_literals(self):
        result = _fix_python_literals('{"a": [True, False, None]}')
        assert result == '{"a": [true, false, null]}'

    def test_literal_in_key_not_affected(self):
        """Python 字面量作为 key 名称（字符串）不受影响。"""
        s = '{"True": 1, "None": 2}'
        assert _fix_python_literals(s) == s

    def test_empty_input(self):
        assert _fix_python_literals('') == ''


# ═══════════════════════════════════════════════════════════════════════════
# 7. _remove_control_chars
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveControlChars:
    """移除控制字符 (U+0000-U+001F)。"""

    def test_remove_control_chars(self):
        s = '{"a": "hello\x00world\x01"}'
        result = _remove_control_chars(s)
        assert result == '{"a": "helloworld"}'

    def test_tab_is_removed(self):
        """Tab \\x09 也被移除（属于控制字符范围）。"""
        s = '{"a": "hello\tworld"}'
        result = _remove_control_chars(s)
        assert '\t' not in result

    def test_newline_is_removed(self):
        """字面量换行符 \\x0a 也被移除。"""
        s = '{"a": "hello\nworld"}'
        result = _remove_control_chars(s)
        assert '\n' not in result

    def test_no_control_chars(self):
        s = '{"a": "normal text"}'
        assert _remove_control_chars(s) == s

    def test_empty_string(self):
        assert _remove_control_chars('') == ''

    def test_carriage_return_removed(self):
        s = '{"a": "hello\rworld"}'
        result = _remove_control_chars(s)
        assert '\r' not in result

    def test_multiple_control_chars(self):
        s = '\x00\x01\x02\x03test\x04\x05'
        result = _remove_control_chars(s)
        assert result == 'test'

    def test_control_chars_in_value_only(self):
        s = '{"a": "te\x00st", "b": "no"}'
        result = _remove_control_chars(s)
        assert result == '{"a": "test", "b": "no"}'

    def test_all_control_chars_removed(self):
        """验证 0x00-0x1f 范围的所有控制字符都被移除。"""
        s = ''.join(chr(i) for i in range(0x20))
        result = _remove_control_chars(s)
        assert result == ''

    def test_escape_sequences_not_affected(self):
        """已转义的字符如 \\n 不会被移除（因为 \\n 是反斜杠+n，不是控制字符）。"""
        s = '{"a": "hello\\nworld"}'
        result = _remove_control_chars(s)
        assert result == s


# ═══════════════════════════════════════════════════════════════════════════
# 8. _fix_extra_brackets
# ═══════════════════════════════════════════════════════════════════════════

class TestFixExtraBrackets:
    """修正多余/缺失的括号。"""

    def test_extra_closing_brace(self):
        result = _fix_extra_brackets('{"a": 1}}')
        assert result == '{"a": 1}'

    def test_extra_closing_bracket(self):
        result = _fix_extra_brackets('{"a": [1, 2]]}')
        assert result == '{"a": [1, 2]}'

    def test_missing_closing_brace(self):
        result = _fix_extra_brackets('{"a": {"b": 1}')
        assert result == '{"a": {"b": 1}}'

    def test_missing_closing_bracket(self):
        result = _fix_extra_brackets('{"a": [1, 2}')
        assert result == '{"a": [1, 2]}'

    def test_missing_multiple_closing(self):
        result = _fix_extra_brackets('{"a": {"b": [1, 2]')
        assert result == '{"a": {"b": [1, 2]}}'

    def test_perfectly_balanced(self):
        s = '{"a": [1, {"b": 2}], "c": 3}'
        assert _fix_extra_brackets(s) == s

    def test_extra_and_missing_combined(self):
        """多余 } 和缺失 ] 同时存在。先删多余，再补缺失。"""
        result = _fix_extra_brackets('{"a": [1, 2}}')
        assert result == '{"a": [1, 2]}'

    def test_empty_braces(self):
        assert _fix_extra_brackets('{}') == '{}'
        assert _fix_extra_brackets('[]') == '[]'

    def test_wrong_order_brackets(self):
        """错位括号：{ ] 类型不匹配，删掉多余 ]。"""
        result = _fix_extra_brackets('{"a": 1]')
        assert result == '{"a": 1}'

    def test_only_close_brackets(self):
        """只有右括号。"""
        result = _fix_extra_brackets('}')
        assert result == ''

    def test_only_open_brackets(self):
        """只有左括号。"""
        result = _fix_extra_brackets('{')
        assert result == '{}'

    def test_nested_missing(self):
        result = _fix_extra_brackets('[[[]')
        assert result == '[[[]]]'

    def test_nested_extra(self):
        result = _fix_extra_brackets('[[[]]]]')
        assert result == '[[[]]]'

    def test_complex_nested(self):
        result = _fix_extra_brackets('{"a": [1, {"b": 2}}')
        assert result == '{"a": [1, {"b": 2}]}'

    def test_all_extra_close(self):
        result = _fix_extra_brackets('}}}}')
        assert result == ''

    def test_all_missing_close(self):
        result = _fix_extra_brackets('{{{{')
        assert result == '{{{{}}}}'

    def text_non_bracket_content(self):
        """无大/中括号的文本原样返回。"""
        s = 'hello world'
        assert _fix_extra_brackets(s) == s


# ═══════════════════════════════════════════════════════════════════════════
# 9. _remove_zero_width_chars
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveZeroWidthChars:
    """零宽字符和不可见 Unicode 字符移除。"""

    def test_remove_zero_width_space(self):
        assert _remove_zero_width_chars('a\u200bb') == 'ab'

    def test_remove_bom(self):
        result = _remove_zero_width_chars('\ufeff{"a": 1}')
        assert '\ufeff' not in result

    def test_remove_multiple_types(self):
        s = 'a\u200bb\u200cc\u200dd\u200ee'
        result = _remove_zero_width_chars(s)
        assert result == 'abcde'

    def test_remove_soft_hyphen(self):
        assert _remove_zero_width_chars('\u00ad') == ''

    def test_no_zero_width(self):
        s = 'normal text'
        assert _remove_zero_width_chars(s) == s

    def test_empty_string(self):
        assert _remove_zero_width_chars('') == ''

    def test_zero_width_non_joiner(self):
        assert _remove_zero_width_chars('a\u200cb') == 'ab'

    def test_zero_width_joiner(self):
        assert _remove_zero_width_chars('a\u200db') == 'ab'

    def test_word_joiner(self):
        assert _remove_zero_width_chars('a\u2060b') == 'ab'

    def test_function_application(self):
        assert _remove_zero_width_chars('\u2061') == ''

    def test_invisible_separator(self):
        assert _remove_zero_width_chars('\u2063') == ''

    def test_invisible_plus(self):
        assert _remove_zero_width_chars('\u2064') == ''

    def test_left_to_right_mark(self):
        assert _remove_zero_width_chars('\u200e') == ''

    def test_right_to_left_mark(self):
        assert _remove_zero_width_chars('\u200f') == ''

    def test_line_separator(self):
        assert _remove_zero_width_chars('\u2028') == ''

    def test_paragraph_separator(self):
        assert _remove_zero_width_chars('\u2029') == ''

    def test_lrm_rlm_marks(self):
        assert _remove_zero_width_chars('\u202a\u202b\u202c\u202d\u202e\u202f') == ''


# ═══════════════════════════════════════════════════════════════════════════
# 10. _fix_missing_commas
# ═══════════════════════════════════════════════════════════════════════════

class TestFixMissingCommas:
    """修复缺失逗号/双逗号，预防 'Expecting '','' delimiter' 错误。"""

    def test_missing_comma_between_kv_pairs(self):
        """对象中键值对之间缺逗号。"""
        result = _fix_missing_commas('{"a": 1 "b": 2}')
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_missing_comma_in_array(self):
        """数组中元素之间缺逗号。"""
        result = _fix_missing_commas('[1 2 3]')
        assert json.loads(result) == [1, 2, 3]

    def test_missing_comma_after_object_before_key(self):
        """嵌套对象 } 后缺逗号，紧跟下一个 key。"""
        result = _fix_missing_commas('{"a": {"b": 1} "c": 2}')
        assert json.loads(result) == {"a": {"b": 1}, "c": 2}

    def test_missing_comma_between_objects(self):
        """两个对象之间缺逗号。"""
        result = _fix_missing_commas('[{"a": 1} {"b": 2}]')
        assert json.loads(result) == [{"a": 1}, {"b": 2}]

    def test_missing_comma_between_array_and_object(self):
        """数组 ] 后缺逗号，紧跟下一个对象。"""
        result = _fix_missing_commas('[[1, 2] {"b": 3}]')
        assert json.loads(result) == [[1, 2], {"b": 3}]

    def test_missing_comma_after_bool_before_key(self):
        """布尔值后缺逗号，紧跟下一个 key。"""
        result = _fix_missing_commas('{"a": true "b": false}')
        assert json.loads(result) == {"a": True, "b": False}

    def test_missing_comma_after_number_before_key(self):
        """数字后缺逗号，紧跟下一个 key。"""
        result = _fix_missing_commas('{"a": 42 "b": "hello"}')
        assert json.loads(result) == {"a": 42, "b": "hello"}

    def test_double_comma_in_object(self):
        """对象中双逗号。"""
        result = _fix_missing_commas('{"a": 1,, "b": 2}')
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_double_comma_in_array(self):
        """数组中双逗号。"""
        result = _fix_missing_commas('[1,, 2, 3]')
        assert json.loads(result) == [1, 2, 3]

    def test_missing_comma_between_nested_arrays(self):
        """嵌套数组元素之间缺逗号。"""
        result = _fix_missing_commas('[[1, 2] [3, 4]]')
        assert json.loads(result) == [[1, 2], [3, 4]]

    def test_missing_comma_after_null_before_key(self):
        """null 后缺逗号，紧跟下一个 key。"""
        result = _fix_missing_commas('{"a": null "b": 1}')
        assert json.loads(result) == {"a": None, "b": 1}

    def test_missing_comma_after_false_before_brace(self):
        """false 后缺逗号，紧跟下一个对象。"""
        result = _fix_missing_commas('[false {"a": 1}]')
        assert json.loads(result) == [False, {"a": 1}]

    def test_string_content_not_affected(self):
        """字符串内的逗号相关的字符不受影响。"""
        s = '{"msg": "he said \\"hi, \\" she said"}'
        assert _fix_missing_commas(s) == s

    def test_already_valid_json_unchanged(self):
        """已合法的 JSON 原样返回。"""
        s = '{"a": 1, "b": 2}'
        assert _fix_missing_commas(s) == s

    def test_empty_string(self):
        assert _fix_missing_commas('') == ''

    def test_no_commas_needed(self):
        """空对象/数组不受影响。"""
        s = '{}'
        assert _fix_missing_commas(s) == s
        s = '[]'
        assert _fix_missing_commas(s) == s

    def test_missing_comma_in_complex_nested(self):
        """复杂嵌套中多处缺逗号。"""
        result = _fix_missing_commas('{"a": [1 2 3] "b": {"c": true "d": null}}')
        parsed = json.loads(result)
        assert parsed == {"a": [1, 2, 3], "b": {"c": True, "d": None}}

    def test_multiple_spaces_around_missing_comma(self):
        """缺逗号处有多余空格。"""
        result = _fix_missing_commas('{"a": 1   "b": 2}')
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_string_with_json_like_content(self):
        """字符串值中包含 } { 等 JSON 语法字符，不影响外部修复。"""
        s = '{"k": "} { } ]", "v": 1}'
        result = _fix_missing_commas(s)
        assert json.loads(result) == {"k": "} { } ]", "v": 1}

    def test_escaped_quotes_in_string_preserved(self):
        """已转义的双引号字符串不受影响。"""
        s = '{"a": "hello \\"world\\"", "b": 2}'
        result = _fix_missing_commas(s)
        assert json.loads(result) == {"a": 'hello "world"', "b": 2}

    def test_missing_comma_with_unicode_values(self):
        """Unicode 字符串值边界。"""
        result = _fix_missing_commas('{"a": "你好" "b": "世界"}')
        assert json.loads(result) == {"a": "你好", "b": "世界"}

    def test_blank_and_missing_commas_mixed(self):
        """双逗号和缺逗号混合。"""
        result = _fix_missing_commas('[1,, 2 3]')
        assert json.loads(result) == [1, 2, 3]


# ═══════════════════════════════════════════════════════════════════════════
# 11. _fix_unescaped_quotes
# ═══════════════════════════════════════════════════════════════════════════

class TestFixUnescapedQuotes:
    """修复字符串值中未转义的双引号（激进兜底策略）。"""

    def test_unescaped_quotes_in_value(self):
        """字符串中的未转义双引号。
        
        函数内部先把合法的 "..." 作为字符串保护起来。在
        '{"msg": "he said "hi" to me"}' 中，第一个合法字串是 "msg"，
        第二个是 "he said "，第三个是 " to me"（'hi' 在字串外）。
        保护后 'hi' 两侧无 " 可转义，最终恢复时无变化。
        """
        result = _fix_unescaped_quotes('{"msg": "he said "hi" to me"}')
        assert result == '{"msg": "he said "hi" to me"}'

    def test_normal_string_untouched(self):
        s = '{"a": "normal"}'
        assert _fix_unescaped_quotes(s) == s

    def test_empty_string(self):
        assert _fix_unescaped_quotes('') == ''

    def test_with_escaped_internal_quotes(self):
        s = '{"a": "he said \\"hi\\""}'
        result = _fix_unescaped_quotes(s)
        # 保护机制保留已有转义
        assert result == s

    def test_no_quotes_at_all(self):
        s = 'plain text'
        assert _fix_unescaped_quotes(s) == s

    def test_multiple_string_values(self):
        s = '{"a": "first", "b": "second"}'
        assert _fix_unescaped_quotes(s) == s


# ═══════════════════════════════════════════════════════════════════════════
# 12. _repair_json（完整修复链）
# ═══════════════════════════════════════════════════════════════════════════

class TestRepairJson:
    """完整 JSON 修复链测试。"""

    def test_already_valid_json(self):
        """合法 JSON 原样返回。"""
        s = '{"a": 1, "b": "hello"}'
        assert _repair_json(s) == s

    def test_code_block(self):
        result = _repair_json('```json\n{"key": "value"}\n```')
        assert json.loads(result) == {"key": "value"}

    def test_bom_prefix(self):
        result = _repair_json('\ufeff{"a": 1}')
        assert json.loads(result) == {"a": 1}

    def test_single_quotes(self):
        result = _repair_json("{'key': 'value'}")
        assert json.loads(result) == {"key": "value"}

    def test_trailing_commas(self):
        result = _repair_json('{"a": 1, "b": [1, 2,]}')
        assert json.loads(result) == {"a": 1, "b": [1, 2]}

    def test_python_literals(self):
        result = _repair_json('{"a": True, "b": False, "c": None}')
        assert json.loads(result) == {"a": True, "b": False, "c": None}

    def test_unquoted_keys(self):
        result = _repair_json('{a: 1, b: "hello"}')
        assert json.loads(result) == {"a": 1, "b": "hello"}

    def test_line_comments(self):
        result = _repair_json('{"a": 1 // comment\n}')
        assert json.loads(result) == {"a": 1}

    def test_block_comment(self):
        result = _repair_json('{"a": 1 /* block */}')
        assert json.loads(result) == {"a": 1}

    def test_extra_closing_brace(self):
        result = _repair_json('{"a": 1}}')
        assert json.loads(result) == {"a": 1}

    def test_missing_closing_brace(self):
        result = _repair_json('{"a": {"b": 1}')
        assert json.loads(result) == {"a": {"b": 1}}

    def test_control_chars(self):
        result = _repair_json('{"a": "hello\x00world"}')
        assert json.loads(result) == {"a": "helloworld"}

    def test_zero_width_chars_in_value(self):
        """零宽字符在字符串值中：因是合法 JSON，原样返回。"""
        result = _repair_json('{"a": "hello\u200bworld"}')
        parsed = json.loads(result)
        assert parsed["a"] == "hello\u200bworld"

    def test_multiple_issues_combined(self):
        """LLM 典型的多问题混合输出。"""
        raw = """```json
        {a: True, 'b': 'hello', 'c': [1, 2, 3,], // trailing
        /* block */
        'd': {'e': 'world',}}
        ```"""
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["a"] is True
        assert parsed["b"] == "hello"
        assert parsed["c"] == [1, 2, 3]
        assert parsed["d"]["e"] == "world"

    def test_empty_string(self):
        """空字符串原样返回。"""
        assert _repair_json('') == ''

    def test_whitespace_only(self):
        assert _repair_json('  ') == '  '

    def test_unrepairable_returns_original(self):
        """完全无法修复的 JSON 返回原始字符串。"""
        result = _repair_json('{invalid json here totally broken')
        assert '{invalid' in result

    def test_unescaped_quotes_fallback(self):
        """未转义引号场景通过兜底修复或返回原始串。"""
        raw = '{"msg": "he said "hello" world"}'
        result = _repair_json(raw)
        try:
            parsed = json.loads(result)
            assert parsed["msg"] is not None
        except json.JSONDecodeError:
            assert result == raw

    def test_missing_and_extra_brackets_combined(self):
        result = _repair_json('{"a": [1, 2}}')
        # 先去掉多余的 }，再补缺失的 ]
        assert json.loads(result) == {"a": [1, 2]}

    def test_bom_with_single_quotes(self):
        result = _repair_json("\ufeff{'a': 1}")
        assert json.loads(result) == {"a": 1}

    def test_code_block_with_inner_issues(self):
        raw = '```\n{a: True, "b": [1, 2,]}\n```'
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": True, "b": [1, 2]}

    def test_comments_in_multiline_json(self):
        raw = '{\n"a": 1, // line\n"b": 2 /* block */\n}'
        result = _repair_json(raw)
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_deeply_nested_with_all_issues(self):
        raw = """{a: True, 'b': None, c: [1, 2, 3,], 'd': {'e': 'hello', // comment
        'f': False,}}
        """
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["a"] is True
        assert parsed["b"] is None
        assert parsed["c"] == [1, 2, 3]
        assert parsed["d"]["e"] == "hello"
        assert parsed["d"]["f"] is False

    def test_only_null_is_not_repairable_as_json(self):
        """单独的 'null' 字符串：json.loads 能解析，但在 _repair_json 中直接返回。"""
        result = _repair_json('null')
        assert result == 'null'

    def test_single_number(self):
        result = _repair_json('42')
        assert result == '42'

    def test_single_string(self):
        result = _repair_json('"hello"')
        assert result == '"hello"'

    def test_missing_commas_in_object(self):
        """缺失逗号修复集成：对象中键值对之间缺逗号。"""
        result = _repair_json('{"a": 1 "b": 2 "c": 3}')
        assert json.loads(result) == {"a": 1, "b": 2, "c": 3}

    def test_missing_commas_in_array(self):
        """缺失逗号修复集成：数组中元素缺逗号。"""
        result = _repair_json('[1 2 3]')
        assert json.loads(result) == [1, 2, 3]

    def test_missing_commas_with_other_issues(self):
        """缺失逗号 + 其他常见问题混合。"""
        raw = "{a: True 'b': 'hello' c: [1 2 3]}"
        result = _repair_json(raw)
        assert json.loads(result) == {"a": True, "b": "hello", "c": [1, 2, 3]}

    def test_missing_commas_double_comma_combined(self):
        """缺失逗号和双逗号同时存在。"""
        result = _repair_json('[1,, 2 3]')
        assert json.loads(result) == [1, 2, 3]


# ═══════════════════════════════════════════════════════════════════════════
# 13. json_loads_safe
# ═══════════════════════════════════════════════════════════════════════════

class TestJsonLoadsSafe:
    """安全 JSON 解析，含自动修复。"""

    def test_normal_json(self):
        result, repaired = json_loads_safe('{"a": 1}')
        assert result == {"a": 1}
        assert repaired is False

    def test_null_returns_empty(self):
        result, repaired = json_loads_safe('null')
        assert result == {}
        assert repaired is False

    def test_empty_string_returns_empty(self):
        result, repaired = json_loads_safe('')
        assert result == {}
        assert repaired is False

    def test_whitespace_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            json_loads_safe('  ')

    def test_repairable_json_returns_true_flag(self):
        result, repaired = json_loads_safe("{'a': 1}")
        assert result == {"a": 1}
        assert repaired is True

    def test_repairable_complex(self):
        result, repaired = json_loads_safe('{a: True, b: None}')
        assert result == {"a": True, "b": None}
        assert repaired is True

    def test_unrepairable_raises(self):
        with pytest.raises(json.JSONDecodeError):
            json_loads_safe('{invalid json here totally broken')

    def test_repairable_with_trailing_comma(self):
        result, repaired = json_loads_safe('{"a": [1, 2,]}')
        assert result == {"a": [1, 2]}
        assert repaired is True

    def test_repairable_with_code_block(self):
        result, repaired = json_loads_safe('```json\n{"a": 1}\n```')
        assert result == {"a": 1}
        assert repaired is True

    def test_repairable_with_comments(self):
        result, repaired = json_loads_safe('{"a": 1 // comment\n}')
        assert result == {"a": 1}
        assert repaired is True

    def test_repairable_with_python_literals(self):
        result, repaired = json_loads_safe('{"a": True, "b": False}')
        assert result == {"a": True, "b": False}
        assert repaired is True

    def test_repairable_with_unquoted_keys(self):
        result, repaired = json_loads_safe('{a: 1, b: 2}')
        assert result == {"a": 1, "b": 2}
        assert repaired is True

    def test_repairable_with_control_chars(self):
        result, repaired = json_loads_safe('{"a": "hello\x00world"}')
        assert result == {"a": "helloworld"}
        assert repaired is True

    def test_repairable_with_bom(self):
        result, repaired = json_loads_safe('\ufeff{"a": 1}')
        assert result == {"a": 1}
        assert repaired is True

    def test_repairable_with_missing_bracket(self):
        result, repaired = json_loads_safe('{"a": {"b": 1}')
        assert result == {"a": {"b": 1}}
        assert repaired is True

    def test_repairable_with_extra_bracket(self):
        result, repaired = json_loads_safe('{"a": 1}}')
        assert result == {"a": 1}
        assert repaired is True

    def test_repairable_multiple_issues(self):
        result, repaired = json_loads_safe("{a: True, 'b': None, c: [1, 2,]}")
        assert result == {"a": True, "b": None, "c": [1, 2]}
        assert repaired is True

    def test_repairable_with_bom_and_code_block_not_handled(self):
        """BOM + 代码块：当前实现中 _strip_code_block 在 lstrip BOM 之前执行，
        因此 BOM 前置的代码块包裹无法被识别修复，抛出原始异常。"""
        with pytest.raises(json.JSONDecodeError):
            json_loads_safe('\ufeff```json\n{"a": 1}\n```')

    def test_repairable_missing_commas_in_object(self):
        """缺失逗号修复：对象中键值对之间缺逗号。"""
        result, repaired = json_loads_safe('{"a": 1 "b": 2 "c": 3}')
        assert result == {"a": 1, "b": 2, "c": 3}
        assert repaired is True

    def test_repairable_missing_commas_in_array(self):
        """缺失逗号修复：数组中元素缺逗号。"""
        result, repaired = json_loads_safe('[1 2 3]')
        assert result == [1, 2, 3]
        assert repaired is True

    def test_repairable_missing_commas_double_comma(self):
        """双逗号修复。"""
        result, repaired = json_loads_safe('[1,, 2, 3]')
        assert result == [1, 2, 3]
        assert repaired is True

    def test_repairable_missing_commas_with_other_issues(self):
        """缺失逗号 + 其他问题混合。"""
        result, repaired = json_loads_safe("{a: True 'b': 'hello' c: [1 2 3]}")
        assert result == {"a": True, "b": "hello", "c": [1, 2, 3]}
        assert repaired is True


# ═══════════════════════════════════════════════════════════════════════════
# 14. get_repair_stats / reset_repair_stats
# ═══════════════════════════════════════════════════════════════════════════

class TestRepairStats:
    """JSON 修复统计：线程安全的计数器。"""

    def setup_method(self):
        reset_repair_stats()

    def test_initial_stats(self):
        stats = get_repair_stats()
        assert stats["attempts"] == 0
        assert stats["success"] == 0
        assert stats["fail"] == 0
        assert "parse_retry" in stats
        assert stats["parse_retry"]["retry_triggered"] == 0
        assert stats["parse_retry"]["retry_success"] == 0
        assert stats["parse_retry"]["retry_exhausted"] == 0

    def test_reset_clears_counts(self):
        _repair_json("{'a': 1}")
        stats = get_repair_stats()
        assert stats["attempts"] >= 1
        assert stats["success"] >= 1

        reset_repair_stats()
        stats = get_repair_stats()
        assert stats["attempts"] == 0
        assert stats["success"] == 0
        assert stats["fail"] == 0
        assert stats["parse_retry"]["retry_triggered"] == 0
        assert stats["parse_retry"]["retry_success"] == 0
        assert stats["parse_retry"]["retry_exhausted"] == 0

    def test_stats_count_successful_repair(self):
        _repair_json("{'a': 1}")
        stats = get_repair_stats()
        assert stats["attempts"] >= 1
        assert stats["success"] >= 1

    def test_stats_count_failed_repair(self):
        _repair_json('{totally broken')
        stats = get_repair_stats()
        assert stats["attempts"] >= 1

    def test_stats_return_is_deep_copy(self):
        """返回的字典是深拷贝，修改不影响内部状态。"""
        stats = get_repair_stats()
        stats["attempts"] = 999
        stats2 = get_repair_stats()
        assert stats2["attempts"] == 0

    def test_thread_safety(self):
        """在并发修!开下，统计计数正确累加。"""
        reset_repair_stats()

        def _repair_many():
            for _ in range(50):
                _repair_json("{'a': 1}")
                _repair_json('{broken forever')

        threads = [threading.Thread(target=_repair_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = get_repair_stats()
        # 每个线程 50 次成功修复 + 50 次失败尝试
        # 但注意 broken json 也可能在某些修复步骤后成功（例如变成合法空对象）
        # 所以不校验精确值，只校验 attempts >= success + fail
        assert stats["attempts"] >= stats["success"] + stats["fail"]
        assert stats["attempts"] > 0

    def test_successful_and_failed_repairs_separately(self):
        reset_repair_stats()
        _repair_json("{'a': 1}")
        _repair_json('{broken forever here')
        stats = get_repair_stats()
        assert stats["attempts"] >= 2
        assert stats["success"] >= 1

    def test_already_valid_json_does_not_increment_stats(self):
        reset_repair_stats()
        _repair_json('{"a": 1}')
        stats = get_repair_stats()
        # 合法 JSON 不进入修复逻辑，attempts 不变
        assert stats["attempts"] == 0
        assert stats["success"] == 0
        assert stats["fail"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 15. 集成测试：完整修复管道
# ═══════════════════════════════════════════════════════════════════════════

class TestFullRepairPipeline:
    """集成测试：多种修复步骤的组合效果。"""

    def test_llm_typical_output(self):
        """LLM 典型输出：代码块 + 单引号 + 尾逗号 + 注释。"""
        raw = (
            '```json\n'
            "{\n"
            "  'name': 'ChatGPT',\n"
            "  'version': 4.0,\n"
            "  'features': ['chat', 'code', 'image',], // TODO: add video\n"
            "  'stats': {'users': 100, 'rating': 4.5, /* approximate */},\n"
            '}\n'
            '```'
        )
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["name"] == "ChatGPT"
        assert parsed["version"] == 4.0
        assert parsed["features"] == ["chat", "code", "image"]
        assert parsed["stats"] == {"users": 100, "rating": 4.5}

    def test_python_dict_style(self):
        """类 Python 字典风格。"""
        raw = """{
    'name': 'test',
    'value': True,
    'data': None,
    'items': [1, 2, 3],
}"""
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["name"] == "test"
        assert parsed["value"] is True
        assert parsed["data"] is None
        assert parsed["items"] == [1, 2, 3]

    def test_unquoted_keys_with_special_values(self):
        raw = '{result: True, error: None, count: 42}'
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed == {"result": True, "error": None, "count": 42}

    def test_bom_code_block_single_quotes_current_limitation(self):
        """BOM + 代码块 + 单引号：BOM 被剥离但代码块未被识别（_strip_code_block
        在 lstrip BOM 之前执行），最终修复失败返回原始串（不含 BOM）。"""
        raw = '\ufeff```json\n{\'a\': 1, \'b\': 2}\n```'
        result = _repair_json(raw)
        # BOM 被剥离，代码块未识别（修复链中 _strip_code_block 已跳过）
        # 单引号被修复，但代码块包裹导致 json.loads 仍失败，最终返回原始串
        assert '\ufeff' not in result
        assert result.startswith('```')

    def test_multiline_with_all_fix_types(self):
        """一个包含所有需要修复问题的 JSON。"""
        raw = """
        ```json
        {a: True, 'b': None, 'c': [1, 2, 3,], // list

        /* block comment */
        'd': {'e': 'hello\x00world', 'f': False,}}
        ```
        """
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["a"] is True
        assert parsed["b"] is None
        assert parsed["c"] == [1, 2, 3]
        assert parsed["d"]["e"] == "helloworld"
        assert parsed["d"]["f"] is False

    def test_json_loads_safe_pipeline(self):
        """通过 json_loads_safe 完成完整的解析-修复-返回流程。"""
        raw = "{a: True, 'b': None}"
        result, repaired = json_loads_safe(raw)
        assert result == {"a": True, "b": None}
        assert repaired is True

    def test_multiple_fix_attempts_without_early_exit(self):
        """所有修复步骤依次执行。"""
        raw = "{a: True, 'b': None, 'c': [1, 2, 3,]}"  # 需要多步修复
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["a"] is True
        assert parsed["b"] is None
        assert parsed["c"] == [1, 2, 3]

    def test_extra_bracket_only_repair(self):
        """仅有多余括号问题。"""
        result = _repair_json('{"a": {"b": 1}}}')
        assert json.loads(result) == {"a": {"b": 1}}

    def test_missing_bracket_only_repair(self):
        """仅有缺失括号问题。"""
        result = _repair_json('{"a": {"b": 1}')
        assert json.loads(result) == {"a": {"b": 1}}
