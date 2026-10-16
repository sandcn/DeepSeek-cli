"""流空闲超时配置测试 — 验证 _STREAM_IDLE_TIMEOUT 为 120 秒。"""

import asyncio
import pytest

import src.api.stream.pipeline_async as pa
from src.api.stream.pipeline_async import (
    _STREAM_IDLE_TIMEOUT,
    StreamIdleTimeoutError,
    _interruptible_iter_async,
    StreamContext,
)


def test_idle_timeout_value_is_120():
    assert _STREAM_IDLE_TIMEOUT == 120.0


@pytest.mark.asyncio
async def test_idle_timeout_raises_with_message(monkeypatch):
    monkeypatch.setattr(pa, "_STREAM_IDLE_TIMEOUT", 0.1)

    async def stalled_iter():
        if False:
            yield {}
        await asyncio.Event().wait()

    ctx = StreamContext("test-model", None, None, True)

    async def run():
        async for _ in _interruptible_iter_async(stalled_iter(), ctx):
            pass

    with pytest.raises(StreamIdleTimeoutError) as ei:
        await asyncio.wait_for(run(), timeout=10)

    assert "流空闲超时" in str(ei.value)
    assert "秒内未收到新数据" in str(ei.value)
