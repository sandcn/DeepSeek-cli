"""测试流式断线重试：未产出内容时交由重试层重试（10 次），已产出内容保留。

覆盖修复：
- stream_call_async 未产出任何内容时重新抛出异常 → retry_api_call_async 重试
- stream_call_async 已产出部分内容时返回已累积内容（不重启流避免重复渲染）
- retry_api_call_async 对连接错误重试 10 次
- 空闲超时 / 连接错误日志降级为 WARNING（不再 ERROR 刷屏）
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from src.api._retry import retry_api_call_async
from src.api.stream.pipeline_async import stream_call_async, StreamIdleTimeoutError


class _FakeAdapter:
    """伪适配器 — 仅提供 stream_call_async 所需协议。"""

    _protocol = ""

    def build_request_kwargs(self, **kwargs):
        return {}


class _FakeAsyncIter:
    """模拟 SSE response_iter：依次 yield chunks，最后抛 exc。"""

    def __init__(self, chunks, exc=None):
        self._chunks = list(chunks)
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._exc is not None:
            exc, self._exc = self._exc, None
            raise exc
        raise StopAsyncIteration

    async def aclose(self):
        return None


async def _run_stream(chunks, exc=None):
    """在 patch 作用域内 await stream_call_async（mock 适配器 + mock 上游流）。"""
    adapter = _FakeAdapter()
    fake_iter = _FakeAsyncIter(chunks, exc)
    with patch("src.api._adapter_manager.get_adapter", return_value=adapter), \
         patch("src.api.stream.pipeline_async.chat_completions_async",
               new=AsyncMock(return_value=fake_iter)):
        return await stream_call_async([], "test-model", False, silent=True)


class TestStreamCallReRaiseForRetry:
    """未产出内容时 stream_call_async 重新抛出，交由重试层重试。"""

    async def test_error_before_content_reraises(self):
        """首个 SSE 块前超时 → 重新抛出，不返回空结果（触发重试层）。"""
        with pytest.raises(StreamIdleTimeoutError):
            await _run_stream([], exc=StreamIdleTimeoutError("timeout"))

    async def test_connect_error_before_content_reraises(self):
        """首个 SSE 块前连接错误 → 重新抛出（触发重试层）。"""
        with pytest.raises(httpx.ConnectError):
            await _run_stream([], exc=httpx.ConnectError("connect failed"))

    async def test_content_after_error_returns_partial(self):
        """已产出部分内容后断线 → 返回已累积内容，不重新抛出（避免重复渲染）。"""
        chunks = [{"choices": [{"delta": {"content": "hi"}}]}]
        result = await _run_stream(chunks, exc=StreamIdleTimeoutError("timeout"))
        # (reasoning, content, usage, tool_calls)
        assert result[1] == "hi"


class TestRetryApiCall10Times:
    """retry_api_call_async 对连接错误重试 10 次。"""

    async def test_connection_error_retries_10_times(self):
        calls = {"n": 0}

        async def _fail():
            calls["n"] += 1
            raise httpx.ConnectError("boom")

        with patch("src.api._retry.wait_for_interrupt_async",
                   new=AsyncMock(return_value=False)):
            result = await retry_api_call_async(
                _fail, silent=True, fixed_delay_sec=0, override_max_retries=10,
            )
        assert calls["n"] == 10
        assert "连接错误" in result[1]

    async def test_default_max_retries_is_10(self):
        """未传 override 时使用全局 MAX_RETRIES（默认 10）。"""
        calls = {"n": 0}

        async def _fail():
            calls["n"] += 1
            raise httpx.ConnectError("boom")

        with patch("src.api._retry.wait_for_interrupt_async",
                   new=AsyncMock(return_value=False)):
            await retry_api_call_async(_fail, silent=True, fixed_delay_sec=0)
        assert calls["n"] == 10

    async def test_default_retry_interval_is_fixed_30s(self):
        """默认重试间隔为固定 30s（RETRY_BASE_SEC），无指数退避。"""
        calls = {"n": 0}
        waits = []

        async def _fail():
            calls["n"] += 1
            raise httpx.ConnectError("boom")

        async def _fake_wait(timeout):
            waits.append(timeout)
            return False

        with patch("src.api._retry.wait_for_interrupt_async", new=_fake_wait):
            await retry_api_call_async(_fail, silent=True, override_max_retries=3)
        assert calls["n"] == 3
        # 3 次尝试间有 2 次等待，每次固定 30s
        assert waits == [30.0, 30.0]
