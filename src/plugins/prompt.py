"""提示词插件 — 提供 ``ctx.prompt``。

包装 ``src/prompt_builder``：系统提词构建、子代理提词、空模式切换。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class PromptService(Service):
    """提示词服务 — 占据 ``ctx.prompt``。"""

    provide = "prompt"
    name = "prompt"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..prompt_builder.builder import _EMPTY_MODE  # noqa: F401  触发加载

    def build(self) -> list[str]:
        from ..prompt_builder.builder import build_system_prompt

        return build_system_prompt()

    def build_for(self, agent_type: str) -> str:
        from ..prompt_builder import builder
        from ..core.agent_types import get_spec

        # 「一切皆插件」：类型 → 提示词文件的映射来自 Agent 类型注册表
        # （每个类型是清单中的独立插件条目，可禁用/替换）。
        name = get_spec(agent_type).agent_name or "sub"
        builder_fn = getattr(builder, f"build_{name}_agent_system_prompt", None)
        if builder_fn is None:
            builder_fn = builder.build_subagent_system_prompt
        return builder_fn()

    def environment_info(self) -> str:
        from ..prompt_builder.builder import build_environment_info

        return build_environment_info()

    def is_empty_mode(self) -> bool:
        from ..prompt_builder.builder import is_empty_mode

        return is_empty_mode()

    def set_empty_mode(self, enabled: bool) -> None:
        from ..prompt_builder.builder import set_empty_mode

        set_empty_mode(enabled)

    def toggle_empty_mode(self) -> bool:
        from ..prompt_builder.builder import toggle_empty_mode

        return toggle_empty_mode()

    def reset_cache(self) -> None:
        from ..prompt_builder.builder import reset_prompts_cache

        reset_prompts_cache()

    @property
    def port(self):
        from ..core.adapters.prompt_builder import DefaultPromptBuilderAdapter

        return DefaultPromptBuilderAdapter()


@plugin("prompt", inject=["config"], provide=["prompt"])
def apply(ctx):
    return PromptService(ctx)
