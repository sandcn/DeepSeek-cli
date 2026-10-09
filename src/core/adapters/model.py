"""模型调用端口适配器 — DefaultAsyncModelAdapter、MockAsyncModelAdapter"""
from __future__ import annotations

from typing import Any

from ..ports.model import AsyncModelPort, ModelResult


def get_sync_call_model():
    """返回同步模型调用函数（``api.model_async.call_model``）。

    适配器层延迟导入基础设施层，供核心层在无异步端口时的兜底同步调用。
    """
    from ...api.model_async import call_model
    return call_model


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
    """同步模型调用桥接器 — 将 api 层的摘要调用包装为核心层可用的接口。

    消除 core/context_manager.py 对 api/model_async.py 的直接导入依赖，
    将桥接逻辑归一到适配器层（core/adapters/model.py），遵循依赖倒置原则。

    使用方式：
        bridge = SyncModelBridge()
        reasoning, content, usage, tool_calls = bridge.summarize(messages, model=model)

    ★ 摘要调用走**流式**管线（``2026-10`` 用户需求「压缩上下文的 agent 调用
    API 时要用流式的」）：上下文压缩摘要是内部长输出调用，经
    ``api.model_async.call_model_summarize_async`` 走与主对话一致的 SSE 流式
    管线——避免非流式长连接在服务端/网关侧空闲超时被截断，并复用真实 usage
    校准与实时 token 统计。
    """

    def summarize(self, messages, model=None, tools=None, display=None,
                  label=None):
        """同步**流式**模型调用，返回 (reasoning, content, usage, tool_calls)。

        内部延迟导入 api.model_async.call_model_summarize_sync，避免模块加载时
        产生跨层依赖。调用方无需感知 api 层的存在。

        ``tools`` / ``display`` / ``label`` 形参仅为签名兼容保留：
        - 摘要只产出文本，不接受工具（``tools`` 恒忽略）；
        - 调用强制 ``silent`` + 内部 label（``label`` 恒忽略），不渲染到终端、
          不进入主 Agent/SubAgent 渲染通道。

        ★ 内部 label（``"summarize"``）——**非主 Agent 对话轮次**：core 侧的
        真实 prompt token 基线与上下文使用率实时增量均按该标签跳过，内部调用
        不得污染 ``main · N%`` 上下文使用率。
        """
        from ...api.model_async import call_model_summarize_sync
        return call_model_summarize_sync(messages, model)
