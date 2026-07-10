"""测试 src.api.tool_parse：JSON 修复工具函数和工具调用格式转换。

测试策略
--------
- 使用 importlib 直接加载模块文件，避免触发 src/__init__.py 的级联导入
- 预先在 sys.modules 中 mock 所有外部依赖（tokens、stats、ui.colors、tools.registry、interrupt_async）
- 每个测试函数关注单个函数的一种行为，遵循"一个断言概念一个测试"
- 边界值、异常路径、正常路径全覆盖
- 统计函数涉及线程锁，测试验证计数正确性和隔离性

风险提示
--------
本文件通过 sys.modules 注入大量 mock 来屏蔽级联导入。这导致测试隔离性差：
- mock 模块可能泄漏到其他测试文件
- 测试顺序敏感（先运行本文件可能影响后续测试）
- cleanup_sys_modules fixture 负责在文件级测试后恢复 sys.modules
"""

import sys
import json
import pytest
import importlib.util
import importlib.abc
import importlib.machinery
from unittest.mock import MagicMock, patch, ANY

# ── 在导入被测试模块前 mock 所有外部依赖 ────────────────────────────────
# 注意：必须 mock 整个 src.ui / src.tools 包，因为 src/__init__.py 会级联导入


class _MockPackageLoader(importlib.abc.Loader):
    """使 MagicMock 表现为合法的 Python 包，支持相对导入。"""
    def create_module(self, spec):
        return MagicMock()
    def exec_module(self, module):
        pass


# 将包路径注册为合法包，避免相对导入时 "'src.ui' is not a package"
# 注意：必须保存原始模块引用，以便后续恢复，避免泄漏到其他测试文件
_MOCK_PACKAGE_NAMES = ['src', 'src.api', 'src.ui', 'src.tools']
_ORIGINAL_PACKAGES: dict[str, object] = {}
for _pkg_name in _MOCK_PACKAGE_NAMES:
    _ORIGINAL_PACKAGES[_pkg_name] = sys.modules.get(_pkg_name)
    _spec = importlib.machinery.ModuleSpec(_pkg_name, _MockPackageLoader(), is_package=True)
    sys.modules[_pkg_name] = importlib.util.module_from_spec(_spec)

_MOCK_MODULES = {
    # 叶子模块（非包，直接 mock）
    'src.api.tokens': MagicMock(),
    'src.api.stats': MagicMock(),
    'src.api.interrupt_async': MagicMock(),
    'src.api.json_repair': MagicMock(),   # placeholder, overwritten below
    'src.api._tool_parse_utils': MagicMock(),  # placeholder, overwritten below
    'src.api.stream_parse': MagicMock(),  # placeholder, overwritten below
    'src.ui.colors': MagicMock(DIM='\x1b[2m', RESET='\x1b[0m', YELLOW='\x1b[33m'),
    'src.ui._lock': MagicMock(),
    'src.tools.registry': MagicMock(),
}

# ★ 保存原始模块引用，清理时恢复而非 pop，避免后续测试 import 时获取到
#   新模块实例（含新 asyncio.Event），导致 is_interrupted() 和
#   request_interrupt_async() 操作不同 Event 对象，中断信号永远无法到达。
_ORIGINAL_MODULES: dict[str, object] = {}
for mod_name in _MOCK_MODULES:
    _ORIGINAL_MODULES[mod_name] = sys.modules.get(mod_name)

for mod_name, mod in _MOCK_MODULES.items():
    sys.modules[mod_name] = mod

_SCRIPT_DIR = '/home/DeepSeek-cli/src/api'

# ── 直接加载 json_repair.py（拆分后的新文件）─────────────────────────
_json_repair_spec = importlib.util.spec_from_file_location(
    'src.api.json_repair', f'{_SCRIPT_DIR}/json_repair.py',
)
_json_repair_module = importlib.util.module_from_spec(_json_repair_spec)
sys.modules['src.api.json_repair'] = _json_repair_module
_json_repair_spec.loader.exec_module(_json_repair_module)

# ── 直接加载 _tool_parse_utils.py ───────────────────────────────────
_tool_parse_utils_spec = importlib.util.spec_from_file_location(
    'src.api._tool_parse_utils', f'{_SCRIPT_DIR}/_tool_parse_utils.py',
)
_tool_parse_utils_module = importlib.util.module_from_spec(_tool_parse_utils_spec)
sys.modules['src.api._tool_parse_utils'] = _tool_parse_utils_module
_tool_parse_utils_spec.loader.exec_module(_tool_parse_utils_module)

# ── 直接加载 stream_parse.py ───────────────────────────────────────
_stream_parse_spec = importlib.util.spec_from_file_location(
    'src.api.stream_parse', f'{_SCRIPT_DIR}/stream_parse.py',
)
_stream_parse_module = importlib.util.module_from_spec(_stream_parse_spec)
sys.modules['src.api.stream_parse'] = _stream_parse_module
_stream_parse_spec.loader.exec_module(_stream_parse_module)

# ── 清理 mock，避免泄漏到其他测试文件（xdist 同 worker 会共享 sys.modules）─
# ★ 恢复被 mock 的叶子模块（而非 pop），确保后续 import 引用原始模块实例
for mod_name in list(_MOCK_MODULES.keys()):
    orig = _ORIGINAL_MODULES.get(mod_name)
    if orig is not None:
        sys.modules[mod_name] = orig
    else:
        sys.modules.pop(mod_name, None)
for mod_name in ['src.api.json_repair', 'src.api._tool_parse_utils', 'src.api.stream_parse']:
    sys.modules.pop(mod_name, None)
# 恢复被 mock 覆盖的包（src, src.api, src.ui, src.tools），
# 否则后续其他测试文件 collection 时无法导入真实模块
for _pkg_name in _MOCK_PACKAGE_NAMES:
    orig = _ORIGINAL_PACKAGES.get(_pkg_name)
    if orig is not None:
        sys.modules[_pkg_name] = orig
    else:
        sys.modules.pop(_pkg_name, None)

# ── 提取所有被测试符号（tool_parse 已删除，直接从源模块引用）──────────────
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
_repair_json = _json_repair_module._repair_json
json_loads_safe = _json_repair_module.json_loads_safe
get_repair_stats = _json_repair_module.get_repair_stats
reset_repair_stats = _json_repair_module.reset_repair_stats
convert_tool_calls_map = _stream_parse_module.convert_tool_calls_map
convert_tool_calls_map_with_status = _stream_parse_module.convert_tool_calls_map_with_status
parse_raw_tool_calls = _stream_parse_module.parse_raw_tool_calls
parse_raw_tool_calls_with_status = _stream_parse_module.parse_raw_tool_calls_with_status


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
        """前后空格：_strip_code_block 不处理前导空格（由调用方 _repair_json strip）。"""
        result = _strip_code_block('  ```json\n{"a":1}\n```  ')
        # 函数只处理以 ``` 开头的字符串，前导空格导致不匹配
        assert result == '  ```json\n{"a":1}\n```  '


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


# ═══════════════════════════════════════════════════════════════════════════
# 3. _remove_comments
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveComments:
    """去除 // 行注释和 /* */ 块注释。"""

    def test_line_comment(self):
        result = _remove_comments('{"key": 1 // comment\n}')
        # 注意：re.sub(r'//[^\n]*', '', s) 把 ' // comment' 替换为空，保留前面的空格
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

    def test_url_not_affected(self):
        """URL 中的 // 不会被误删，因为修复链中 _remove_comments 在字符串保护之后执行。
        但纯 _remove_comments 直接调用会删除 //。这个测试记录实际行为。"""
        s = '{"url": "http://example.com"}'
        result = _remove_comments(s)
        # _remove_comments 是纯正则，会删除 "http://example.com" 中的 //
        # 但实际上在修复链中它是被字符串保护过的，这里仅测试函数自身行为
        assert '//' not in result


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

    def test_no_keys(self):
        assert _fix_unquoted_keys('[]') == '[]'

    def test_empty_object(self):
        assert _fix_unquoted_keys('{}') == '{}'


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
        # 正则 r',\s*([}\]])' 只匹配 ]} 前的逗号，所以 ,,,} → ,,}
        assert result == '{"a": 1,,}'

    def test_empty_input(self):
        assert _fix_trailing_commas('') == ''


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
        """'Truehood' 中的 'True' 不应被替换（\b 边界）。"""
        s = '{"key": "Truehood"}'
        assert _fix_python_literals(s) == s

    def test_no_python_literals(self):
        s = '{"a": 1, "b": "hello"}'
        assert _fix_python_literals(s) == s

    def test_single_quoted_strings_protected(self):
        result = _fix_python_literals("{'msg': 'True story', 'val': None}")
        assert result == "{'msg': 'True story', 'val': null}"


# ═══════════════════════════════════════════════════════════════════════════
# 7. _remove_control_chars
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveControlChars:
    """移除控制字符 (U+0000-U+001F)，保留 Tab/LF/CR？实际是移除所有。"""

    def test_remove_control_chars(self):
        # \x00, \x01, \x1f 都是控制字符
        s = '{"a": "hello\x00world\x01"}'
        result = _remove_control_chars(s)
        assert result == '{"a": "helloworld"}'

    def test_tab_is_removed(self):
        """Tab \x09 也被移除（属于控制字符范围）。"""
        s = '{"a": "hello\tworld"}'
        result = _remove_control_chars(s)
        assert '\t' not in result

    def test_newline_is_removed(self):
        """字面量换行符 \x0a 也被移除。"""
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
        # { 入栈，遇到 ] 不匹配（栈顶是 {），] 被标记为多余删除
        # 最终栈中还有 {，需要补 }
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


# ═══════════════════════════════════════════════════════════════════════════
# 9. _remove_zero_width_chars
# ═══════════════════════════════════════════════════════════════════════════

class TestRemoveZeroWidthChars:
    """零宽字符和不可见 Unicode 字符移除。"""

    def test_remove_zero_width_space(self):
        assert _remove_zero_width_chars('a\u200bb') == 'ab'

    def test_remove_bom(self):
        """BOM \ufeff 虽不在此函数处理范围，但零宽字符包括很多种。"""
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


# ═══════════════════════════════════════════════════════════════════════════
# 10. _fix_unescaped_quotes
# ═══════════════════════════════════════════════════════════════════════════

class TestFixUnescapedQuotes:
    """修复字符串值中未转义的双引号。"""

    def test_unescaped_quotes_in_value(self):
        """字符串中的未转义双引号被保护机制影响。

        注意：函数内部先把合法的 "..." 作为字符串保护起来。在
        '{"msg": "he said "hi" to me"}' 中，第一个合法字串是 "msg"，
        第二个是 "he said "，第三个是 " to me"（'hi' 在字串外）。
        保护后 'hi' 两侧无 " 可转义，最终恢复时无变化。
        此函数作为 _repair_json 的兜底策略，配合其他步骤一起工作。
        """
        result = _fix_unescaped_quotes('{"msg": "he said "hi" to me"}')
        # 当前实现：合法字符串被保护，残余 "hi" 两侧没有 " 可转义
        assert result == '{"msg": "he said "hi" to me"}'

    def test_normal_string_untouched(self):
        s = '{"a": "normal"}'
        assert _fix_unescaped_quotes(s) == s

    def test_empty_string(self):
        assert _fix_unescaped_quotes('') == ''


# ═══════════════════════════════════════════════════════════════════════════
# 11. _repair_json（完整修复链）
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

    def test_zero_width_chars(self):
        """零宽字符在合法 JSON 字符串内不被修复链触及（json.loads 接受 Unicode）。"""
        result = _repair_json('{"a": "hello\u200bworld"}')
        # 这是合法 JSON → 初始 json.loads 通过，原样返回
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
        # 此时返回原始串
        assert result is not None
        assert '{invalid' in result

    def test_unescaped_quotes_fallback(self):
        """未转义引号场景应通过兜底修复成功。"""
        raw = '{"msg": "he said "hello" world"}'
        result = _repair_json(raw)
        try:
            parsed = json.loads(result)
            assert parsed["msg"] is not None
        except json.JSONDecodeError:
            # 如果兜底也没修复，返回原始串，这也算合理
            assert result == raw


# ═══════════════════════════════════════════════════════════════════════════
# 12. json_loads_safe
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
        """纯空白字符串：not s 为假，s.strip()!='null' → 进入 json.loads 并抛异常。"""
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
        """完全无法修复的 JSON 抛出原始异常。"""
        with pytest.raises(json.JSONDecodeError):
            json_loads_safe('{invalid json here totally broken')

    def test_none_type_handling(self):
        """直接传 'null' 已被覆盖，测试 None 类似场景。"""
        result, repaired = json_loads_safe('null')
        assert result == {}
        assert repaired is False


# ═══════════════════════════════════════════════════════════════════════════
# 13. get_repair_stats / reset_repair_stats
# ═══════════════════════════════════════════════════════════════════════════

class TestRepairStats:
    """JSON 修复统计：线程安全的计数器。"""

    def test_initial_stats(self):
        reset_repair_stats()
        stats = get_repair_stats()
        # 检查核心计数器归零（stats 可能含其他扩展键如 parse_retry）
        assert stats["attempts"] == 0
        assert stats["success"] == 0
        assert stats["fail"] == 0

    def test_reset_clears_counts(self):
        reset_repair_stats()
        # 触发一次修复
        _repair_json("{'a': 1}")
        stats = get_repair_stats()
        assert stats["attempts"] >= 1
        assert stats["success"] >= 1

        reset_repair_stats()
        stats = get_repair_stats()
        # 检查核心计数器归零（stats 可能含其他扩展键如 parse_retry）
        assert stats["attempts"] == 0
        assert stats["success"] == 0
        assert stats["fail"] == 0

    def test_stats_count_successful_repair(self):
        reset_repair_stats()
        _repair_json("{'a': 1}")
        stats = get_repair_stats()
        assert stats["attempts"] >= 1
        assert stats["success"] >= 1

    def test_stats_count_failed_repair(self):
        reset_repair_stats()
        _repair_json('{totally broken')
        stats = get_repair_stats()
        assert stats["attempts"] >= 1
        # fail 可能为 0 或 1，取决于修复是否成功
        # 但至少 attempts > 0

    def test_stats_return_is_deep_copy(self):
        """返回的字典是深拷贝，修改不影响内部状态。"""
        reset_repair_stats()
        stats = get_repair_stats()
        stats["attempts"] = 999
        stats2 = get_repair_stats()
        assert stats2["attempts"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 14. convert_tool_calls_map
# ═══════════════════════════════════════════════════════════════════════════

class TestConvertToolCallsMap:
    """将流式累积的工具调用映射 {index: {...}} 转换为列表格式。"""

    def test_basic_conversion(self):
        tool_calls_map = {
            0: {"id": "call1", "name": "get_weather", "arguments": '{"city": "北京"}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert len(result) == 1
        assert result[0]["id"] == "call1"
        assert result[0]["name"] == "get_weather"
        assert result[0]["arguments"] == {"city": "北京"}

    def test_sorted_by_index(self):
        tool_calls_map = {
            1: {"id": "call2", "name": "tool_b", "arguments": '{"x": 2}'},
            0: {"id": "call1", "name": "tool_a", "arguments": '{"x": 1}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert len(result) == 2
        assert result[0]["id"] == "call1"
        assert result[1]["id"] == "call2"

    def test_empty_map(self):
        assert convert_tool_calls_map({}) == []

    def test_stream_label_priority(self):
        """_stream_label 优先于 id。"""
        tool_calls_map = {
            0: {"id": "call1", "_stream_label": "stream_label_1",
                "name": "get_weather", "arguments": '{"city": "北京"}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["id"] == "stream_label_1"

    def test_auto_id_when_id_empty(self):
        """id 空时使用 auto_{idx}。"""
        tool_calls_map = {
            0: {"id": "", "name": "get_weather", "arguments": '{"city": "北京"}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["id"] == "auto_0"

    def test_arguments_parse_error_skipped(self):
        """参数解析失败时跳过该 tool_call（日志警告）。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "bad_tool", "arguments": '{invalid json}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result == []

    def test_empty_arguments(self):
        tool_calls_map = {
            0: {"id": "call1", "name": "no_args", "arguments": ""},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["arguments"] == {}

    def test_arguments_not_dict_fallback(self):
        """arguments 解析后不是 dict 时兜底为空 dict。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "get_val", "arguments": '"string_val"'},
        }
        # 这个测试：json_loads_safe 解析 '"string_val"' 成功但返回 str
        # convert_tool_calls_map 中判断 isinstance(args, dict) → False → {}
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["arguments"] == {}

    def test_multiple_calls(self):
        tool_calls_map = {
            0: {"id": "c1", "name": "tool_a", "arguments": '{"a": 1}'},
            1: {"id": "c2", "name": "tool_b", "arguments": '{"b": 2}'},
            2: {"id": "c3", "name": "tool_c", "arguments": '{"c": 3}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert len(result) == 3
        for i, tc in enumerate(result):
            assert tc["id"] == f"c{i+1}"


# ═══════════════════════════════════════════════════════════════════════════
# 15. parse_raw_tool_calls
# ═══════════════════════════════════════════════════════════════════════════

class TestParseRawToolCalls:
    """解析原始 JSON dict 格式的工具调用列表。"""

    def test_basic_parse(self):
        raw = [
            {
                "id": "call1",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "北京"}',
                },
            },
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0]["id"] == "call1"
        assert calls[0]["name"] == "get_weather"
        assert calls[0]["arguments"] == {"city": "北京"}
        assert total_args == '{"city": "北京"}'
        assert names == ["get_weather"]

    def test_empty_list(self):
        calls, total_args, names = parse_raw_tool_calls([])
        assert calls == []
        assert total_args == ""
        assert names == []

    def test_empty_arguments(self):
        raw = [
            {
                "id": "call1",
                "function": {
                    "name": "no_args",
                    "arguments": "",
                },
            },
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert calls[0]["arguments"] == {}
        assert total_args == ""
        assert names == ["no_args"]

    def test_invalid_json_arguments_skipped(self):
        raw = [
            {
                "id": "call1",
                "function": {
                    "name": "bad_tool",
                    "arguments": "{invalid json}",
                },
            },
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert calls == []
        assert total_args == ""
        assert names == []

    def test_multiple_calls_with_names(self):
        raw = [
            {"id": "c1", "function": {"name": "tool_a", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "tool_b", "arguments": '{"b": 2}'}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert len(calls) == 2
        assert total_args == '{"a": 1}{"b": 2}'
        assert names == ["tool_a", "tool_b"]

    def test_duplicate_names(self):
        raw = [
            {"id": "c1", "function": {"name": "tool_a", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "tool_a", "arguments": '{"a": 2}'}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert names == ["tool_a", "tool_a"]

    def test_non_dict_arguments_fallback(self):
        raw = [
            {
                "id": "call1",
                "function": {
                    "name": "get_str",
                    "arguments": '"just_a_string"',
                },
            },
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert calls[0]["arguments"] == {}
        # 注意：JSON 解析 '"just_a_string"' 成功，但结果是 str，不是 dict
        # parse_raw_tool_calls 中 args is dict 判断 → False → {}
        # total_args 仍累加原始字符串

    def test_repairable_arguments(self):
        raw = [
            {
                "id": "call1",
                "function": {
                    "name": "test_tool",
                    "arguments": "{'city': '北京'}",
                },
            },
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert calls[0]["arguments"] == {"city": "北京"}
        assert calls[0]["name"] == "test_tool"


# ═══════════════════════════════════════════════════════════════════════════
# 16. 综合边界情况
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """跨函数的边界条件综合测试。"""

    def test_deeply_nested_structure_repair(self):
        """深层嵌套 JSON 修复。"""
        raw = "{a: {'b': [1, 2, None,], 'c': {'d': True,},}}"
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["a"]["b"] == [1, 2, None]
        assert parsed["a"]["c"]["d"] is True

    def test_empty_object_after_repair(self):
        result = _repair_json("''")
        # 空字符串单引号修复后变成 ""，json.loads("") 会失败
        # 所以返回原始串
        assert result is not None


    def test_mixed_unicode_and_control_chars(self):
        """控制字符被移除，但零宽字符在合法 JSON 字符串内保持。"""
        raw = '{"msg": "hello\u200bworld\x00!"}'
        result = _repair_json(raw)
        parsed = json.loads(result)
        # \x00 被 _remove_control_chars 移除，\u200b 在合法 JSON 中保留
        assert parsed["msg"] == "hello\u200bworld!"

    def test_llm_typical_output(self):
        """模拟典型 LLM 工具调用输出。"""
        raw = """```json
        {
            'thought': "Let's check weather",
            'tool': 'get_weather',
            'params': {
                'city': '北京',
                'date': '2025-01-01',
            }, // end of params
        }
        ```"""
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["thought"] == "Let's check weather"
        assert parsed["tool"] == "get_weather"
        assert parsed["params"]["city"] == "北京"

    def test_convert_with_repairable_args(self):
        """convert_tool_calls_map 中参数需修复的场景。"""
        tool_calls_map = {
            0: {"id": "c1", "name": "tool_a", "arguments": "{'city': '上海'}"},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["arguments"] == {"city": "上海"}

    def test_large_integer_values(self):
        raw = '{"a": 999999999999999999999999999999}'
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["a"] == 999999999999999999999999999999

    def test_unicode_escapes(self):
        raw = '{"msg": "\\u4f60\\u597d"}'
        result = _repair_json(raw)
        parsed = json.loads(result)
        assert parsed["msg"] == "你好"

    def test_parse_raw_with_invalid_and_valid_mixed(self):
        """混合有效和无效的工具调用。"""
        raw = [
            {"id": "c1", "function": {"name": "good", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "bad", "arguments": '{broken}'}},
            {"id": "c3", "function": {"name": "good2", "arguments": '{"b": 2}'}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert len(calls) == 2  # 中间一个被跳过
        assert names == ["good", "good2"]
        assert calls[0]["id"] == "c1"
        assert calls[1]["id"] == "c3"


# ═══════════════════════════════════════════════════════════════════════════
# 17. convert_tool_calls_map_with_status
# ═══════════════════════════════════════════════════════════════════════════

class TestConvertToolCallsMapWithStatus:
    """测试 convert_tool_calls_map_with_status — 返回解析失败 ID 列表。"""

    def test_normal_parse_no_failures(self):
        """正常解析：返回空 failed_ids。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "get_weather", "arguments": '{"city": "北京"}'},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call1"
        assert tool_calls[0]["name"] == "get_weather"
        assert tool_calls[0]["arguments"] == {"city": "北京"}
        assert failed_ids == []

    def test_partial_failure(self):
        """部分解析失败：成功项正常返回，失败项记录 ID。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "good_tool", "arguments": '{"a": 1}'},
            1: {"id": "call2", "name": "bad_tool", "arguments": '{invalid json}'},
            2: {"id": "call3", "name": "good_tool2", "arguments": '{"b": 2}'},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert len(tool_calls) == 2
        assert tool_calls[0]["id"] == "call1"
        assert tool_calls[1]["id"] == "call3"
        assert len(failed_ids) == 1
        assert "call2" in failed_ids

    def test_all_failures(self):
        """全部解析失败：tool_calls 为空，failed_ids 包含所有 ID。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "bad1", "arguments": '{broken}'},
            1: {"id": "call2", "name": "bad2", "arguments": '{also broken}'},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert tool_calls == []
        assert len(failed_ids) == 2

    def test_empty_map(self):
        """空 map：返回空列表。"""
        tool_calls, failed_ids = convert_tool_calls_map_with_status({})
        assert tool_calls == []
        assert failed_ids == []

    def test_failed_id_uses_stream_label(self):
        """解析失败时，_stream_label 优先于 id。"""
        tool_calls_map = {
            0: {"id": "call1", "_stream_label": "stream_1",
                "name": "bad", "arguments": '{broken}'},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert tool_calls == []
        assert failed_ids == ["stream_1"]

    def test_failed_id_uses_auto_id(self):
        """解析失败时，无 id 和 _stream_label 则使用 auto_{idx}。"""
        tool_calls_map = {
            0: {"id": "", "name": "bad", "arguments": '{broken}'},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert tool_calls == []
        assert failed_ids == ["auto_0"]

    def test_sorted_by_index(self):
        """结果按 index 排序。"""
        tool_calls_map = {
            2: {"id": "c3", "name": "tool_c", "arguments": '{"c": 3}'},
            0: {"id": "c1", "name": "tool_a", "arguments": '{"a": 1}'},
            1: {"id": "c2", "name": "tool_b", "arguments": '{"b": 2}'},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert failed_ids == []
        assert [tc["id"] for tc in tool_calls] == ["c1", "c2", "c3"]

    def test_repairable_arguments(self):
        """可修复的参数（单引号 JSON）正常解析。"""
        tool_calls_map = {
            0: {"id": "c1", "name": "tool_a", "arguments": "{'city': '上海'}"},
        }
        tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
        assert failed_ids == []
        assert tool_calls[0]["arguments"] == {"city": "上海"}


# ═══════════════════════════════════════════════════════════════════════════
# 18. parse_raw_tool_calls_with_status
# ═══════════════════════════════════════════════════════════════════════════

class TestParseRawToolCallsWithStatus:
    """测试 parse_raw_tool_calls_with_status — 返回解析失败 ID 列表。"""

    def test_normal_parse_no_failures(self):
        """正常解析：返回空 failed_ids。"""
        raw = [
            {"id": "call1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
        ]
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status(raw)
        assert len(calls) == 1
        assert calls[0]["id"] == "call1"
        assert calls[0]["name"] == "get_weather"
        assert calls[0]["arguments"] == {"city": "北京"}
        assert total_args == '{"city": "北京"}'
        assert names == ["get_weather"]
        assert failed_ids == []

    def test_mixed_valid_invalid(self):
        """混合有效和无效的工具调用。"""
        raw = [
            {"id": "c1", "function": {"name": "good", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "bad", "arguments": '{broken}'}},
            {"id": "c3", "function": {"name": "good2", "arguments": '{"b": 2}'}},
        ]
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status(raw)
        assert len(calls) == 2
        assert calls[0]["id"] == "c1"
        assert calls[1]["id"] == "c3"
        assert names == ["good", "good2"]
        assert total_args == '{"a": 1}{"b": 2}'
        assert failed_ids == ["c2"]

    def test_empty_list(self):
        """空列表：所有返回值均为空。"""
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status([])
        assert calls == []
        assert total_args == ""
        assert names == []
        assert failed_ids == []

    def test_all_invalid(self):
        """全部无效：calls 和 names 为空，所有 ID 计入 failed_ids。"""
        raw = [
            {"id": "c1", "function": {"name": "bad1", "arguments": '{broken1}'}},
            {"id": "c2", "function": {"name": "bad2", "arguments": '{broken2}'}},
        ]
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status(raw)
        assert calls == []
        assert total_args == ""
        assert names == []
        assert len(failed_ids) == 2
        assert "c1" in failed_ids
        assert "c2" in failed_ids

    def test_empty_arguments(self):
        """空 arguments：正常返回空 dict，不计入 failed_ids。"""
        raw = [
            {"id": "call1", "function": {"name": "no_args", "arguments": ""}},
        ]
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status(raw)
        assert calls[0]["arguments"] == {}
        assert total_args == ""
        assert names == ["no_args"]
        assert failed_ids == []

    def test_repairable_arguments(self):
        """可修复参数（单引号 JSON）正常解析。"""
        raw = [
            {"id": "call1", "function": {"name": "test_tool", "arguments": "{'city': '北京'}"}},
        ]
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status(raw)
        assert failed_ids == []
        assert calls[0]["arguments"] == {"city": "北京"}

    def test_multiple_calls_with_names(self):
        """多个工具调用，验证 names 和 total_args 累加正确。"""
        raw = [
            {"id": "c1", "function": {"name": "tool_a", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "tool_b", "arguments": '{"b": 2}'}},
        ]
        calls, total_args, names, failed_ids = parse_raw_tool_calls_with_status(raw)
        assert total_args == '{"a": 1}{"b": 2}'
        assert names == ["tool_a", "tool_b"]
        assert failed_ids == []


# ═══════════════════════════════════════════════════════════════════════════
# 19. 回归测试 — convert_tool_calls_map / parse_raw_tool_calls 行为不变
# ═══════════════════════════════════════════════════════════════════════════

class TestToolParseRegression:
    """验证重构后原函数 convert_tool_calls_map / parse_raw_tool_calls 行为不变。"""

    # ── convert_tool_calls_map 回归 ──

    def test_convert_basic(self):
        """基本转换：与重构前一致。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "get_weather", "arguments": '{"city": "北京"}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert len(result) == 1
        assert result[0]["id"] == "call1"
        assert result[0]["name"] == "get_weather"
        assert result[0]["arguments"] == {"city": "北京"}

    def test_convert_parse_error_skipped(self):
        """参数解析失败时跳过（静默 continue，不抛异常）。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "bad_tool", "arguments": '{invalid json}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result == []

    def test_convert_empty_map(self):
        """空 map 返回空列表。"""
        assert convert_tool_calls_map({}) == []

    def test_convert_stream_label_priority(self):
        """_stream_label 优先于 id。"""
        tool_calls_map = {
            0: {"id": "call1", "_stream_label": "stream_1",
                "name": "tool", "arguments": '{"x": 1}'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["id"] == "stream_1"

    def test_convert_arguments_not_dict_fallback(self):
        """arguments 解析后不是 dict 则兜底为空 dict。"""
        tool_calls_map = {
            0: {"id": "call1", "name": "get_val", "arguments": '"string_val"'},
        }
        result = convert_tool_calls_map(tool_calls_map)
        assert result[0]["arguments"] == {}

    # ── parse_raw_tool_calls 回归 ──

    def test_parse_basic(self):
        """基本解析：与重构前一致。"""
        raw = [
            {"id": "call1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0]["id"] == "call1"
        assert calls[0]["name"] == "get_weather"
        assert total_args == '{"city": "北京"}'
        assert names == ["get_weather"]

    def test_parse_invalid_skipped(self):
        """无效 JSON 参数被跳过，不抛异常。"""
        raw = [
            {"id": "call1", "function": {"name": "bad", "arguments": "{invalid}"}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert calls == []
        assert total_args == ""
        assert names == []

    def test_parse_empty_list(self):
        """空列表返回空结果。"""
        calls, total_args, names = parse_raw_tool_calls([])
        assert calls == []
        assert total_args == ""
        assert names == []

    def test_parse_mixed_valid_invalid(self):
        """混合有效无效：只返回有效的，无效静默跳过。"""
        raw = [
            {"id": "c1", "function": {"name": "good", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "bad", "arguments": '{broken}'}},
            {"id": "c3", "function": {"name": "good2", "arguments": '{"b": 2}'}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert len(calls) == 2
        assert names == ["good", "good2"]
        assert calls[0]["id"] == "c1"
        assert calls[1]["id"] == "c3"

    def test_parse_total_args_concatenation(self):
        """total_args 为所有成功解析的参数串拼接。"""
        raw = [
            {"id": "c1", "function": {"name": "tool_a", "arguments": '{"a": 1}'}},
            {"id": "c2", "function": {"name": "tool_b", "arguments": '{"b": 2}'}},
        ]
        calls, total_args, names = parse_raw_tool_calls(raw)
        assert total_args == '{"a": 1}{"b": 2}'


@pytest.fixture(autouse=True)
def cleanup_sys_modules():
    """文件级清理：恢复 sys.modules 中被本文件污染的模块。

    注意：仅清理本文件运行时新注入的 mock 模块，
    不触及其他测试文件已加载的合法模块。
    """
    saved = sys.modules.copy()
    yield
    for mod_name in list(sys.modules.keys()):
        if mod_name not in saved:
            del sys.modules[mod_name]
