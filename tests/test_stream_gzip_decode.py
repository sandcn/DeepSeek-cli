"""流式响应 Content-Encoding（gzip/deflate）解码回归测试 —— 2026-10-13。

## 问题现象

``python chat.py`` 输入任意消息后，终端反复显示::

    ✖ API 调用失败 (第1次): 模型返回空响应（无正文、无推理、无工具调用），2.0秒后重试...
    ...（MAX_RETRIES=10 次，每次 2s）...

重试耗尽后回答区只剩「抱歉，API 调用出错: 模型返回空响应（无正文、无推理、
无工具调用）」——回答内容永久丢失（会话存档里 assistant 消息即该错误文案）。

## 根因（已实测确认）

DeepSeek 官方 ``/v1/chat/completions`` 的**流式**响应带
``Content-Encoding: gzip``（非流式路径走 ``resp.json()``，httpx 自动解码，
所以只有流式对话受害；同一 API Key / 同一模型非流式调用完全正常）。

``client_async._stream_iter_async`` 此前用 ``resp.aiter_raw()`` 读取响应体：
httpx 的 ``aiter_raw()`` 返回**未经内容编码解码的原始字节**（只有
``aiter_bytes()`` 才按 ``Content-Encoding`` 增量解码）。于是拿到的是 gzip
压缩流——里面没有以 ``data: `` 开头的 SSE 行，逐行解析把每一行都当成
「非 SSE 行」静默跳过 → 零 chunk → ``AsyncStreamPipeline`` 判定「完全无产出」
（无正文/无推理/无工具调用）→ ``EmptyResponseError`` → 重试 10 次（每次仍是
压缩字节）→ 最终返回错误文案。整个链路没有任何报错，只有重试提示。

## 修复

1. ``aiter_raw()`` → ``aiter_bytes()``：按 ``Content-Encoding`` 增量解码
   （gzip/deflate/br/zstd；无编码时与 ``aiter_raw()`` 行为等价，且解码是
   流式的——服务端逐帧 Z_SYNC_FLUSH，首 token 延迟不受影响）；
2. 本地无解码器的编码（如未装 brotli 时的 ``br``）显式抛出可操作错误，
   而不是像 httpx 那样静默透传压缩字节、再被误判成「空响应」；
3. 解码失败（压缩流损坏/截断）未产出内容时转可重试语义；
4. 「收到字节却解析不出任何 SSE data 帧」时留下可定位 WARNING。
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import zlib

import httpx
import pytest

import src.api.client_async as ca
from src.api.errors import APIError, EmptyResponseError, ServerError, is_retryable


# ═══════════════════════════════════════════════════════════
# HTTP 层替身：真实传输语义（无 _content 快路径 / 可切分响应块）
# ═══════════════════════════════════════════════════════════

class _PieceStream(httpx.AsyncByteStream):
    """按给定字节片段逐个产出的响应流（模拟网络分片）。"""

    def __init__(self, pieces):
        self._pieces = list(pieces)

    async def __aiter__(self):
        for piece in self._pieces:
            yield piece


class _Transport(httpx.AsyncBaseTransport):
    """固定返回给定字节片段 + 头部的传输层替身。"""

    def __init__(self, pieces, headers=None, status_code=200):
        self._pieces = list(pieces)
        self._headers = dict(headers or {})
        self._status_code = status_code

    async def handle_async_request(self, request):
        return httpx.Response(
            self._status_code,
            headers=self._headers,
            stream=_PieceStream(self._pieces),
        )


def _drive(monkeypatch, pieces, headers=None, status_code=200):
    """跑一遍 ``_stream_iter_async``，返回解析出的 chunk 列表。"""
    client = httpx.AsyncClient(transport=_Transport(pieces, headers, status_code))

    async def _get_client():
        return client

    monkeypatch.setattr(ca, "get_async_client", _get_client)

    async def _run():
        try:
            out = []
            async for chunk in ca._stream_iter_async("http://x", {}, {"messages": []}):
                out.append(chunk)
            return out
        finally:
            await client.aclose()

    return asyncio.run(_run())


_SSE_PLAIN = (
    b'data: {"choices": [{"delta": {"content": "a"}}]}\n'
    b'\n'
    b'data: {"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]}\n'
    b'\n'
    b"data: [DONE]\n"
)


# ═══════════════════════════════════════════════════════════
# 1. 编码解码（核心回归）
# ═══════════════════════════════════════════════════════════

def test_gzip_sse_stream_is_decoded(monkeypatch):
    """★ 核心回归：``Content-Encoding: gzip`` 的 SSE 流必须被解码后解析。

    修复前（aiter_raw）此处解析出 0 个 chunk → 上层判「空响应」→ 用户看到
    「模型返回空响应（无正文、无推理、无工具调用）」并重试至耗尽。
    """
    out = _drive(
        monkeypatch,
        [gzip.compress(_SSE_PLAIN)],
        {"content-encoding": "gzip", "content-type": "text/event-stream"},
    )
    assert [c["choices"][0]["delta"].get("content") for c in out[:2]] == ["a", "b"]


def test_gzip_stream_split_across_pieces(monkeypatch):
    """压缩流被切成任意小片（跨 gzip 块 / 跨 SSE 行）也能增量解码 + 拼接。"""
    body = gzip.compress(_SSE_PLAIN)
    pieces = [body[i:i + 7] for i in range(0, len(body), 7)]
    out = _drive(monkeypatch, pieces, {"content-encoding": "gzip"})
    assert len(out) == 2
    assert out[1]["choices"][0]["delta"]["content"] == "b"


def test_deflate_sse_stream_is_decoded(monkeypatch):
    """``Content-Encoding: deflate`` 同样解码。"""
    out = _drive(monkeypatch, [zlib.compress(_SSE_PLAIN)], {"content-encoding": "deflate"})
    assert len(out) == 2


def test_plain_sse_stream_still_works(monkeypatch):
    """无内容编码的响应行为不变（aiter_bytes 与 aiter_raw 等价路径）。"""
    out = _drive(monkeypatch, [_SSE_PLAIN])
    assert len(out) == 2
    assert out[0]["choices"][0]["delta"]["content"] == "a"


def test_identity_encoding_is_not_treated_as_unsupported(monkeypatch):
    """``identity`` 与多重编码中的已知编码不算「不支持」。"""
    out = _drive(monkeypatch, [_SSE_PLAIN], {"content-encoding": "identity"})
    assert len(out) == 2


def test_data_field_without_space_is_accepted(monkeypatch):
    """SSE 规范允许 ``data:`` 后省略空格（部分 OpenAI 兼容网关如此发送）。

    此前只认 ``data: `` → 整条流被静默丢弃 → 又一次「模型返回空响应」。
    """
    body = (
        b'data:{"choices": [{"delta": {"content": "x"}}]}\n'
        b'data:{"choices": [{"delta": {"content": "y"}, "finish_reason": "stop"}]}\n'
        b"data:[DONE]\n"
    )
    out = _drive(monkeypatch, [body])
    assert [c["choices"][0]["delta"].get("content") for c in out[:2]] == ["x", "y"]


def test_gzip_data_field_without_space_is_accepted(monkeypatch):
    """无空格 ``data:`` + gzip 压缩（两类缺陷叠加的最坏情况）。"""
    body = b'data:{"choices": [{"delta": {"content": "z"}, "finish_reason": "stop"}]}\n'
    out = _drive(monkeypatch, [gzip.compress(body)], {"content-encoding": "gzip"})
    assert out[0]["choices"][0]["delta"]["content"] == "z"


# ═══════════════════════════════════════════════════════════
# 2. 不支持 / 损坏的编码：显式报错而非静默空响应
# ═══════════════════════════════════════════════════════════

def test_unsupported_content_encoding_raises_actionable_error(monkeypatch):
    """本地无解码器的编码（br 未装 brotli）→ 501 明确报错且不可重试。

    修复前 httpx 对未知编码静默按 Identity 透传压缩字节 → 又被误判成
    「模型返回空响应」并重试 10 次，用户完全无法定位。
    """
    monkeypatch.setattr(ca, "_SUPPORTED_DECODERS",
                        {"identity": object, "gzip": object, "deflate": object})
    with pytest.raises(APIError) as ei:
        _drive(monkeypatch, [b"\x00\x01\x02"], {"content-encoding": "br"})
    assert ei.value.status_code == 501
    assert "br" in str(ei.value)
    assert is_retryable(ei.value) is False


def test_corrupted_gzip_body_becomes_retryable(monkeypatch):
    """压缩流损坏/截断且尚未产出内容 → 可重试语义（交由重试层重发）。"""
    with pytest.raises(ServerError) as ei:
        _drive(monkeypatch, [b"not-a-gzip-stream"], {"content-encoding": "gzip"})
    assert ei.value.status_code == 503
    assert is_retryable(ei.value) is True


def test_unsupported_content_encodings_helper():
    """编码识别：空/identity 忽略，未知编码去重保序，支持逗号分隔多重编码。"""
    def _resp(value):
        return httpx.Response(200, headers={"content-encoding": value})

    assert ca._unsupported_content_encodings(_resp("")) == []
    assert ca._unsupported_content_encodings(_resp("identity")) == []
    assert ca._unsupported_content_encodings(_resp("gzip, identity")) == []
    assert ca._unsupported_content_encodings(_resp("gzip, X-Bogus")) == ["x-bogus"]
    assert ca._unsupported_content_encodings(_resp("br, br")) == ["br"]


# ═══════════════════════════════════════════════════════════
# 3. 诊断：收到数据却解析不出 SSE 帧
# ═══════════════════════════════════════════════════════════

def test_non_sse_body_with_bytes_logs_warning(monkeypatch, caplog):
    """收到响应体但零 SSE 帧 → WARNING 可定位（而非完全静默）。"""
    with caplog.at_level(logging.WARNING, logger="src.api.client_async"):
        out = _drive(monkeypatch, [b"<html>gateway error</html>\n"])
    assert out == []
    assert any("未解析出任何 SSE data 帧" in r.getMessage() for r in caplog.records)


def test_truly_empty_body_has_no_warning(monkeypatch, caplog):
    """服务端真的什么都没返回（0 字节）不产生该告警（属正常空响应路径）。"""
    with caplog.at_level(logging.WARNING, logger="src.api.client_async"):
        out = _drive(monkeypatch, [])
    assert out == []
    assert not [r for r in caplog.records if "未解析出任何 SSE data 帧" in r.getMessage()]


# ═══════════════════════════════════════════════════════════
# 4. 端到端：gzip 流 → stream_call_async 返回正文（而非 EmptyResponseError）
# ═══════════════════════════════════════════════════════════

class _FakeAdapter:
    provider_name = "fake"
    _protocol = ""
    _base_url = "http://fake"

    def is_reasoner_model(self, model):
        return False

    def build_request_kwargs(self, messages, model, tools=None, stream=False,
                             stream_options=None):
        return {"messages": messages, "model": model, "tools": tools, "stream": stream,
                "stream_options": stream_options}


def _patch_pipeline_env(monkeypatch):
    """只替换显示/事件/统计副作用；HTTP 与 SSE 解析走真实代码路径。"""
    import src.api._adapter_manager as am
    import src.api.stream.context as pc
    import src.api.stream.handlers._base as phb
    import src.api.stream.pipeline_async as pa
    import src.config as cfg

    # 测试环境未配置模型档案 → BASE_URL 为空，httpx 对相对 URL 会报错；
    # 这里只给一个绝对地址（HTTP 传输层已被替换为假传输）。
    monkeypatch.setattr(cfg, "BASE_URL", "http://test.local/v1/chat/completions")
    monkeypatch.setattr(am, "get_adapter", lambda model: _FakeAdapter())
    monkeypatch.setattr(pa, "_notify_stream_started", lambda: None)
    monkeypatch.setattr(pa, "_notify_stream_ended", lambda: None)
    monkeypatch.setattr(pa, "_notify_stream_progress", lambda: None)
    monkeypatch.setattr(pa, "accumulate_usage", lambda u: None)
    monkeypatch.setattr(pa, "set_stream_speed", lambda s: None)
    monkeypatch.setattr(pa, "publish_event", lambda *a, **k: None)
    monkeypatch.setattr(pc, "publish_event", lambda *a, **k: None)
    monkeypatch.setattr(phb, "publish_event", lambda *a, **k: None)

    async def _not_interrupted():
        return False

    monkeypatch.setattr(pa, "is_interrupted_async", _not_interrupted)


def test_stream_call_async_survives_gzip_sse(monkeypatch):
    """★ 端到端回归：官方 gzip SSE 流 → 用户拿到真实回答。

    修复前：``stream_call_async`` 抛 EmptyResponseError（回答永久丢失）。
    """
    from src.api.stream.pipeline_async import stream_call_async

    _patch_pipeline_env(monkeypatch)
    client = httpx.AsyncClient(transport=_Transport(
        [gzip.compress(_SSE_PLAIN)], {"content-encoding": "gzip"}))

    async def _get_client():
        return client

    monkeypatch.setattr(ca, "get_async_client", _get_client)

    async def _run():
        try:
            return await stream_call_async(
                [{"role": "user", "content": "hi"}], "m", False, None,
                display=None, label="assistant", silent=True,
            )
        finally:
            await client.aclose()

    result = asyncio.run(_run())
    assert result[1] == "ab"
    assert not isinstance(result[1], str) or "空响应" not in result[1]


def test_stream_call_async_gzip_stream_reaches_empty_response_guard(monkeypatch):
    """反证：压缩流经解码后若**确实**无 delta，仍走原有 EmptyResponseError 判定。

    （保证修复没有把「真空响应」的守护一起改掉。）
    """
    from src.api.stream.pipeline_async import stream_call_async

    _patch_pipeline_env(monkeypatch)
    body = b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n'
    client = httpx.AsyncClient(transport=_Transport(
        [gzip.compress(body)], {"content-encoding": "gzip"}))

    async def _get_client():
        return client

    monkeypatch.setattr(ca, "get_async_client", _get_client)

    async def _run():
        try:
            return await stream_call_async(
                [{"role": "user", "content": "hi"}], "m", False, None,
                display=None, label="assistant", silent=True,
            )
        finally:
            await client.aclose()

    with pytest.raises(EmptyResponseError):
        asyncio.run(_run())
