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
        override_max_retries: int | None = None,
        fixed_delay_sec: float | None = None,
    ) -> ModelResult:
        from ...api.model_async import call_model_async

        reasoning, content, usage, tool_calls = await call_model_async(
            messages=messages,
            model=model,
            tools=tools,
            display=display,
            label=label,
            silent=silent,
            override_max_retries=override_max_retries,
            fixed_delay_sec=fixed_delay_sec,
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
        override_max_retries: int | None = None,
        fixed_delay_sec: float | None = None,
    ) -> ModelResult:
        from ...api.model_async import call_model_sync_async

        reasoning, content, usage, tool_calls = await call_model_sync_async(
            messages=messages,
            model=model,
            tools=tools,
            display=display,
            label=label,
            override_max_retries=override_max_retries,
            fixed_delay_sec=fixed_delay_sec,
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


class SyncModelBridge:
    """同步模型调用桥接器 — 将 api 层的 call_model_sync 包装为核心层可用的接口。

    消除 core/context_manager.py 对 api/model_async.py 的直接导入依赖，
    将桥接逻辑归一到适配器层（core/adapters/model.py），遵循依赖倒置原则。

    使用方式：
        bridge = SyncModelBridge()
        reasoning, content, usage, tool_calls = bridge.summarize(messages, model=model)
    """

    def summarize(self, messages, model=None, tools=None, display=None, label=None):
        """同步模型调用，返回 (reasoning, content, usage, tool_calls)。

        内部延迟导入 api.model_async.call_model_sync，避免模块加载时
        产生跨层依赖。调用方无需感知 api 层的存在。
        """
        from ...api.model_async import call_model_sync
        return call_model_sync(messages, model, tools, display, label)
