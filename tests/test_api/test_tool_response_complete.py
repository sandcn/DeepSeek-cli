"""ensure_tool_response_complete / 适配器 prepare_messages 契约测试。

覆盖：
1. 完整消息历史不被修改
2. 缺失 tool 响应的历史补发占位 tool 消息
3. 空 tool_call_id 不补发
4. 已响应的 id 不重复补发
5. 多 assistant 消息场景（补发紧跟所属 assistant 之后）
6. BaseLLMAdapter.prepare_messages 默认实现（API 边界最终防御层）
7. DeepSeekAdapter.prepare_messages 集成（tool 配对 + reasoning_content 双修复）
"""
from __future__ import annotations

from src.api.adapters._utils import ensure_tool_response_complete
from src.api.adapters.base import BaseLLMAdapter
from src.api.adapters.deepseek import DeepSeekAdapter
from src.api.adapters.openai_compat import OpenAICompatAdapter


def _assistant_msg(tool_ids):
    """构造带 tool_calls 的 assistant 消息。"""
    return {
        "role": "assistant",
        "content": None,
        "reasoning_content": "",
        "tool_calls": [
            {
                "id": tid,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
            for tid in tool_ids
        ],
    }


# ── ensure_tool_response_complete 纯函数 ────────────────

def test_complete_history_unchanged():
    messages = [
        {"role": "user", "content": "hi"},
        _assistant_msg(["call_1"]),
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "done", "reasoning_content": ""},
    ]
    result = ensure_tool_response_complete([dict(m) for m in messages])
    assert result == messages


def test_missing_tool_response_backfilled():
    messages = [
        {"role": "user", "content": "hi"},
        _assistant_msg(["call_1", "call_2"]),
        # 只有 call_2 的响应，call_1 缺失
        {"role": "tool", "tool_call_id": "call_2", "content": "ok"},
    ]
    result = ensure_tool_response_complete([dict(m) for m in messages])
    roles = [m["role"] for m in result]
    assert roles == ["user", "assistant", "tool", "tool"]
    # 补发的占位 tool 消息紧跟 assistant 消息之后（call_1 缺失 → 补发）
    assert result[2]["tool_call_id"] == "call_1"
    assert "丢弃" in result[2]["content"]
    assert result[3]["tool_call_id"] == "call_2"


def test_empty_id_not_backfilled():
    messages = [
        _assistant_msg([""]),
    ]
    result = ensure_tool_response_complete([dict(m) for m in messages])
    assert result == messages


def test_already_responded_not_duplicated():
    messages = [
        _assistant_msg(["call_1"]),
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    result = ensure_tool_response_complete([dict(m) for m in messages])
    assert result == messages


def test_multiple_assistant_messages_backfill_position():
    messages = [
        _assistant_msg(["call_a"]),
        {"role": "tool", "tool_call_id": "call_a", "content": "ok"},
        {"role": "assistant", "content": "mid", "reasoning_content": ""},
        _assistant_msg(["call_b"]),  # call_b 无响应
    ]
    result = ensure_tool_response_complete([dict(m) for m in messages])
    roles = [m["role"] for m in result]
    assert roles == ["assistant", "tool", "assistant", "assistant", "tool"]
    # 补发紧跟第二个 assistant 之后
    assert result[3]["role"] == "assistant"
    assert result[4]["tool_call_id"] == "call_b"


def test_input_list_not_mutated():
    messages = [
        _assistant_msg(["call_1"]),
    ]
    original_len = len(messages)
    ensure_tool_response_complete(messages)
    # 返回新列表，不就地修改原列表
    assert len(messages) == original_len


# ── BaseLLMAdapter.prepare_messages（API 边界最终防御层） ──

class _ConcreteAdapter(BaseLLMAdapter):
    provider_name = "test"

    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None):
        return {"model": model, "messages": messages}

    def parse_response(self, response):
        return response


def test_base_prepare_messages_backfills():
    adapter = _ConcreteAdapter()
    messages = [
        _assistant_msg(["call_1"]),
    ]
    result = adapter.prepare_messages(messages, "test-model")
    assert len(result) == 2
    assert result[1]["role"] == "tool"
    assert result[1]["tool_call_id"] == "call_1"


# ── DeepSeekAdapter.prepare_messages 集成 ───────────────

def test_deepseek_prepare_messages_backfills_and_reasoning():
    adapter = DeepSeekAdapter()
    messages = [
        _assistant_msg(["call_1"]),  # 缺失响应 + 已有 reasoning_content
    ]
    result = adapter.prepare_messages(messages, "deepseek-v4-pro")
    assert len(result) == 2
    assert result[0]["reasoning_content"] == ""
    assert result[1]["role"] == "tool"
    assert result[1]["tool_call_id"] == "call_1"


def test_deepseek_prepare_messages_adds_reasoning_content():
    adapter = DeepSeekAdapter()
    messages = [
        {"role": "assistant", "content": "hi"},  # 无 reasoning_content key
    ]
    result = adapter.prepare_messages(messages, "deepseek-v4-pro")
    assert result[0]["reasoning_content"] == ""


def test_openai_compat_prepare_messages_backfills():
    adapter = OpenAICompatAdapter()
    messages = [
        _assistant_msg(["call_1"]),
    ]
    result = adapter.prepare_messages(messages, "test-model")
    assert len(result) == 2
    assert result[1]["role"] == "tool"
    assert result[1]["tool_call_id"] == "call_1"
