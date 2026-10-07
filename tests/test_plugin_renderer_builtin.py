"""渲染器内置 handler/filter 插件化测试。

覆盖：
- 内置 handler/filter 注册表（id + 工厂）；
- RenderEngine 从注册表装配内置 handler；
- IncrementalRenderer 从注册表装配内置 filter；
- 禁用 API（disable_builtin_*）；
- renderer_builtin 插件按清单 config 禁用内置项（Fiber 卸载恢复）。
"""

from __future__ import annotations

import io
import json

import pytest

from src.renderer.extensions import (
    builtin_filter_factories,
    builtin_filter_ids,
    builtin_handler_factories,
    builtin_handler_ids,
    disable_builtin_filters,
    disable_builtin_handlers,
)
from src.plugins.bootstrap import build_kernel, shutdown_kernel


def test_builtin_ids_and_order():
    assert builtin_handler_ids() == [
        "inline", "code", "math", "mermaid", "details",
        "admonition", "html_block", "table", "fenced_div",
        "front_matter", "table_caption",
    ]
    assert builtin_filter_ids() == [
        "code_block_batcher", "heading_anchor", "stream_optimizer",
    ]


def _adapter():
    from rich.console import Console

    from src.renderer.output import OutputAdapter

    return OutputAdapter(Console(file=io.StringIO(), width=60))


def test_engine_assembles_builtin_handlers():
    from src.renderer.engine import RenderEngine
    from src.renderer.types import TokenType

    engine = RenderEngine(_adapter())
    assert engine._handler_registry.get(TokenType.HEADING) is not None
    assert engine._handler_registry.get(TokenType.CODE_FENCE_OPEN) is not None


def test_incremental_renderer_assembles_builtin_filters():
    from src.renderer import IncrementalRenderer
    from src.renderer.pipeline import CodeBlockBatcher
    from src.renderer.pipeline_filters import HeadingAnchorFilter, TokenStreamOptimizer

    renderer = IncrementalRenderer(show_indicator=False, captured_output=[])
    kinds = [type(f) for f in renderer._pipeline._filters]
    assert CodeBlockBatcher in kinds
    assert HeadingAnchorFilter in kinds
    assert TokenStreamOptimizer in kinds


def test_disable_builtin_filter_api():
    undo = disable_builtin_filters(["stream_optimizer"])
    try:
        assert len(builtin_filter_factories()) == 2
        assert len(builtin_filter_ids()) == 3
    finally:
        undo()
    assert len(builtin_filter_factories()) == 3


def test_disable_builtin_handler_api_and_unknown():
    undo = disable_builtin_handlers(["code"])
    try:
        assert len(builtin_handler_factories()) == 10
    finally:
        undo()
    assert len(builtin_handler_factories()) == 11
    with pytest.raises(KeyError):
        disable_builtin_handlers(["nope"])


async def test_renderer_builtin_plugin_disable(tmp_path):
    patch = {"replace": [{
        "id": "presentation::renderer_builtin",
        "config": {"disabled_handlers": ["code"], "disabled_filters": ["stream_optimizer"]},
    }]}
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(patch), encoding="utf-8")
    kernel = await build_kernel("cli", patch_paths=[str(path)])
    try:
        assert len(builtin_handler_factories()) == 10
        assert len(builtin_filter_factories()) == 2
    finally:
        await shutdown_kernel(kernel)
    assert len(builtin_handler_factories()) == 11
    assert len(builtin_filter_factories()) == 3
