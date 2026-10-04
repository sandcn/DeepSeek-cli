"""模型适配器插件 — 提供 ``ctx.llm``。

模型侧接缝：按模型名路由到对应 Provider 适配器（DeepSeek / Anthropic /
Ollama / OpenAI 兼容），并暴露异步模型端口供 Agent 调用。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class LlmService(Service):
    """模型服务 — 占据 ``ctx.llm``。"""

    provide = "llm"
    name = "llm"
    inject = ("config",)

    def adapter(self, model: str):
        from ..api._adapter_manager import get_adapter

        return get_adapter(model)

    def model_port(self):
        from ..core.adapters.model import DefaultAsyncModelAdapter

        return DefaultAsyncModelAdapter()

    async def call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display=None,
        label: str | None = None,
        silent: bool = False,
    ):
        port = self.model_port()
        return await port.call(messages, model, tools, display, label, silent)

    async def call_sync(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        display=None,
        label: str | None = None,
    ):
        port = self.model_port()
        return await port.call_sync(messages, model, tools, display, label)

    def models(self) -> list[str]:
        try:
            return list(self.ctx.consume("config").models())
        except Exception:
            return []

    def default_model(self) -> str:
        return self.ctx.consume("config").model()


@plugin("llm", inject=["config"], provide=["llm"])
def apply(ctx):
    return LlmService(ctx)
