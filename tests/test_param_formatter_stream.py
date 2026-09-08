"""流式工具参数关键值摘要测试（2026-09-08 用户需求）。

需求：subagent 面板接收参数（流式解析）期间的显示要跟 mainagent 一样
（关键参数值格式，非原始 JSON 串）。

实现固化项：
  1. ``extract_key_params_stream``：完整 JSON 与 ``extract_key_params``
     同格式输出（关键参数值，空格连接）；
  2. 截断 JSON（字符串值/多键/数字/嵌套未闭合）宽容补全后提取关键参数值
     （``_complete_partial_json`` 状态机补全引号与括号栈）；
  3. 末尾孤立反斜杠（转义序列被截断）丢弃后闭合；
  4. 非 JSON 文本回退 ``extract_key_params`` 原串截断路径；
  5. dict 输入直通 ``extract_key_params``；空输入返回空串；
  6. 超长单值截断 ≤60 字符（与 extract_key_params 已知工具行为一致）。
"""

from __future__ import annotations

import json

from src.core.param_formatter import (
    _complete_partial_json,
    extract_key_params,
    extract_key_params_stream,
)


class TestCompletePartialJson:
    """截断 JSON 宽容补全解析（_complete_partial_json）。"""

    def test_complete_json_dict(self):
        assert _complete_partial_json('{"a": 1}') == {"a": 1}

    def test_truncated_string_value(self):
        assert _complete_partial_json('{"path": "src/ma') == {"path": "src/ma"}

    def test_truncated_multiple_keys(self):
        obj = _complete_partial_json('{"query": "foo", "path": "src/ma')
        assert obj == {"query": "foo", "path": "src/ma"}

    def test_truncated_number_value(self):
        assert _complete_partial_json('{"limit": 1') == {"limit": 1}

    def test_truncated_bool_partial_fallback_none(self):
        assert _complete_partial_json('{"flag": tru') is None

    def test_truncated_nested(self):
        obj = _complete_partial_json('{"config": {"a": 1')
        assert obj == {"config": {"a": 1}}

    def test_truncated_array(self):
        obj = _complete_partial_json('{"items": [1, 2')
        assert obj == {"items": [1, 2]}

    def test_trailing_backslash_dropped(self):
        assert _complete_partial_json('{"path": "src/ma\\') == {"path": "src/ma"}

    def test_escaped_quote_inside_value(self):
        obj = _complete_partial_json('{"command": "echo \\"hi')
        assert obj == {"command": 'echo \\"hi'}

    def test_mismatched_close_returns_none(self):
        assert _complete_partial_json('{"a": 1}}') is None

    def test_empty_string(self):
        assert _complete_partial_json("") is None

    def test_non_dict_top_level_returns_none(self):
        assert _complete_partial_json('"just a string') is None


class TestExtractKeyParamsStream:
    """流式参数预览 → 关键参数值摘要（与 mainagent 工具卡同格式）。"""

    def test_complete_json_matches_extract_key_params(self):
        args = json.dumps({"path": "src/main.py", "offset": 3})
        assert extract_key_params_stream("read_file", args) == extract_key_params(
            "read_file", args
        )
        assert extract_key_params_stream("read_file", args) == "src/main.py"

    def test_truncated_string_value(self):
        assert extract_key_params_stream("read_file", '{"path": "src/ma') == "src/ma"

    def test_truncated_multi_key_known_tool(self):
        out = extract_key_params_stream("search", '{"query": "foo", "path": "src/ma')
        assert out == "foo src/ma"

    def test_truncated_bash_command(self):
        out = extract_key_params_stream("bash", '{"command": "echo hi')
        assert out == "echo hi"

    def test_long_value_truncated_60(self):
        out = extract_key_params_stream("read_file", '{"path": "' + "x" * 100)
        assert out == "x" * 57 + "..."
        assert len(out) == 60

    def test_non_json_falls_back_to_raw(self):
        out = extract_key_params_stream("unknown_tool", "hello world")
        assert out == "hello world"

    def test_non_json_long_truncated_80(self):
        out = extract_key_params_stream("unknown_tool", "a" * 200)
        assert out == "a" * 77 + "..."

    def test_empty_input(self):
        assert extract_key_params_stream("read_file", "") == ""
        assert extract_key_params_stream("read_file", None) == ""

    def test_dict_input_passthrough(self):
        assert extract_key_params_stream("read_file", {"path": "a.py"}) == "a.py"

    def test_unparseable_partial_keeps_raw(self):
        raw = '{"flag": tru'
        assert extract_key_params_stream("read_file", raw) == raw
