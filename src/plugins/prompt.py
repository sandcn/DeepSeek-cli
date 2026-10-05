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
        cfg = config or getattr(ctx, "config", None) or {}
        # 「一切皆插件」：运行模式 / 提词来源由清单独立条目注册；本服务收到
        # 组合根注入的 managed_* 后抑制对应内置项的默认装配（条目被禁用即缺席）。
        managed_modes = cfg.get("managed_prompt_modes") or ()
        if managed_modes:
            from ..prompt_builder.modes import set_managed_builtin_modes

            undo = set_managed_builtin_modes(managed_modes)
            ctx.effect(lambda: undo)
        disabled_modes = cfg.get("disabled_prompt_modes") or ()
        if disabled_modes:
            from ..prompt_builder.modes import disable_builtin_modes

            undo = disable_builtin_modes(disabled_modes)
            ctx.effect(lambda: undo)
        managed_sources = cfg.get("managed_prompt_sources") or ()
        if managed_sources:
            from ..prompt_builder.sources import set_managed_builtin_prompt_sources

            undo = set_managed_builtin_prompt_sources(managed_sources)
            ctx.effect(lambda: undo)
        disabled_sources = cfg.get("disabled_prompt_sources") or ()
        if disabled_sources:
            from ..prompt_builder.sources import disable_builtin_prompt_sources

            undo = disable_builtin_prompt_sources(disabled_sources)
            ctx.effect(lambda: undo)
        from ..prompt_builder.builder import _EMPTY_MODE  # noqa: F401  触发加载

    # ── 自省（运行模式 / 提词来源注册表） ───────────────

    def modes(self) -> list:
        """当前生效的主 Agent 运行模式名（内置 + 扩展）。"""
        from ..prompt_builder.modes import active_modes

        return list(active_modes())

    def builtin_modes(self) -> list:
        from ..prompt_builder.modes import builtin_mode_names

        return list(builtin_mode_names())

    def managed_modes(self) -> list:
        from ..prompt_builder.modes import managed_mode_names

        return list(managed_mode_names())

    def mode_order(self) -> list:
        from ..prompt_builder.modes import mode_order

        return list(mode_order())

    def sources(self) -> dict:
        """当前生效的提词来源（agent → 文件基名）。"""
        from ..prompt_builder.sources import active_prompt_sources

        return dict(active_prompt_sources())

    def builtin_sources(self) -> list:
        from ..prompt_builder.sources import builtin_prompt_source_ids

        return list(builtin_prompt_source_ids())

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

    def get_mode(self) -> str:
        from ..prompt_builder.builder import get_mode

        return get_mode()

    def set_mode(self, mode: str) -> str:
        from ..prompt_builder.builder import set_mode

        return set_mode(mode)

    def cycle_mode(self) -> str:
        from ..prompt_builder.builder import cycle_mode

        return cycle_mode()

    def mode_label(self, mode: str | None = None) -> str:
        from ..prompt_builder.builder import mode_label

        return mode_label(mode)

    def is_empty_mode(self) -> bool:
        from ..prompt_builder.builder import is_empty_mode

        return is_empty_mode()

    def is_simple_mode(self) -> bool:
        from ..prompt_builder.builder import is_simple_mode

        return is_simple_mode()

    def is_standard_mode(self) -> bool:
        from ..prompt_builder.builder import is_standard_mode

        return is_standard_mode()

    def set_empty_mode(self, enabled: bool) -> None:
        from ..prompt_builder.builder import set_empty_mode

        set_empty_mode(enabled)

    def set_simple_mode(self, enabled: bool = True) -> None:
        from ..prompt_builder.builder import set_simple_mode

        set_simple_mode(enabled)

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
