"""JSON 修复模块测试 — 覆盖 src/api/json_repair.py。

验证 LLM 返回的常见 JSON 格式问题的自动修复能力。
"""

import json

import pytest

from src.api.json_repair import (
    _fix_extra_brackets,
    _fix_missing_commas,
    _fix_python_literals,
    _fix_quotes,
    _fix_trailing_commas,
    _fix_unquoted_keys,
    _remove_comments,
    _remove_control_chars,
    _remove_zero_width_chars,
    _repair_json,
    _strip_code_block,
    get_repair_stats,
    json_loads_safe,
    reset_repair_stats,
)


# ── _strip_code_block ─────────────────────────────────────

def test_strip_code_block_json():
    assert _strip_code_block("```json\n{}\n```") == "{}"


def test_strip_code_block_plain_fence():
    assert _strip_code_block("```\n{}\n```") == "{}"


def test_strip_code_block_no_fence():
    assert _strip_code_block("{}") == "{}"


# ── _fix_quotes ───────────────────────────────────────────

def test_fix_quotes_single_to_double():
    assert _fix_quotes("{'a': 1}") == '{"a": 1}'


def test_fix_quotes_preserves_inner_double_quotes():
    assert _fix_quotes("{'a': \"x\"}") == '{"a": "x"}'


# ── _remove_comments ──────────────────────────────────────

def test_remove_line_comment():
    assert _remove_comments('{"a": 1 // note\n}') == '{"a": 1 \n}'


def test_remove_block_comment():
    assert _remove_comments('{"a": 1 /* note */}') == '{"a": 1 }'


# ── _fix_unquoted_keys ────────────────────────────────────

def test_fix_unquoted_keys():
    assert _fix_unquoted_keys("{a: 1}") == '{"a": 1}'


def test_fix_unquoted_keys_after_comma():
    assert _fix_unquoted_keys('{"a": 1, b: 2}') == '{"a": 1, "b": 2}'


# ── _fix_trailing_commas ──────────────────────────────────

def test_fix_trailing_commas():
    assert _fix_trailing_commas('{"a": 1,}') == '{"a": 1}'


# ── _remove_control_chars ─────────────────────────────────

def test_remove_control_chars():
    assert _remove_control_chars('{"a": "x\x00y"}') == '{"a": "xy"}'


# ── _fix_python_literals ──────────────────────────────────

def test_fix_python_literals():
    assert _fix_python_literals('{"a": True, "b": None, "c": False}') == \
        '{"a": true, "b": null, "c": false}'


# ── _fix_extra_brackets ───────────────────────────────────

def test_fix_extra_brackets_removes_extra_close():
    assert _fix_extra_brackets('{"a": 1}}') == '{"a": 1}'


def test_fix_extra_brackets_adds_missing_close():
    assert _fix_extra_brackets('{"a": 1') == '{"a": 1}'


# ── _remove_zero_width_chars ──────────────────────────────

def test_remove_zero_width_chars():
    assert _remove_zero_width_chars('{"a": "\u200b"}') == '{"a": ""}'


# ── _fix_missing_commas ───────────────────────────────────

def test_fix_missing_commas_double_comma():
    repaired = _fix_missing_commas('{"a": 1,, "b": 2}')
    assert json.loads(repaired) == {"a": 1, "b": 2}


# ── _repair_json ──────────────────────────────────────────

def test_repair_json_valid_passthrough():
    assert _repair_json('{"a": 1}') == '{"a": 1}'


def test_repair_json_trailing_comma():
    assert json.loads(_repair_json('{"a": 1,}')) == {"a": 1}


def test_repair_json_unquoted_keys():
    assert json.loads(_repair_json('{a: 1}')) == {"a": 1}


def test_repair_json_python_literals():
    assert json.loads(_repair_json('{"a": True}')) == {"a": True}


def test_repair_json_empty_returns_empty():
    assert _repair_json("") == ""
    assert _repair_json("   ") == "   "


def test_repair_json_code_block():
    assert json.loads(_repair_json("```json\n{\"a\": 1}\n```")) == {"a": 1}


# ── json_loads_safe ───────────────────────────────────────

def test_json_loads_safe_valid():
    assert json_loads_safe('{"a": 1}') == ({"a": 1}, False)


def test_json_loads_safe_repaired():
    result, repaired = json_loads_safe('{"a": 1,}')
    assert result == {"a": 1}
    assert repaired is True


def test_json_loads_safe_empty():
    assert json_loads_safe("") == ({}, False)
    assert json_loads_safe("null") == ({}, False)


def test_json_loads_safe_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        json_loads_safe('{invalid')


# ── 统计 ──────────────────────────────────────────────────

def test_repair_stats_reset():
    reset_repair_stats()
    stats = get_repair_stats()
    assert stats["attempts"] == 0
    assert stats["success"] == 0
    assert stats["fail"] == 0
    assert "parse_retry" in stats
