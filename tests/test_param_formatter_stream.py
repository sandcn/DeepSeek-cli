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
        """字符串值内的转义引号不得提前终止字符串，补全后按 JSON 语义解码。

        输入 ``{"command": "echo \\"hi``（截断：值内含转义引号 ``\\"`` 且字符串
        未闭合）——状态机须把 ``\\"`` 识别为值内转义（不结束字符串），补全引号
        后交 ``json.loads`` 解码 → 值 ``echo "hi``（与完整 JSON 同格式）。
        ★ 修复（错误期望）：原用例期望值为 ``echo \\"hi``（保留原始转义符）——
        与输入值雷同（复制输入所得），且与「完整 JSON 走 json.loads 解码」
        的显示语义相悖（主 agent 工具卡 / subagent 面板 / 轨迹均为解码后的
        参数值，保留反斜杠会显示多余字符）。
        """
        assert _complete_partial_json('{"command": "echo \\"hi') == {
            "command": 'echo "hi',
        }
        # 对照：完整 JSON（值内含转义引号）解码结果与截断补全一致
        assert _complete_partial_json('{"command": "echo \\"hi\\" ok"}') == {
            "command": 'echo "hi" ok',
        }

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
        assert len(out) == 80

    def test_non_json_exactly_80_unchanged(self):
        """恰好 80 字符不截断（省略号仅在超长时追加）。"""
        raw = "b" * 80
        assert extract_key_params_stream("unknown_tool", raw) == raw

    def test_non_json_81_truncated_with_ellipsis(self):
        """81 字符触发截断 → 77 + "..."（与未知工具 k=v 分支口径一致）。"""
        out = extract_key_params_stream("unknown_tool", "c" * 81)
        assert out == "c" * 77 + "..."
        assert len(out) == 80

    def test_empty_input(self):
        assert extract_key_params_stream("read_file", "") == ""
        assert extract_key_params_stream("read_file", None) == ""

    def test_dict_input_passthrough(self):
        assert extract_key_params_stream("read_file", {"path": "a.py"}) == "a.py"

    def test_unparseable_partial_keeps_raw(self):
        raw = '{"flag": tru'
        assert extract_key_params_stream("read_file", raw) == raw
