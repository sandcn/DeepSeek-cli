"""上下文消息选择测试 — 覆盖 src/core/context_selector.py。

验证消息文本提取、限制检测、工具调用组保护与压缩候选筛选。
"""

import pytest

from src.core.context_selector import (
    adjust_keep_for_tool_groups,
    calc_excess_chars_values,
    calc_usage_percent_values,
    exceeds_limit_values,
    find_tool_groups,
    message_to_text,
    select_candidates,
    select_for_compression,
    should_auto_force_values,
)


# ── message_to_text ───────────────────────────────────────

def test_message_to_text_plain():
    assert message_to_text({"role": "user", "content": "hello"}) == "hello"


def test_message_to_text_no_content():
    assert message_to_text({"role": "user"}) == ""


def test_message_to_text_assistant_tool_calls():
    msg = {
        "role": "assistant",
        "content": "done",
        "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path": "x"}'}}],
    }
    text = message_to_text(msg)
    assert "调用工具 read_file" in text
    assert "done" in text


def test_message_to_text_tool_role():
    msg = {"role": "tool", "tool_call_id": "call_1234567890", "content": "result"}
    assert "工具结果" in message_to_text(msg)
    assert "result" in message_to_text(msg)


def test_message_to_text_tool_role_multimodal():
    """tool 角色多模态 content（list blocks）→ 归一化文本，不输出 base64。"""
    msg = {
        "role": "tool", "tool_call_id": "call_abc",
        "content": [
            {"type": "text", "text": "图片是"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ],
    }
    text = message_to_text(msg)
    assert "工具结果" in text
    assert "图片是" in text
    assert "base64" not in text
    assert "data:image" not in text


def test_message_to_text_assistant_content_list():
    """assistant 带 tool_calls 且 content 为 list → 归一化后 join 不崩溃。"""
    msg = {
        "role": "assistant",
        "content": [{"type": "text", "text": "done"}],
        "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path": "x"}'}}],
    }
    text = message_to_text(msg)
    assert "调用工具 read_file" in text
    assert "done" in text


# ── find_tool_groups ──────────────────────────────────────

def test_find_tool_groups():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "r1"},
        {"role": "tool", "content": "r2"},
        {"role": "user", "content": "next"},
    ]
    assert find_tool_groups(messages) == [(0, 2)]


def test_find_tool_groups_none():
    messages = [{"role": "user", "content": "x"}]
    assert find_tool_groups(messages) == []


# ── adjust_keep_for_tool_groups ───────────────────────────

def test_adjust_keep_expands_to_cover_group():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "r1"},
        {"role": "user", "content": "after"},
    ]
    # keep_recent=2 时 boundary=1 落在工具组 (0,1) 内，应扩大到 3
    assert adjust_keep_for_tool_groups(messages, keep_recent=2) == 3


def test_adjust_keep_no_split_no_change():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "r1"},
        {"role": "user", "content": "after"},
    ]
    # keep_recent=1 时 boundary=2 不在工具组内，保持不变
    assert adjust_keep_for_tool_groups(messages, keep_recent=1) == 1


# ── select_candidates ─────────────────────────────────────

def test_select_candidates():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "m1"},
        {"role": "assistant", "content": "m2"},
        {"role": "user", "content": "m3"},
    ]
    assert select_candidates(messages, keep_recent=1) == [1, 2]


def test_select_candidates_skips_pinned():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "m1", "pinned": True},
        {"role": "user", "content": "m2"},
        {"role": "user", "content": "m3"},
    ]
    assert select_candidates(messages, keep_recent=1) == [2]


# ── select_for_compression ────────────────────────────────

def test_select_for_compression_force():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "m1"},
        {"role": "assistant", "content": "m2"},
        {"role": "user", "content": "m3"},
    ]
    assert select_for_compression(messages, keep_recent=1, force=True) == [1, 2]


def test_select_for_compression_no_excess():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "short"},
    ]
    # total 未超限，返回空
    assert select_for_compression(messages, keep_recent=0,
                                  total_chars_val=5, total_tokens_val=3) == []


# ── 限制检测 ──────────────────────────────────────────────

def test_exceeds_limit_chars():
    assert exceeds_limit_values(100, 0, max_context_chars=80, max_context_tokens=0) is True
    assert exceeds_limit_values(50, 0, max_context_chars=80, max_context_tokens=0) is False


def test_exceeds_limit_tokens():
    assert exceeds_limit_values(0, 100, max_context_chars=0, max_context_tokens=80) is True


def test_should_auto_force_values():
    assert should_auto_force_values(1000, 0, auto_force_threshold=500,
                                    max_context_tokens=0) is True
    assert should_auto_force_values(100, 0, auto_force_threshold=500,
                                    max_context_tokens=0) is False


def test_calc_excess_chars_values():
    assert calc_excess_chars_values(200, 0, max_context_chars=100, max_context_tokens=0) == 100
    assert calc_excess_chars_values(50, 0, max_context_chars=100, max_context_tokens=0) == 0


def test_calc_usage_percent_values():
    assert calc_usage_percent_values(50, max_context_chars=200) == 25.0
    assert calc_usage_percent_values(50, max_context_chars=0) == 0.0
