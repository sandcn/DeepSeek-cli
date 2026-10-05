"""渲染器内置扩展插件 — 从清单配置启用/禁用内置 handler / filter。

「一切皆插件」：渲染引擎的内置 handler（inline/code/math/...）与内置 filter
（code_block_batcher/heading_anchor/stream_optimizer）由
``src.renderer.extensions`` 的内置注册表提供，本插件按
``config.disabled_handlers`` / ``config.disabled_filters`` 禁用指定内置项
（用于替换或精简渲染管线）。禁用是挂在 Fiber 上的可逆副作用（卸载恢复）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("renderer_builtin", inject=["renderer"])
def apply(ctx):
    from ..renderer.extensions import (
        disable_builtin_filters,
        disable_builtin_handlers,
    )

    disabled_handlers = ctx.config.get("disabled_handlers") or ()
    disabled_filters = ctx.config.get("disabled_filters") or ()
    if disabled_handlers:
        undo_handlers = disable_builtin_handlers(disabled_handlers)
        ctx.effect(lambda: undo_handlers)
    if disabled_filters:
        undo_filters = disable_builtin_filters(disabled_filters)
        ctx.effect(lambda: undo_filters)
