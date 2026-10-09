"""上下文压缩摘要的**流式**调用测试。

用户需求：「压缩上下文的 agent 调用 API 时要用流式的」——上下文压缩摘要
（``CompactionEngine`` → ``summarize_region`` → 默认 ``summarize_fn``）不再
走一次性非流式 POST，而走与主对话一致的 SSE 流式管线：

  - ``api.model_async.call_model_summarize_async``：流式（``stream=True``）+
    ``silent`` + 内部 label（``STREAM_LABEL_SUMMARIZE``），无工具、无 display；
  - ``api.model_async.call_model_summarize_sync``：同步包装（持久化事件循环），
    供压缩引擎在线程中同步调用；
  - ``core.adapters.model.SyncModelBridge.summarize``：核心层默认摘要函数，
    委托流式摘要调用。

所有网络层均被 mock，不产生真实请求。
"""

from __future__ import annotations

import asyncio

import pytest

import src.api.model_async as ma
from src.api.model_async import STREAM_LABEL_SUMMARIZE
from src.core.adapters.model import SyncModelBridge


class _FakeAdapter:
    """无网络假适配器（流式管线依赖面）。"""

    provider_name = "fake"
    _protocol = ""
    _base_url = "http://fake"

    def prepare_messages(self, messages, model):
        return list(messages)

    def is_reasoner_model(self, model):
        return False

    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None):
        return {"messages": messages, "model": model, "tools": tools, "stream": stream}


def _chunks_with(content="", reasoning="", output=66):
    chunks = []
    if reasoning:
        chunks.append({"choices": [{"delta": {"reasoning_content": reasoning}}]})
    if content:
        chunks.append({"choices": [{"delta": {"content": content}}]})
    chunks.append({
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": output,
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100},
    })
    return chunks


@pytest.fixture
def _stream_env(monkeypatch):
    """替换流式管线依赖（适配器 + 客户端函数），返回记录容器。"""
    import src.api._adapter_manager as am
    import src.api.stream.pipeline_async as pa

    record: dict = {"requests": [], "chunks": _chunks_with(content="## summary body")}

    async def fake_stream(**kwargs):
        record["requests"].append(kwargs)

        async def _gen():
            for chunk in record["chunks"]:
                yield chunk
        return _gen()

    monkeypatch.setattr(am, "get_adapter", lambda model: _FakeAdapter())
    monkeypatch.setattr(ma, "get_adapter", lambda model: _FakeAdapter())
    monkeypatch.setattr(pa, "chat_completions_async", fake_stream)
    monkeypatch.setattr(pa, "chat_completions_async_anthropic", fake_stream)
    return record


@pytest.fixture(autouse=True)
def _clean_stats():
    from src.core.stats import reset_stats, reset_token_speed
    reset_stats()
    reset_token_speed(keep_total=False)
    yield
    reset_stats()
    reset_token_speed(keep_total=False)


# ═══════════════════════════════════════════════════════════
# 1. call_model_summarize_async：流式 + silent + 内部 label
# ═══════════════════════════════════════════════════════════

def test_summarize_async_uses_sse_streaming(_stream_env, monkeypatch):
    captured: dict = {}
    real_stream_call = ma.stream_call_async

    async def spy_stream_call(messages, model, is_reasoner, tools=None,
                              display=None, label=None, silent=False):
        captured.update(model=model, is_reasoner=is_reasoner, tools=tools,
                        display=display, label=label, silent=silent)
        return await real_stream_call(messages, model, is_reasoner, tools,
                                      display=display, label=label, silent=silent)

    monkeypatch.setattr(ma, "stream_call_async", spy_stream_call)

    reasoning, content, usage, tool_calls = asyncio.run(ma.call_model_summarize_async(
        [{"role": "user", "content": "hi"}], model="deepseek-flash",
    ))

    assert content == "## summary body"
    assert reasoning == ""
    assert tool_calls == []
    assert usage["output"] == 66
    assert captured["silent"] is True
    assert captured["label"] == STREAM_LABEL_SUMMARIZE == "summarize"
    assert captured["display"] is None
    assert captured["tools"] is None
    assert captured["is_reasoner"] is False
    # 走 SSE 流式（stream=True），而非一次性非流式 POST
    assert _stream_env["requests"] and _stream_env["requests"][0]["stream"] is True


def test_summarize_async_passes_retry_overrides(_stream_env, monkeypatch):
    captured: dict = {}

    async def spy_call_model_async(messages, **kwargs):
        captured.update(kwargs)
        return ("", "ok summary body", {"input": 1, "output": 2}, [])

    monkeypatch.setattr(ma, "call_model_async", spy_call_model_async)
    asyncio.run(ma.call_model_summarize_async(
        [{"role": "user", "content": "hi"}], model="m",
        override_max_retries=3, fixed_delay_sec=1.5,
    ))
    assert captured["override_max_retries"] == 3
    assert captured["fixed_delay_sec"] == 1.5
    assert captured["silent"] is True
    assert captured["label"] == STREAM_LABEL_SUMMARIZE


def test_summarize_async_reasoning_only_fallback(_stream_env):
    """推理模型仅返回 reasoning（无 content）时以 reasoning 作为摘要文本。"""
    _stream_env["chunks"] = _chunks_with(reasoning="只有推理的摘要正文内容", output=20)
    reasoning, content, _usage, _tool_calls = asyncio.run(
        ma.call_model_summarize_async([{"role": "user", "content": "hi"}], model="m")
    )
    assert reasoning == "只有推理的摘要正文内容"
    assert content == reasoning


def test_summarize_async_records_generation_rate(_stream_env):
    """摘要（流式）结束后登记真实平均速率，且总 tok 不重复累加。"""
    from src.core.stats import get_total_tokens
    from src.core.stats._token_speed import _token_speed

    asyncio.run(ma.call_model_summarize_async(
        [{"role": "user", "content": "hi"}], model="m",
    ))
    assert get_total_tokens() == 66
    batch = _token_speed._last_batch
    assert batch is not None
    _end_ts, size, seconds, total_after = batch
    assert size == 66 and seconds > 0 and total_after == 66


# ═══════════════════════════════════════════════════════════
# 2. call_model_summarize_sync：同步包装
# ═══════════════════════════════════════════════════════════

def test_summarize_sync_wrapper(monkeypatch):
    async def fake_async(messages, model=None):
        assert messages == [{"role": "user", "content": "hi"}]
        assert model == "m"
        return ("r", "c", {"input": 0, "output": 0}, [])

    monkeypatch.setattr(ma, "call_model_summarize_async", fake_async)
    assert ma.call_model_summarize_sync(
        [{"role": "user", "content": "hi"}], "m",
    ) == ("r", "c", {"input": 0, "output": 0}, [])


# ═══════════════════════════════════════════════════════════
# 3. SyncModelBridge：核心层默认摘要函数委托流式调用
# ═══════════════════════════════════════════════════════════

def test_bridge_summarize_delegates_to_streaming_sync(monkeypatch):
    seen: dict = {}

    def fake_sync(messages, model=None):
        seen.update(messages=messages, model=model)
        return ("r", "c", {"input": 1, "output": 2}, [])

    monkeypatch.setattr(ma, "call_model_summarize_sync", fake_sync)
    result = SyncModelBridge().summarize([{"role": "user", "content": "hi"}], model="m")
    assert result == ("r", "c", {"input": 1, "output": 2}, [])
    assert seen["model"] == "m"


def test_context_manager_default_summarize_fn_is_bridge_streaming():
    """ContextManager 默认摘要函数 = SyncModelBridge.summarize（流式摘要）。"""
    from src.core.adapters.config import MockConfigAdapter
    from src.core.context_manager import ContextManager

    cm = ContextManager([{"role": "user", "content": "hi"}], "m",
                        config_port=MockConfigAdapter({}))
    fn = cm._summarize_fn
    assert getattr(fn, "__self__", None).__class__ is SyncModelBridge
    assert fn.__func__ is SyncModelBridge.summarize
