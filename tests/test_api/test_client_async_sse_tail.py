"""测试 SSE 尾部残余 data 行解析 + finish_reason 结束检查。

覆盖 _stream_iter_async 的辅助容错（步骤 5）：
- 循环结束后的残余 buffer（尾部 data 行无 \n，原实现从不解析）解析并 yield
- 残余 data: [DONE] 正常结束
- finish_reason 非空时先 yield 当前 chunk 再提前结束
- 残缺 JSON 尾行按既有策略跳过（不抛错）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.api.client_async import _stream_iter_async


class _FakeResponse:
    """伪 httpx.Response — 仅实现 _stream_iter_async 所需协议。"""

    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def aiter_raw(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()

    async def aread(self):
        return b""


class _FakeClient:
    """伪 httpx.AsyncClient — stream() 返回伪响应（异步上下文管理器）。"""

    def __init__(self, chunks):
        self._resp = _FakeResponse(chunks)

    def stream(self, method, url, **kwargs):
        return self._resp


async def _collect(chunks):
    """驱动 _stream_iter_async 并收集全部 yield 的 dict（不发起真实网络）。"""
    fake = _FakeClient(chunks)
    with patch(
        "src.api.client_async.get_async_client",
        new=AsyncMock(return_value=fake),
    ):
        return [c async for c in _stream_iter_async("http://x", {}, {})]


class TestSseTailDataLineWithoutNewline:
    """SSE 尾部无换行 data 行解析（步骤 5.1）。"""

    async def test_tail_data_line_without_newline_parsed(self):
        """尾行无 \\n 的完整 data 行被解析并 yield。"""
        chunks = [b'data: {"a":1}\n', b'data: {"b":2}']
        result = await _collect(chunks)
        assert result == [{"a": 1}, {"b": 2}]

    async def test_tail_done_without_newline(self):
        """残余 data: [DONE]（无 \\n）正常结束不抛错。"""
        chunks = [b'data: {"a":1}\n', b"data: [DONE]"]
        result = await _collect(chunks)
        assert result == [{"a": 1}]

    async def test_malformed_tail_skipped(self):
        """尾行为残缺 JSON（连接中断半行）→ 跳过不 yield 且不抛错。"""
        chunks = [b'data: {"a":1}\n', b'data: {"broken']
        result = await _collect(chunks)
        assert result == [{"a": 1}]

    async def test_empty_tail_skipped(self):
        """空/空白残余跳过。"""
        chunks = [b'data: {"a":1}\n', b""]
        result = await _collect(chunks)
        assert result == [{"a": 1}]

    async def test_tail_with_crlf_stripped(self):
        """残余行带 CRLF 行尾（\\r\\n）时去除行尾空白后正常解析。"""
        chunks = [b'data: {"a":1}\n', b'data: {"b":2}\r\n']
        result = await _collect(chunks)
        assert result == [{"a": 1}, {"b": 2}]


class TestFinishReasonEndsStream:
    """finish_reason 结束检查（步骤 5.2）。"""

    async def test_finish_reason_ends_stream(self):
        """finish_reason="stop" 的 chunk 被 yield 后迭代结束，后续 chunk 不再消费。"""
        chunks = [
            b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n',
            b'data: {"choices":[{"delta":{"content":"bye"},"finish_reason":"stop"}]}\n',
            b'data: {"choices":[{"delta":{"content":"extra"}}]}\n',
        ]
        result = await _collect(chunks)
        assert len(result) == 2
        assert result[0]["choices"][0]["delta"]["content"] == "hi"
        assert result[1]["choices"][0]["delta"]["content"] == "bye"

    async def test_finish_reason_on_tail_line_ends_stream(self):
        """残余行含 finish_reason → 先 yield 再结束。"""
        chunks = [
            b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}',
        ]
        result = await _collect(chunks)
        assert len(result) == 1
        assert result[0]["choices"][0]["delta"]["content"] == "hi"

    async def test_null_finish_reason_does_not_end(self):
        """null/空字符串 finish_reason 不触发提前结束。"""
        chunks = [
            b'data: {"choices":[{"delta":{"content":"a"},"finish_reason":null}]}\n',
            b'data: {"choices":[{"delta":{"content":"b"},"finish_reason":""}]}\n',
        ]
        result = await _collect(chunks)
        assert len(result) == 2

    async def test_finish_reason_after_done_not_consumed(self):
        """[DONE] 后到达的 chunk 不再消费（既有 done 短路保持）。"""
        chunks = [
            b'data: {"a":1}\n',
            b"data: [DONE]\n",
            b'data: {"b":2}\n',
        ]
        result = await _collect(chunks)
        assert result == [{"a": 1}]
