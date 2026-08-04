"""测试 _stream_iter_async 断线重试的重复渲染修复。

修复背景：断线后内部 `max_attempts=2` 重试会从流开头重启，若断线发生在
已产出部分 chunk 之后，重试会从开头重新 yield 已渲染过的内容 → 下游重复显示。

修复行为：
- 已产出任何 chunk 后断线 → 不再重启，直接抛出连接错误（保留部分内容）
- 未产出任何 chunk 时断线 → 仍安全重启（重置客户端后重试一次）
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from src.api.client_async import _stream_iter_async


class _FakeResponse:
    """伪 httpx.Response — 在 chunks 后按需抛出连接错误。"""

    status_code = 200

    def __init__(self, chunks, exc=None):
        self._chunks = chunks
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def aiter_raw(self):
        async def gen():
            for c in self._chunks:
                yield c
            if self._exc is not None:
                raise self._exc
        return gen()

    async def aread(self):
        return b""


class _FakeClient:
    """伪 httpx.AsyncClient — stream() 返回指定伪响应。"""

    def __init__(self, response):
        self._resp = response

    def stream(self, method, url, **kwargs):
        return self._resp


class TestMidStreamDisconnectNoRestart:
    """已产出部分内容后断线 → 不重启，避免重复渲染。"""

    async def test_partial_content_then_error_raises_without_restart(self):
        """已 yield 一个 chunk 后断线 → 直接抛出连接错误，且不再次调用客户端。"""
        client1 = _FakeClient(_FakeResponse(
            chunks=[b'data: {"a":1}\n'],
            exc=httpx.ConnectError("connection lost"),
        ))
        client2 = _FakeClient(_FakeResponse(
            chunks=[b'data: {"a":1}\n', b'data: {"b":2}\n'],
        ))
        mock_get = AsyncMock(side_effect=[client1, client2])
        mock_reset = AsyncMock()
        seen = []
        with patch("src.api.client_async.get_async_client", new=mock_get), \
             patch("src.api.client_async.reset_async_client", new=mock_reset):
            with pytest.raises(httpx.ConnectError):
                async for c in _stream_iter_async("http://x", {}, {}):
                    seen.append(c)
        # 已渲染的第一个 chunk 被保留
        assert seen == [{"a": 1}]
        # 未重启：客户端仅创建一次，未重置
        assert mock_get.await_count == 1
        assert mock_reset.await_count == 0

    async def test_multiple_chunks_then_error_no_restart(self):
        """已 yield 多个 chunk 后断线 → 同样不重启，保留全部已产出内容。"""
        client1 = _FakeClient(_FakeResponse(
            chunks=[
                b'data: {"a":1}\n',
                b'data: {"b":2}\n',
            ],
            exc=httpx.ReadError("stream read aborted"),
        ))
        mock_get = AsyncMock(side_effect=[client1])
        seen = []
        with patch("src.api.client_async.get_async_client", new=mock_get):
            with pytest.raises(httpx.ReadError):
                async for c in _stream_iter_async("http://x", {}, {}):
                    seen.append(c)
        assert seen == [{"a": 1}, {"b": 2}]
        assert mock_get.await_count == 1


class TestPreStreamErrorSafeRestart:
    """未产出任何 chunk 时断线 → 仍安全重启（既有行为保留）。"""

    async def test_connect_error_before_content_restarts_once(self):
        """首个 SSE 块前断线 → 重置客户端并重试一次，无重复内容。"""
        client1 = _FakeClient(_FakeResponse(
            chunks=[],
            exc=httpx.ConnectError("connect failed"),
        ))
        client2 = _FakeClient(_FakeResponse(
            chunks=[b'data: {"a":1}\n', b'data: {"b":2}\n'],
        ))
        mock_get = AsyncMock(side_effect=[client1, client2])
        mock_reset = AsyncMock()
        with patch("src.api.client_async.get_async_client", new=mock_get), \
             patch("src.api.client_async.reset_async_client", new=mock_reset):
            result = [c async for c in _stream_iter_async("http://x", {}, {})]
        assert result == [{"a": 1}, {"b": 2}]
        assert mock_get.await_count == 2
        assert mock_reset.await_count == 1
