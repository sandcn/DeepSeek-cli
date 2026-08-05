"""RendererFactory — 流式渲染器实例创建工厂。

将渲染器创建逻辑从 StreamContext 中解耦，集中管理。
当渲染器构造参数变更时，只需修改此文件，无需遍历所有调用点。

使用方式：
    from .factory import create_stream_renderers
    reason_renderer, content_renderer = create_stream_renderers()
"""

from __future__ import annotations

from . import IncrementalRenderer

def create_stream_renderers(output_file=None) -> tuple:
    """创建一对流式渲染器（推理渲染器 + 内容渲染器）。

    两个渲染器共享相同的输出目标，
    但推理渲染器使用 dim 样式以视觉区分推理内容。

    Args:
        output_file: 输出文件对象。None 时使用 Console 默认输出（sys.stdout）。
                     流式场景中应传入 sys.__stdout__ 绕过 stdout 捕获劫持。

    Returns:
        (reasoning_renderer, content_renderer) 二元组
    """
    kwargs = {"show_indicator": False}
    if output_file is not None:
        kwargs["_file"] = output_file

    reasoning_renderer = IncrementalRenderer(
        style="dim", **kwargs,
    )
    content_renderer = IncrementalRenderer(**kwargs)

    return reasoning_renderer, content_renderer