"""渲染器插件 — 提供 ``ctx.renderer``。

增量流式 Markdown 渲染器的创建入口（Parser → TokenPipeline →
RenderEngine → OutputAdapter）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class RendererService(Service):
    """渲染服务 — 占据 ``ctx.renderer``。"""

    provide = "renderer"
    name = "renderer"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_render_targets") or ()
        if managed:
            from ..renderer.targets.registry import set_managed_builtin_render_targets

            undo_managed = set_managed_builtin_render_targets(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_render_targets") or ()
        self._disabled_targets = list(disabled)
        if disabled:
            from ..renderer.targets.registry import disable_builtin_render_targets

            undo_disabled = disable_builtin_render_targets(disabled)
            ctx.effect(lambda: undo_disabled)

    def create(self, **kwargs):
        from ..renderer import IncrementalRenderer

        return IncrementalRenderer(**kwargs)

    def create_stream_renderers(self, output_file=None):
        from ..renderer.factory import create_stream_renderers

        return create_stream_renderers(output_file)

    # ── 扩展点（「一切皆插件」：渲染 handler/filter 可插拔） ──

    def register_handler(self, factory):
        """注册 TokenHandler 工厂（注册即副作用，卸载时自动撤销）。

        工厂为无参可调用，每次创建渲染器时实例化；返回的 handler 需实现
        ``get_token_types()`` 与 ``handle(token, engine)``。
        """
        from ..renderer.extensions import register_handler

        undo = register_handler(factory)
        self.ctx.effect(lambda: undo)
        return undo

    def register_filter(self, factory):
        """注册 TokenFilter 工厂（注册即副作用，卸载时自动撤销）。

        工厂为无参可调用，每次创建渲染器时实例化；返回的 filter 需实现
        ``process(tokens, ctx) -> tokens``。
        """
        from ..renderer.extensions import register_filter

        undo = register_filter(factory)
        self.ctx.effect(lambda: undo)
        return undo

    def handlers(self) -> list:
        """列出已注册的扩展 handler 工厂。"""
        from ..renderer.extensions import handler_factories

        return list(handler_factories())

    def filters(self) -> list:
        """列出已注册的扩展 filter 工厂。"""
        from ..renderer.extensions import filter_factories

        return list(filter_factories())

    def builtin_handlers(self) -> list:
        """列出当前生效的内置 handler 工厂（清单注册/默认/跳过接管）。"""
        from ..renderer.extensions import builtin_handler_factories

        return list(builtin_handler_factories())

    def builtin_filters(self) -> list:
        """列出当前生效的内置 filter 工厂。"""
        from ..renderer.extensions import builtin_filter_factories

        return list(builtin_filter_factories())

    def builtin_handler_ids(self) -> list:
        """列出全部内置 handler id（含被接管/禁用的）。"""
        from ..renderer.extensions import builtin_handler_ids

        return list(builtin_handler_ids())

    def builtin_filter_ids(self) -> list:
        """列出全部内置 filter id（含被接管/禁用的）。"""
        from ..renderer.extensions import builtin_filter_ids

        return list(builtin_filter_ids())

    # ── 渲染目标（「一切皆插件」：目标可插拔） ─────────────

    def target_ids(self) -> list:
        """当前生效的渲染目标 id（生效内置 + 扩展）。"""
        from ..renderer.targets.registry import (
            builtin_render_target_factories,
            render_target_factories,
        )

        return sorted(set(builtin_render_target_factories()) | set(render_target_factories()))

    def builtin_target_ids(self) -> list:
        """全部内置渲染目标 id（含被接管/禁用的）。"""
        from ..renderer.targets.registry import builtin_render_target_ids

        return list(builtin_render_target_ids())

    def managed_targets(self) -> list:
        from ..renderer.targets.registry import managed_render_target_ids

        return list(managed_render_target_ids())

    def disabled_targets(self) -> list:
        return sorted(self._disabled_targets)

    def register_target(self, name: str, factory) -> object:
        """注册扩展渲染目标（注册即副作用，卸载时自动撤销）。"""
        from ..renderer.targets.registry import register_render_target

        undo = register_render_target(name, factory)
        self.ctx.effect(lambda: undo)
        return undo

    def create_target(self, name=None, **kwargs):
        """按名构造渲染目标实例（None/空用默认目标）。"""
        from ..renderer.targets.registry import resolve_render_target

        return resolve_render_target(name or "", **kwargs)

    def render_diff(self, path: str, old_content: str, new_content: str) -> str:
        if self.ctx.has("ui"):
            return self.ctx.consume("ui").render_diff(path, old_content, new_content)
        from ..core.adapters import ui_runtime as _ui_runtime

        return _ui_runtime.render_diff_to_ansi(path, old_content, new_content)


@plugin("renderer", inject=["config"], provide=["renderer"])
def apply(ctx):
    return RendererService(ctx, ctx.config)
