"""内置模型 Provider 插件 — 每个 provider 一个独立插件条目。

「一切皆插件」：模型适配器不是 llm 插件内部的硬编码分支，而是各自独立的
provider 插件（对应 dsh 的 ``dsh-llm-deepseek`` / ``dsh-llm-pi-ai`` 等
独立包）。它们在 ``model`` bundle 中经清单声明、依赖 ``ctx.llm`` 服务，
通过 ``ctx.llm.register_provider`` 注册，卸载时随 Fiber 自动撤销。

外部插件可用同样的方式注册自己的 provider：

    @plugin("my_llm", inject=["llm"])
    def apply(ctx):
        ctx.llm.register_provider(
            "my_llm", lambda: MyAdapter(), prefixes=("my-model",)
        )
"""

from __future__ import annotations

from ..api.provider_registry import BUILTIN_PROVIDERS
from ..kernel import plugin

_SPECS = {spec["name"]: spec for spec in BUILTIN_PROVIDERS}


def _register(ctx, name: str) -> None:
    spec = _SPECS[name]
    undo = ctx.llm.register_provider(
        spec["name"],
        spec["factory"],
        prefixes=spec.get("prefixes", ()),
        substrings=spec.get("substrings", ()),
        fallback=bool(spec.get("fallback", False)),
    )
    # 把撤销绑定到本 provider 插件自身的 Fiber（卸载时自动回退）
    ctx.effect(lambda: undo)


@plugin("llm_provider_deepseek", inject=["llm"])
def apply_deepseek(ctx):
    _register(ctx, "deepseek")


@plugin("llm_provider_anthropic", inject=["llm"])
def apply_anthropic(ctx):
    _register(ctx, "anthropic")


@plugin("llm_provider_ollama", inject=["llm"])
def apply_ollama(ctx):
    _register(ctx, "ollama")


@plugin("llm_provider_openai_compat", inject=["llm"])
def apply_openai_compat(ctx):
    _register(ctx, "openai_compat")


__all__ = [
    "apply_deepseek",
    "apply_anthropic",
    "apply_ollama",
    "apply_openai_compat",
]
