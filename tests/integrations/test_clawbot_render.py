"""ClawBot 渲染工具测试 — 覆盖 src/clawbot/render.py。

验证 ANSI 清理、回复/思考提取、工具摘要与消息分段。
"""

import pytest

from src.clawbot.render import (
    _content_to_text,
    _find_cut,
    extract_reasoning,
    extract_reply,
    extract_tool_summary,
    split_message,
    strip_ansi,
)


# ── strip_ansi ────────────────────────────────────────────

def test_strip_ansi_removes_sequences():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_strip_ansi_plain():
    assert strip_ansi("plain") == "plain"


def test_strip_ansi_none():
    assert strip_ansi(None) == ""


# ── _content_to_text ──────────────────────────────────────

def test_content_to_text_str():
    assert _content_to_text("hello") == "hello"


def test_content_to_text_multimodal_list():
    content = [{"text": "a"}, {"content": "b"}, "c"]
    assert _content_to_text(content) == "abc"


def test_content_to_text_none():
    assert _content_to_text(None) == ""


# ── extract_reply ─────────────────────────────────────────

def test_extract_reply():
    messages = [{"role": "assistant", "content": "hello"}]
    assert extract_reply(messages) == "hello"


def test_extract_reply_finds_last_non_empty():
    messages = [
        {"role": "assistant", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]
    assert extract_reply(messages) == "second"


def test_extract_reply_empty():
    assert extract_reply([]) == ""
    assert extract_reply(None) == ""


# ── extract_reasoning ─────────────────────────────────────

def test_extract_reasoning():
    messages = [{"role": "assistant", "reasoning_content": "thinking"}]
    assert extract_reasoning(messages) == "thinking"


def test_extract_reasoning_none():
    assert extract_reasoning([{"role": "assistant"}]) == ""


# ── extract_tool_summary ──────────────────────────────────

def test_extract_tool_summary():
    messages = [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "read_file", "arguments": '{"path": "x"}'}}]},
        {"role": "tool", "content": "result text"},
    ]
    summary = extract_tool_summary(messages)
    assert "read_file" in summary
    assert "result text" in summary


def test_extract_tool_summary_empty():
    assert extract_tool_summary([]) == ""


# ── _find_cut / split_message ─────────────────────────────

def test_find_cut_at_space():
    assert _find_cut("hello world", 8) == 6


def test_split_message_short():
    assert split_message("short") == ["short"]


def test_split_message_empty():
    assert split_message("") == []
    assert split_message(None) == []


def test_split_message_long():
    chunks = split_message("hello world", limit=8)
    assert chunks == ["hello", "world"]


def test_split_message_very_long():
    text = "a" * 100
    chunks = split_message(text, limit=30)
    assert len(chunks) > 1
    assert "".join(chunks) == text
