"""渲染器插件 — 提供 ``ctx.renderer``。

渲染扩展入口：内置 TokenFilter 的注册（``renderer_filter`` 条目）与插件扩展
filter 的可逆注册。内容渲染由 ``src.renderer.ansi`` 直接完成，本服务不持有
渲染器实例。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class RendererService(Service):
    """渲染服务 — 占据 ``ctx.renderer``。"""

    provide = "renderer"
    name = "renderer"
    inject = ("config",)

    # ── 扩展点（「一切皆插件」：渲染 filter 可插拔） ──

    def register_filter(self, factory):
        """注册 TokenFilter 工厂（注册即副作用，卸载时自动撤销）。

        工厂为无参可调用，每次装配时实例化；返回的 filter 需实现
        ``process(tokens, ctx) -> tokens``。
        """
        from ..renderer.extensions import register_filter

        undo = register_filter(factory)
        self.ctx.effect(lambda: undo)
        return undo

    def filters(self) -> list:
        """列出已注册的扩展 filter 工厂。"""
        from ..renderer.extensions import filter_factories

        return list(filter_factories())

    def builtin_filters(self) -> list:
        """列出当前生效的内置 filter 工厂。"""
        from ..renderer.extensions import builtin_filter_factories

        return list(builtin_filter_factories())

    def builtin_filter_ids(self) -> list:
        """列出全部内置 filter id（含被接管/禁用的）。"""
        from ..renderer.extensions import builtin_filter_ids

        return list(builtin_filter_ids())

    def render_diff(self, path: str, old_content: str, new_content: str) -> str:
        if self.ctx.has("ui"):
            return self.ctx.consume("ui").render_diff(path, old_content, new_content)
        from ..core.adapters import ui_runtime as _ui_runtime

        return _ui_runtime.render_diff_to_ansi(path, old_content, new_content)


@plugin("renderer", inject=["config"], provide=["renderer"])
def apply(ctx):
    return RendererService(ctx, ctx.config)
