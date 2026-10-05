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

    async def run(self, *, model=None, re_login: bool = False, tui: bool = True):
        from ..clawbot.runner import run_clawbot

        return await run_clawbot(model=model, re_login=re_login, tui=tui)


@plugin("clawbot", inject=["config", "sessions"], provide=["clawbot"])
def apply(ctx):
    return ClawbotService(ctx)


__all__ = ["ClawbotService", "apply"]
