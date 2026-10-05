"""ClawBot 插件 — 提供 ``ctx.clawbot``。

微信远程控制模式（默认 TUI：非全屏聊天界面 + 本地输入 + 微信多用户共享
同一会话）作为独立插件条目承载；``ctx.clawbot.run(...)`` 装配并运行
ClawBot 运行时，可被 Profile/Patch 禁用或替换。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ClawbotService(Service):
    """ClawBot 服务 — 占据 ``ctx.clawbot``。"""

    provide = "clawbot"
    name = "clawbot"
    inject = ("config", "sessions")

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        # 「一切皆插件」：远程指令由清单独立条目注册；本服务收到组合根注入的
        # managed_clawbot_commands 后抑制对应内置命令的默认装配（条目被禁用即缺席）。
        managed = cfg.get("managed_clawbot_commands") or ()
        if managed:
            from ..clawbot.command_registry import set_managed_builtin_clawbot_commands

            undo = set_managed_builtin_clawbot_commands(managed)
            ctx.effect(lambda: undo)
        disabled = cfg.get("disabled_clawbot_commands") or ()
        if disabled:
            from ..clawbot.command_registry import disable_builtin_clawbot_commands

            undo = disable_builtin_clawbot_commands(disabled)
            ctx.effect(lambda: undo)

    def commands(self) -> list:
        """当前生效的远程指令名（自省）。"""
        from ..clawbot.command_registry import active_clawbot_commands

        return sorted(active_clawbot_commands())

    def builtin_commands(self) -> list:
        from ..clawbot.command_registry import builtin_clawbot_command_ids

        return list(builtin_clawbot_command_ids())

    async def run(self, *, model=None, re_login: bool = False, tui: bool = True):
        from ..clawbot.runner import run_clawbot

        return await run_clawbot(model=model, re_login=re_login, tui=tui)


@plugin("clawbot", inject=["config", "sessions"], provide=["clawbot"])
def apply(ctx):
    return ClawbotService(ctx)


__all__ = ["ClawbotService", "apply"]
