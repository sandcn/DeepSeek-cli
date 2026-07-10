"""模型调用端口 — 异步端口抽象接口

仅保留异步端口（AsyncModelPort）及其默认适配器与 Mock 适配器。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


# ─── 统一结果数据类 ────────────────────────────────────────────────────────────


@dataclass
class ModelResult:
    """模型调用统一结果。"""
    reasoning: str = ""
    content: str = ""
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    tool_calls: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 异步端口
# ═══════════════════════════════════════════════════════════════


class AsyncModelPort(ABC):
    """异步模型调用抽象端口。

    与 ModelPort 接口对等，但所有方法均为 async。
    核心层通过此接口使用 asyncio 调用 LLM。
    """

    @abstractmethod
    async def call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
        silent: bool = False,
    ) -> ModelResult:
        """异步流式调用模型，返回 ModelResult。"""
        ...

    @abstractmethod
    async def call_sync(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display: Any = None,
        label: str | None = None,
    ) -> ModelResult:
        """异步非流式调用模型，返回 ModelResult。"""
        ...

    @abstractmethod
    def call_sync_blocking(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
    ) -> ModelResult:
        """同步（阻塞）调用模型，返回 ModelResult。

        在已有事件循环的上下文中使用 loop.run_until_complete()，
        否则使用 asyncio.run()。
        """
        ...


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

    def call_sync_blocking(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
    ) -> ModelResult:
        from ...api.model_async import call_model_sync

        result = call_model_sync(messages, model=model, tools=tools)
        if result is None:
            return ModelResult()
        if isinstance(result, tuple):
            # call_model_sync 返回 (content, tool_calls) 或 (reasoning, content, usage, tool_calls)
            # 根据长度判断
            if len(result) == 2:
                return ModelResult(content=result[0], tool_calls=result[1] or [])
            elif len(result) == 4:
                return ModelResult(reasoning=result[0], content=result[1], usage=result[2], tool_calls=result[3] or [])
        return ModelResult(content=str(result))


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

    def call_sync_blocking(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
    ) -> ModelResult:
        self.call_count += 1
        self.last_messages = messages
        self.last_model = model
        return self._result
