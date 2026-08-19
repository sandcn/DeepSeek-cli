"""适配器层工具函数测试 — 覆盖 src/api/adapters/_utils.py 与 base.py。

验证 V4 模型检测、reasoning_content 修复、tool 响应配对修复，
以及 OpenAI 兼容的 usage 提取与流式 chunk 解析。
"""

import pytest

from src.api.adapters._utils import (
    ensure_reasoning_content,
    ensure_tool_response_complete,
    is_deepseek_v4_model,
)
from src.api.adapters.base import _extract_cache_usage, _parse_openai_stream_chunk


# ── is_deepseek_v4_model ──────────────────────────────────

def test_is_deepseek_v4_model_true():
    assert is_deepseek_v4_model("deepseek-v4-0324") is True
    assert is_deepseek_v4_model("deepseek-v4") is True


def test_is_deepseek_v4_model_false():
    assert is_deepseek_v4_model("deepseek-v3") is False
    assert is_deepseek_v4_model("gpt-4") is False


# ── ensure_reasoning_content ──────────────────────────────

def test_reasoning_content_missing_key_added():
    messages = [{"role": "assistant", "content": "hi"}]
    result = ensure_reasoning_content(messages)
    assert result[0]["reasoning_content"] == ""


def test_reasoning_content_none_fixed():
    messages = [{"role": "assistant", "content": "hi", "reasoning_content": None}]
    result = ensure_reasoning_content(messages)
    assert result[0]["reasoning_content"] == ""


def test_reasoning_content_non_str_converted():
    messages = [{"role": "assistant", "reasoning_content": 123}]
    result = ensure_reasoning_content(messages)
    assert result[0]["reasoning_content"] == "123"


def test_reasoning_content_str_preserved():
    messages = [{"role": "assistant", "reasoning_content": "keep"}]
    result = ensure_reasoning_content(messages)
    assert result[0]["reasoning_content"] == "keep"


def test_reasoning_content_skips_non_assistant():
    messages = [{"role": "user", "content": "hi"}]
    result = ensure_reasoning_content(messages)
    assert "reasoning_content" not in result[0]


# ── ensure_tool_response_complete ─────────────────────────

def test_tool_response_complete_adds_placeholder():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
    ]
    result = ensure_tool_response_complete(messages)
    assert len(result) == 2
    assert result[1]["role"] == "tool"
    assert result[1]["tool_call_id"] == "call_1"


def test_tool_response_complete_already_paired():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    result = ensure_tool_response_complete(messages)
    assert len(result) == 2


def test_tool_response_complete_does_not_mutate_original():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
    ]
    ensure_tool_response_complete(messages)
    assert len(messages) == 1  # 原列表未被修改


def test_tool_response_complete_empty_id_skipped():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": ""}]},
    ]
    result = ensure_tool_response_complete(messages)
    assert len(result) == 1  # 空 id 不补发


# ── _extract_cache_usage ──────────────────────────────────

def test_extract_cache_usage_deepseek_format():
    assert _extract_cache_usage(
        {"prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 5}) == (10, 5)


def test_extract_cache_usage_openai_format():
    assert _extract_cache_usage(
        {"prompt_tokens": 30, "prompt_tokens_details": {"cached_tokens": 10}}) == (10, 20)


def test_extract_cache_usage_none():
    assert _extract_cache_usage({}) == (0, 0)
    assert _extract_cache_usage(None) == (0, 0)


# ── _parse_openai_stream_chunk ────────────────────────────

def test_parse_stream_chunk_content():
    chunk = {"choices": [{"delta": {"content": "hi"}}]}
    result = _parse_openai_stream_chunk(chunk)
    assert result["content"] == "hi"
    assert result["reasoning_content"] == ""
    assert result["tool_calls"] == []


def test_parse_stream_chunk_reasoning():
    chunk = {"choices": [{"delta": {"reasoning_content": "think"}}]}
    result = _parse_openai_stream_chunk(chunk)
    assert result["reasoning_content"] == "think"


def test_parse_stream_chunk_tool_calls():
    chunk = {"choices": [{"delta": {"tool_calls": [{"id": "c1"}]}}]}
    result = _parse_openai_stream_chunk(chunk)
    assert result["tool_calls"] == [{"id": "c1"}]


def test_parse_stream_chunk_usage():
    chunk = {
        "choices": [{"delta": {"content": ""}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    result = _parse_openai_stream_chunk(chunk)
    assert result["usage"]["input"] == 10
    assert result["usage"]["output"] == 5


def test_parse_stream_chunk_no_choices():
    result = _parse_openai_stream_chunk({})
    assert result["content"] == ""
    assert result["usage"] is None
