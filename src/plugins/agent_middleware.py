"""Agent 中间件插件 — 从清单配置启用/禁用 Agent 主循环中间件。

「一切皆插件」：Agent 的 Pipeline 中间件由清单中的独立条目（``middleware``，
经 ``middleware_entries``）声明与注册。本聚合插件负责：

- 按组合根注入的 ``managed_middlewares``（清单已接管的 id，含被禁用的）声明
  接管——对应内置中间件不再走默认装配，只有条目注册者生效，因此
  overlay/patch 禁用单个中间件真正缺席（卸载时恢复默认）；
- 按 ``config.disabled`` 显式禁用指定内置中间件（如 ``audit``）。

以上均为挂在 Fiber 上的可逆副作用（卸载恢复）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("agent_middleware")
def apply(ctx):
    from ..core.middleware.registry import (
        disable_builtin_middleware,
        set_managed_builtin_middleware,
    )

    managed = ctx.config.get("managed_middlewares") or ()
    if managed:
        undo_managed = set_managed_builtin_middleware(managed)
        ctx.effect(lambda: undo_managed)

    disabled = ctx.config.get("disabled") or ()
    if disabled:
        undo = disable_builtin_middleware(disabled)
        ctx.effect(lambda: undo)
