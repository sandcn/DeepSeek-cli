"""Agent 中间件插件 — 从清单配置启用/禁用 Agent 主循环中间件。

「一切皆插件」：Agent 的 Pipeline 中间件由 ``src.core.middleware.registry``
的内置注册表提供，本插件按 ``config.disabled`` 禁用内置中间件（如 ``audit``），
也可经 ``ctx`` 注册扩展中间件。禁用是挂在 Fiber 上的可逆副作用（卸载恢复）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("agent_middleware")
def apply(ctx):
    from ..core.middleware.registry import disable_builtin_middleware

    disabled = ctx.config.get("disabled") or ()
    if disabled:
        undo = disable_builtin_middleware(disabled)
        ctx.effect(lambda: undo)
