"""空响应（无正文/无推理/无工具调用）→ 可重试瞬时错误 回归测试。

问题现象：模型偶发返回**完全空**的响应（服务端 200 但无任何有效 delta），
``AsyncStreamPipeline._build_result`` 落占位文本「(无内容)」并结束本轮——
用户在聊天区/轨迹视图只能看到「#N 回答 / (无内容)」，必须手动输入「继续」
才能继续对话（回答内容永久丢失）。

修复：
  1. ``errors.EmptyResponseError`` —— 空响应归类为**可重试**瞬时异常
     （``retryable=True``，携带短 ``retry_after`` 避免指数退避 30s 起步）；
  2. ``pipeline_async.stream_call_async`` 在流式处理结束后若**完全无产出**
     则抛出该异常 → 交由 ``retry_api_call_async`` 原样重发（此时尚未渲染
     任何内容，重启流幂等安全）；
  3. ``client_async`` 的 SSE 解析识别 ``{"error": ...}`` 错误帧（HTTP 200
     内的服务端错误），抛语义化可重试异常，不再被当作「无产出的空流」。
"""

from __future__ import annotations

import asyncio

import pytest

import src.api.client_async as ca
import src.api.model_async as ma
import src.api.stream.pipeline_async as pa
from src.api.errors import EmptyResponseError, ServerError, format_user_error, is_retryable
from src.api.stream.pipeline_async import (
    AsyncStreamPipeline, is_empty_stream_result, stream_call_async,
)


# ═══════════════════════════════════════════════════════════
# 0. 异常语义
# ═══════════════════════════════════════════════════════════

def test_empty_response_error_is_retryable():
    err = EmptyResponseError()
    assert isinstance(err, ServerError.__mro__[1])  # APIError 子类
    assert err.retryable is True
    assert is_retryable(err) is True


def test_empty_response_error_retry_after_is_short():
    """短 retry_after → 重试等待走 compute_retry_delay 的 retry_after 分支（非 30s 起步）。"""
    from src.api.errors import compute_retry_delay

    err = EmptyResponseError()
    assert err.retry_after == 2.0
    assert compute_retry_delay(1, 30.0, err) == pytest.approx(2.0)


def test_empty_response_error_str_has_no_http_prefix():
    """__str__ 不带 "API error 200" 前缀（HTTP 层面其实是成功的）。"""
    text = str(EmptyResponseError())
    assert "200" not in text
    assert "空响应" in text
    # 用户可见文案沿用「API 调用出错」关键词（SubAgent 网络错误识别依赖）
    assert "API 调用出错" in format_user_error(EmptyResponseError())


# ═══════════════════════════════════════════════════════════
# 1. pipeline_async：空流判定与抛出
# ═══════════════════════════════════════════════════════════

class _FakeAdapter:
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


def _patch_stream_env(monkeypatch, responder):
    """替换 stream_call_async 的环境依赖（适配器/网络/事件/统计）。"""
    import src.api._adapter_manager as am

    monkeypatch.setattr(am, "get_adapter", lambda model: _FakeAdapter())
    monkeypatch.setattr(pa, "chat_completions_async", responder)
    monkeypatch.setattr(pa, "_notify_stream_started", lambda: None)
    monkeypatch.setattr(pa, "_notify_stream_ended", lambda: None)
    monkeypatch.setattr(pa, "_notify_stream_progress", lambda: None)
    monkeypatch.setattr(pa, "accumulate_usage", lambda u: None)
    monkeypatch.setattr(pa, "set_stream_speed", lambda s: None)
    # 事件发布：避免污染全局 EventBus（各模块独立绑定，逐一替换）
    import src.api.stream.context as pc
    import src.api.stream.handlers._base as phb

    monkeypatch.setattr(pa, "publish_event", lambda *a, **k: None)
    monkeypatch.setattr(pc, "publish_event", lambda *a, **k: None)
    monkeypatch.setattr(phb, "publish_event", lambda *a, **k: None)
    # 中断标志：默认未中断
    async def _not_interrupted():
        return False

    monkeypatch.setattr(pa, "is_interrupted_async", _not_interrupted)


def _responder(chunks, *, calls=None):
    """构造 chat_completions_async 替身：每次调用返回一个新生成器。"""
    async def _call(**kwargs):
        if calls is not None:
            calls.append(kwargs)

        async def _gen():
            for ch in chunks:
                yield ch

        return _gen()

    return _call


def _run_stream(monkeypatch, chunks, responder=None):
    _patch_stream_env(monkeypatch, responder or _responder(chunks))
    return asyncio.run(stream_call_async(
        [{"role": "user", "content": "hi"}], "m", False, None,
        display=None, label="assistant", silent=True,
    ))


def test_empty_stream_raises_empty_response_error(monkeypatch):
    """完全空流（无任何 chunk）→ 抛 EmptyResponseError（而非返回「(无内容)」）。"""
    with pytest.raises(EmptyResponseError):
        _run_stream(monkeypatch, [])


def test_choices_without_delta_raises(monkeypatch):
    """只有 usage / 无 delta 的帧同样视为空响应。"""
    with pytest.raises(EmptyResponseError):
        _run_stream(monkeypatch, [
            {"choices": []},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 0}},
        ])


def test_content_stream_returns_normally(monkeypatch):
    """正常回答流不抛异常，返回正文。"""
    result = _run_stream(monkeypatch, [
        {"choices": [{"delta": {"content": "你好"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])
    assert result[1] == "你好"


def test_reasoning_only_stream_returns_normally(monkeypatch):
    """仅推理（无正文）也是有效产出，不抛异常。"""
    result = _run_stream(monkeypatch, [
        {"choices": [{"delta": {"reasoning_content": "思考"}}]},
    ])
    assert result[0] == "思考"


def test_tool_calls_stream_returns_normally(monkeypatch):
    """工具调用流（无正文）是有效产出，不抛异常。"""
    result = _run_stream(monkeypatch, [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "ls", "arguments": '{"path": "."}'}},
        ]}}]},
    ])
    assert result[3] and result[3][0]["name"] == "ls"


def test_interrupted_empty_stream_does_not_raise(monkeypatch):
    """中断（ESC）语义下空流不视为异常：返回「(已中断)」。"""
    _patch_stream_env(monkeypatch, _responder([]))

    async def _interrupted():
        return True

    monkeypatch.setattr(pa, "is_interrupted_async", _interrupted)
    result = asyncio.run(stream_call_async(
        [{"role": "user", "content": "hi"}], "m", False, None,
        display=None, label="assistant", silent=True,
    ))
    assert "(已中断" in result[1]


def test_is_empty_stream_result_predicate():
    """判定谓词：无产出 → True；有产出 / 中断 → False。"""
    from src.api.stream.context import StreamContext

    ctx = StreamContext("m", None, "assistant", True)
    assert is_empty_stream_result(ctx) is True
    ctx.content_full = "x"
    assert is_empty_stream_result(ctx) is False

    ctx2 = StreamContext("m", None, "assistant", True)
    ctx2.esc_interrupted = True
    assert is_empty_stream_result(ctx2) is False


# ═══════════════════════════════════════════════════════════
# 2. 重试层：空响应被自动重发并恢复
# ═══════════════════════════════════════════════════════════

def test_retry_layer_retries_empty_response(monkeypatch):
    """retry_api_call_async 对 EmptyResponseError 重发；成功即返回正文。"""
    import src.api._retry as R

    async def _no_wait(_timeout):
        return False

    monkeypatch.setattr(R, "wait_for_interrupt_async", _no_wait)

    calls = {"n": 0}

    async def _api():
        calls["n"] += 1
        if calls["n"] < 3:
            raise EmptyResponseError()
        return ("r", "c", {"input": 0, "output": 0}, [])

    result = asyncio.run(R.retry_api_call_async(
        _api, silent=True, override_max_retries=5,
    ))
    assert calls["n"] == 3
    assert result == ("r", "c", {"input": 0, "output": 0}, [])


def test_retry_layer_exhausted_returns_readable_error(monkeypatch):
    """重试用尽 → 返回可读错误文本（不再静默占位「(无内容)」）。"""
    import src.api._retry as R

    async def _no_wait(_timeout):
        return False

    monkeypatch.setattr(R, "wait_for_interrupt_async", _no_wait)

    async def _api():
        raise EmptyResponseError()

    result = asyncio.run(R.retry_api_call_async(
        _api, silent=True, override_max_retries=2,
    ))
    assert result[0] == ""
    assert "空响应" in result[1]
    assert result[1] != "(无内容)"


def test_call_model_async_recovers_from_empty_first_stream(monkeypatch):
    """端到端：首个流为空 → 自动重发 → 用户拿到真实回答（而非「(无内容)」）。"""
    import src.api._retry as R

    async def _no_wait(_timeout):
        return False

    monkeypatch.setattr(R, "wait_for_interrupt_async", _no_wait)
    monkeypatch.setattr(ma, "get_adapter", lambda model: _FakeAdapter())
    monkeypatch.setattr(ma, "_optimize_messages_for_upload", lambda messages: None)

    streams = [
        [],  # 第一次：空响应
        [{"choices": [{"delta": {"content": "真实回答"}}]}],  # 第二次：正常
    ]

    async def _responder(**kwargs):
        chunks = streams.pop(0)

        async def _gen():
            for ch in chunks:
                yield ch

        return _gen()

    _patch_stream_env(monkeypatch, _responder)

    result = asyncio.run(ma.call_model_async(
        [{"role": "user", "content": "hi"}], model="m", display=None, label="assistant",
    ))
    assert result[1] == "真实回答"


# ═══════════════════════════════════════════════════════════
# 3. client_async：SSE 错误帧不再被静默忽略
# ═══════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, lines, status_code=200):
        self.status_code = status_code
        self._lines = lines
        self.headers = {}
        self.text = ""

    async def aread(self):
        return b""

    async def aiter_raw(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def stream(self, *args, **kwargs):
        return _FakeStreamCtx(self._resp)


def _drive_sse(monkeypatch, lines):
    async def _get_client():
        return _FakeClient(_FakeResponse(lines))

    monkeypatch.setattr(ca, "get_async_client", _get_client)

    async def _collect():
        out = []
        async for chunk in ca._stream_iter_async("http://x", {}, {"messages": []}):
            out.append(chunk)
        return out

    return asyncio.run(_collect())


def test_sse_error_frame_raises_server_error(monkeypatch):
    """HTTP 200 内的 {"error": {...}} 帧 → 抛可重试 ServerError（信息不丢失）。"""
    with pytest.raises(ServerError) as ei:
        _drive_sse(monkeypatch, [
            b'data: {"error": {"message": "upstream busy"}}\n',
            b"data: [DONE]\n",
        ])
    assert "upstream busy" in str(ei.value)
    assert is_retryable(ei.value) is True


def test_sse_normal_chunks_pass_through(monkeypatch):
    """正常帧照常产出；[DONE] 结束迭代。"""
    out = _drive_sse(monkeypatch, [
        b'data: {"choices": [{"delta": {"content": "a"}}]}\n',
        b'data: {"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]}\n',
        b"data: [DONE]\n",
    ])
    assert len(out) == 2
    assert out[0]["choices"][0]["delta"]["content"] == "a"


def test_sse_null_error_is_not_treated_as_error(monkeypatch):
    """``"error": null`` / 空 dict 不算错误帧（兼容部分提供方）。"""
    out = _drive_sse(monkeypatch, [
        b'data: {"error": null, "choices": [{"delta": {"content": "a"}}]}\n',
    ])
    assert len(out) == 1
