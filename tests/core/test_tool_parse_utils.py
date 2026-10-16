"""工具调用格式转换测试 — 覆盖 src/api/_tool_parse_utils.py 与 stream_parse.py。

验证流式/非流式工具调用解析、失败 ID 追踪、参数串拼接与解析计时器。
"""

import pytest

from src.api._tool_parse_utils import (
    convert_tool_calls_map,
    convert_tool_calls_map_with_status,
    full_args_str,
    parse_raw_tool_calls,
    parse_raw_tool_calls_with_status,
)
from src.api.stream_parse import ToolParseTracker


# ── full_args_str ─────────────────────────────────────────

def test_full_args_str_from_parts():
    tc = {"_args_parts": ["{", '"a": 1', "}"]}
    assert full_args_str(tc) == '{"a": 1}'


def test_full_args_str_from_legacy_arguments():
    tc = {"arguments": '{"b": 2}'}
    assert full_args_str(tc) == '{"b": 2}'


def test_full_args_str_empty():
    assert full_args_str({}) == ""


# ── convert_tool_calls_map ────────────────────────────────

def test_convert_tool_calls_map():
    tool_calls_map = {
        0: {"id": "call_1", "name": "read_file", "arguments": '{"path": "x"}'},
    }
    result = convert_tool_calls_map(tool_calls_map)
    assert result == [{"id": "call_1", "name": "read_file", "arguments": {"path": "x"}}]


def test_convert_tool_calls_map_sorted_by_index():
    tool_calls_map = {
        1: {"id": "b", "name": "n", "arguments": "{}"},
        0: {"id": "a", "name": "m", "arguments": "{}"},
    }
    result = convert_tool_calls_map(tool_calls_map)
    assert [r["id"] for r in result] == ["a", "b"]


def test_convert_tool_calls_map_empty_arguments():
    tool_calls_map = {0: {"id": "c", "name": "n"}}
    result = convert_tool_calls_map(tool_calls_map)
    assert result[0]["arguments"] == {}


def test_convert_tool_calls_map_invalid_json_failed_id():
    tool_calls_map = {0: {"id": "bad", "name": "n", "arguments": "{invalid"}}
    tool_calls, failed_ids = convert_tool_calls_map_with_status(tool_calls_map)
    assert tool_calls == []
    assert failed_ids == ["bad"]


# ── parse_raw_tool_calls ──────────────────────────────────

def test_parse_raw_tool_calls():
    raw = [{"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "x"}'}}]
    tool_calls, total_args, names = parse_raw_tool_calls(raw)
    assert tool_calls == [{"id": "c1", "name": "read_file", "arguments": {"path": "x"}}]
    assert total_args == '{"path": "x"}'
    assert names == ["read_file"]


def test_parse_raw_tool_calls_empty_arguments():
    raw = [{"id": "c1", "function": {"name": "n", "arguments": ""}}]
    tool_calls, total_args, names = parse_raw_tool_calls(raw)
    assert tool_calls == [{"id": "c1", "name": "n", "arguments": {}}]
    assert total_args == ""
    assert names == ["n"]


def test_parse_raw_tool_calls_invalid_json():
    raw = [{"id": "bad", "function": {"name": "n", "arguments": "{oops"}}]
    tool_calls, _total, names, failed_ids = parse_raw_tool_calls_with_status(raw)
    assert tool_calls == []
    assert names == []
    assert failed_ids == ["bad"]


# ── ToolParseTracker ──────────────────────────────────────

def test_tracker_initial_state():
    tracker = ToolParseTracker({})
    assert tracker.started is False
    assert tracker.elapsed == 0.0
    assert tracker.interrupted is False


def test_tracker_elapsed_zero_before_start():
    tracker = ToolParseTracker({})
    assert tracker.elapsed == 0.0


async def test_tracker_finalize_without_start():
    tracker = ToolParseTracker({})
    await tracker.finalize()
    # 未启动时不会崩溃，只是记录 0
    assert tracker.started is False
