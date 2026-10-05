"""渲染器内置扩展插件 — 从清单配置启用/禁用内置 handler / filter。

「一切皆插件」：渲染引擎的内置 handler（inline/code/math/...）与内置 filter
（code_block_batcher/heading_anchor/stream_optimizer）由清单中的独立条目
（``renderer_handler`` / ``renderer_filter``，经 ``renderer_entries``）声明与
注册。本聚合插件负责：

- 按组合根注入的 ``managed_handlers`` / ``managed_filters``（清单已接管的 id，
  含被禁用的）声明接管——对应内置项不再走默认装配，只有条目注册者生效，
  因此 overlay/patch 禁用单个 handler/filter 真正缺席（卸载时恢复默认）；
- 按 ``config.disabled_handlers`` / ``config.disabled_filters`` 显式禁用指定
  内置项（用于替换或精简渲染管线）。

以上均为挂在 Fiber 上的可逆副作用（卸载恢复）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("renderer_builtin", inject=["renderer"])
def apply(ctx):
    from ..renderer.extensions import (
        disable_builtin_filters,
        disable_builtin_handlers,
        set_managed_builtin_filters,
        set_managed_builtin_handlers,
    )

    managed_handlers = ctx.config.get("managed_handlers") or ()
    managed_filters = ctx.config.get("managed_filters") or ()
    if managed_handlers:
        undo_managed_handlers = set_managed_builtin_handlers(managed_handlers)
        ctx.effect(lambda: undo_managed_handlers)
    if managed_filters:
        undo_managed_filters = set_managed_builtin_filters(managed_filters)
        ctx.effect(lambda: undo_managed_filters)

    disabled_handlers = ctx.config.get("disabled_handlers") or ()
    disabled_filters = ctx.config.get("disabled_filters") or ()
    if disabled_handlers:
        undo_handlers = disable_builtin_handlers(disabled_handlers)
        ctx.effect(lambda: undo_handlers)
    if disabled_filters:
        undo_filters = disable_builtin_filters(disabled_filters)
        ctx.effect(lambda: undo_filters)
