"""渲染器内置 filter 插件化测试。

覆盖：
- 内置 filter 注册表（id + 工厂）；
- AnsiStreamRenderer 从注册表装配内置 filter；
- 禁用 API（disable_builtin_filters）；
- renderer_builtin 插件按清单 config 禁用内置项（Fiber 卸载恢复）。
"""

from __future__ import annotations

import json

import pytest

from src.renderer.extensions import (
    builtin_filter_factories,
    builtin_filter_ids,
    disable_builtin_filters,
)
from src.plugins.bootstrap import build_kernel, shutdown_kernel


def test_builtin_filter_ids_and_order():
    assert builtin_filter_ids() == [
        "code_block_batcher", "heading_anchor", "stream_optimizer",
    ]


def test_ansi_renderer_assembles_builtin_filters():
    from src.renderer.ansi import AnsiStreamRenderer
    from src.renderer.pipeline import CodeBlockBatcher
    from src.renderer.pipeline_filters import HeadingAnchorFilter, TokenStreamOptimizer

    renderer = AnsiStreamRenderer(width=60)
    kinds = [type(f) for f in renderer._pipeline._filters]
    assert CodeBlockBatcher in kinds
    assert HeadingAnchorFilter in kinds
    assert TokenStreamOptimizer in kinds
    renderer.close()


def test_disable_builtin_filter_api():
    undo = disable_builtin_filters(["stream_optimizer"])
    try:
        assert len(builtin_filter_factories()) == 2
        assert len(builtin_filter_ids()) == 3
    finally:
        undo()
    assert len(builtin_filter_factories()) == 3


def test_disable_builtin_filter_unknown_id():
    with pytest.raises(KeyError):
        disable_builtin_filters(["nope"])


async def test_renderer_builtin_plugin_disable(tmp_path):
    patch = {"replace": [{
        "id": "presentation::renderer_builtin",
        "config": {"disabled_filters": ["stream_optimizer"]},
    }]}
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(patch), encoding="utf-8")
    kernel = await build_kernel("cli", patch_paths=[str(path)])
    try:
        assert len(builtin_filter_factories()) == 2
    finally:
        await shutdown_kernel(kernel)
    assert len(builtin_filter_factories()) == 3
