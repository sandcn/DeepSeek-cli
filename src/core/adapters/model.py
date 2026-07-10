"""模型调用端口适配器 — DefaultAsyncModelAdapter、MockAsyncModelAdapter"""
from __future__ import annotations

from typing import Any

from ..ports.model import AsyncModelPort, ModelResult


class DefaultAsyncModelAdapter(AsyncModelPort):
    """异步默认适配器 — 包装 src/api/model_async.py 中的 async 函数。"""

    async def call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
        silent: bool = False,
    ) -> ModelResult:
        from ...api.model_async import call_model_async

        reasoning, content, usage, tool_calls = await call_model_async(
            messages=messages,
            model=model,
            tools=tools,
            display=display,
            label=label,
            silent=silent,
        )
        return ModelResult(
            reasoning=reasoning,
            content=content,
            usage=usage,
            tool_calls=tool_calls,
        )

    async def call_sync(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
    ) -> ModelResult:
        from ...api.model_async import call_model_sync_async

        reasoning, content, usage, tool_calls = await call_model_sync_async(
            messages=messages,
            model=model,
            tools=tools,
            display=display,
            label=label,
        )
        return ModelResult(
            reasoning=reasoning,
            content=content,
            usage=usage,
            tool_calls=tool_calls,
        )


class MockAsyncModelAdapter(AsyncModelPort):
    """异步 Mock 适配器 — 返回预设的 ModelResult。"""

    def __init__(self, result: ModelResult | None = None) -> None:
        self._result = result or ModelResult()
        self.call_count: int = 0
        self.last_messages: list[dict] | None = None
        self.last_model: str | None = None

    async def call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
        silent: bool = False,
    ) -> ModelResult:
        self.call_count += 1
        self.last_messages = messages
        self.last_model = model
        return self._result

    async def call_sync(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
    ) -> ModelResult:
        self.call_count += 1
        self.last_messages = messages
        self.last_model = model
        return self._result
