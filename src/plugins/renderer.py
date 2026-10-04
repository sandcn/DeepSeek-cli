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

    def create(self, **kwargs):
        from ..renderer import IncrementalRenderer

        return IncrementalRenderer(**kwargs)

    def create_stream_renderers(self, output_file=None):
        from ..renderer.factory import create_stream_renderers

        return create_stream_renderers(output_file)

    def render_diff(self, path: str, old_content: str, new_content: str) -> str:
        from .ui import _ui_runtime

        return _ui_runtime.render_diff_to_ansi(path, old_content, new_content)


@plugin("renderer", inject=["config"], provide=["renderer"])
def apply(ctx):
    return RendererService(ctx)
