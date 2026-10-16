"""模态全屏视图高度撑满 —— /sandbox 与 /export 内容不再贴到屏幕底部。

★ 修复回归（2026-10）：模态全屏视图整屏渲染（App 直接把视图组件作为根元素），
文档高度小于终端高度时渲染器按**底部对齐**绘制——内容行数少的视图
（空闲沙盒约 11 行、导出向导约 9 行）会贴到屏幕底部、顶部留大片空白，与
/mcp、/skill 等内部撑满高度的视图表现不一致。修复：内容区用显式
``height=vh`` 的 Column 撑满视口（文档高度与其它模态视图同构）。

本文件断言渲染出的文档行数 ≥ 视口行数（``viewport_rows()``）——修复前
远小于该值。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_view_registry():
    import src.tui.app.view_registry as vreg

    vreg.reset()
    yield
    vreg.reset()


def _render(model, component, width: int = 100) -> str:
    from src.tui.ink import h, renderToString

    return renderToString(
        h(component, {"model": model, "width": width}), {"columns": width},
    )


def _viewport_rows() -> int:
    from src.tui.app._view_common import viewport_rows

    return viewport_rows()


def test_sandbox_stats_view_fills_viewport_height():
    """空闲沙盒（内容少）也占满视口高度 → 顶部对齐渲染（不再贴底）。"""
    from src.tui.app._state_types import SandboxStatsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    model = AppModel()
    model.sandbox_view = SandboxStatsViewState(visible=True, seq=1, sections=[])
    out = _render(model, SandboxStatsView)

    lines = out.splitlines()
    assert lines and "文件沙盒" in lines[0]
    assert len(lines) >= _viewport_rows()


def test_sandbox_stats_view_with_sections_fills_viewport_height():
    from src.tui.app._state_types import SandboxStatsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    model = AppModel()
    model.sandbox_view = SandboxStatsViewState(
        visible=True, seq=1,
        sections=[{"title": "概览", "rows": [("文件数", "0", "info", None)]}],
    )
    out = _render(model, SandboxStatsView)
    assert len(out.splitlines()) >= _viewport_rows()


def test_sandbox_stats_view_help_fills_viewport_height():
    from src.tui.app._state_types import SandboxStatsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    model = AppModel()
    model.sandbox_view = SandboxStatsViewState(visible=True, seq=1, help_open=True)
    out = _render(model, SandboxStatsView)
    assert len(out.splitlines()) >= _viewport_rows()


def test_export_view_fills_viewport_height():
    """导出向导（表体约 9 行）占满视口高度 → 顶部对齐渲染。"""
    from src.tui.app._state_types import ExportViewState
    from src.tui.app.export_view import ExportView
    from src.tui.app.model import AppModel

    model = AppModel()
    model.export_view = ExportViewState(visible=True, seq=1, format="md")
    out = _render(model, ExportView)

    lines = out.splitlines()
    assert "导出向导" in lines[0]
    assert len(lines) >= _viewport_rows()
    assert any("执行导出" in line for line in lines)
