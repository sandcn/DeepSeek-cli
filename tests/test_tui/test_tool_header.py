"""test_tool_header — ToolStatusHeader 组件测试（Claude TUI parity 步骤 2.1）。

覆盖：active_tool=None 不占行、running 显示边框标题（图标+名称+参数摘要）、
宽度截断、done/fail 不显示。
"""

from __future__ import annotations

from src.tui.app.model import AppModel
from src.tui.app.tool_header import _build_header_runs
from src.tui.ink import h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.element import BOX, TEXT
from src.tui.ink.components import render_frame


def _render(model, width=40):
    """渲染 ToolStatusHeader，返回 (布局高度, 纯文本行列表)。"""
    from src.tui.app.tool_header import ToolStatusHeader
    r = Reconciler()
    root = r.create_root()
    r.render(root, h(ToolStatusHeader, {"model": model, "width": width}), width, 24)
    frame = render_frame(root, width)
    return [line.plain for line in frame.lines]


class TestToolStatusHeader:
    def test_no_active_tool_no_lines(self):
        """active_tool=None → 不占行（无边框内容）。"""
        model = AppModel()
        lines = _render(model)
        assert not any("┌" in l or "┐" in l for l in lines)

    def test_running_shows_border_title(self):
        """status=running → 显示 open 边框标题（含图标与工具名缩写）。"""
        model = AppModel()
        model.open_tool_box("t1", "bash", detail="ls -la")
        lines = _render(model, width=40)
        # bash 缩写 bs，图标 ⚡
        assert any(l.startswith("┌─ ⚡ bs") for l in lines)
        assert any("ls -la" in l for l in lines)

    def test_closed_tool_hides_header(self):
        """工具关闭（done/fail）后 header 隐藏。"""
        model = AppModel()
        model.open_tool_box("t1", "bash", "ls")
        model.close_tool_box("t1", True)
        lines = _render(model)
        assert not any("┌" in l for l in lines)

    def test_build_header_runs_icon_and_name(self):
        """标题 runs 含工具图标与显示名（bash → ⚡ + bs）。"""
        from src.tui.app._theme import ThemeRegistry
        runs = _build_header_runs(
            {"tool_name": "bash", "name": "bs", "detail": "", "status": "running"},
            ThemeRegistry.resolve("dark"),
        )
        text = "".join(r.text for r in runs)
        assert "⚡" in text
        assert "bs" in text

    def test_width_truncation(self):
        """超长参数摘要不撑爆边框（宽度受限于 width）。"""
        model = AppModel()
        model.open_tool_box("t1", "read_file", detail="x" * 200)
        for line in _render(model, width=20):
            assert len(line) <= 20
