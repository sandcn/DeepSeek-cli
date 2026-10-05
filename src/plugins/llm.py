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

    def __init__(self, ctx, config=None):
        # 先建自有 provider 注册表（在 super().__init__ 提供 ctx.llm 之前），
        # 避免构造期解析服务时自引用递归。
        # 「一切皆插件」：内置 provider 由清单中的独立条目
        # （``llm_provider_*``）注册；本服务收到组合根注入的
        # ``managed_llm_providers`` 后抑制对应内置项的默认装配，使 overlay
        # 禁用单个 provider 条目真正生效。``disabled_llm_providers`` 为显式
        # 禁用（不注册，也不由条目注册）。
        cfg = config or getattr(ctx, "config", None) or {}
        self._managed_providers = [str(item) for item in (cfg.get("managed_llm_providers") or ())]
        self._disabled_providers = [str(item) for item in (cfg.get("disabled_llm_providers") or ())]
        from ..api.provider_registry import build_default_registry

        self._provider_registry = build_default_registry(
            managed=self._managed_providers,
            disabled=self._disabled_providers,
        )
        super().__init__(ctx, config)

    def adapter(self, model: str):
        from ..api._adapter_manager import get_adapter

        return get_adapter(model)

    # ── Provider 注册（「一切皆插件」：模型 provider 可插拔） ──

    def provider_registry(self):
        """返回本服务的 LLM provider 注册表（内核服务自有实例）。"""
        return self._provider_registry

    def register_provider(self, name, factory, *, prefixes=(), substrings=(), fallback=False):
        """注册一个模型 provider（注册即副作用，卸载时自动撤销）。

        Args:
            name: provider 名（同名注册覆盖旧条目，卸载时恢复）。
            factory: 无参可调用，返回适配器实例。
            prefixes: 前缀匹配列表（模型名小写后以某前缀开头即匹配）。
            substrings: 子串匹配列表（模型名包含某子串即匹配）。
            fallback: 作为兜底 provider（无其它条目匹配时使用）。

        Returns:
            撤销函数（幂等）。
        """
        registry = self.provider_registry()
        undo = registry.register(
            name,
            factory,
            prefixes=prefixes,
            substrings=substrings,
            fallback=fallback,
            source="plugin",
        )
        self.ctx.effect(lambda: undo)
        return undo

    def unregister_provider(self, name) -> bool:
        """注销一个 provider（返回是否存在）。"""
        return self.provider_registry().unregister(name)

    def providers(self) -> list:
        """列出全部 provider 条目（自省）。"""
        return self.provider_registry().describe()

    def provider_names(self) -> list:
        return self.provider_registry().names()

    def builtin_providers(self) -> list:
        """全部内置 provider 名（含被接管/禁用的）。"""
        from ..api.provider_registry import builtin_provider_names

        return builtin_provider_names()

    def managed_providers(self) -> list:
        """清单已接管的内置 provider 名（默认装配被抑制）。"""
        return list(self._managed_providers)

    def disabled_providers(self) -> list:
        """显式禁用的内置 provider 名。"""
        return list(self._disabled_providers)

    def resolve_provider(self, model: str) -> str:
        """返回某模型匹配到的 provider 名（无匹配返回 None）。"""
        return self.provider_registry().match_name(model)

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
