"""test_subdomain_facades — 子域聚合门面架构固化（方向C，2026-08-05）。

方向C（顶层模块归类）执行结论：``_input*`` / ``_subagent_*`` 实现文件
保持顶层（`input.py` 门面命名冲突 + 测试 patch 路径依赖 + 引用面大），
以**子域聚合门面**提供统一入口。本测试固化：
  - ``src.tui.subagent`` 聚合门面存在且可导入
  - 门面 re-export 与顶层模块同一对象（单一真源，防双实现漂移）
  - 门面覆盖「控制器/渲染/状态」三域公开符号
"""

from __future__ import annotations

from pathlib import Path


def _src_tui() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "tui"


class TestSubagentFacadeExists:
    """subagent/ 聚合门面。"""

    def test_package_dir_exists(self) -> None:
        assert (_src_tui() / "subagent").is_dir()
        assert (_src_tui() / "subagent" / "__init__.py").is_file()

    def test_facade_import_works(self) -> None:
        from src.tui.subagent import (
            SubAgentPanelController,
            StateStore,
            render_frame,
            build_agent_lines,
            format_tool_record,
            _get_tool_color,
            _SPINNER_FRAMES,
        )
        assert callable(SubAgentPanelController)
        assert callable(render_frame)
        assert callable(build_agent_lines)

    def test_facade_single_source(self) -> None:
        """门面符号与顶层模块同一对象（单一真源）。"""
        import src.tui.subagent as sub
        from src.tui import _subagent_panel as panel
        from src.tui import _subagent_state as state
        from src.tui import _subagent_render as render

        assert sub.SubAgentPanelController is panel.SubAgentPanelController
        assert sub.StateStore is state.StateStore
        assert sub._AgentSlot is state._AgentSlot
        assert sub._ToolRecord is state._ToolRecord
        assert sub.render_frame is render.render_frame
        assert sub.build_agent_lines is render.build_agent_lines
        assert sub.format_tool_record is render.format_tool_record
        assert sub._get_tool_color is render._get_tool_color
        assert sub._SPINNER_FRAMES is render._SPINNER_FRAMES

    def test_facade_all_complete(self) -> None:
        """门面 __all__ 完整覆盖三域公开符号。"""
        import src.tui.subagent as sub
        for name in (
            "SubAgentPanelController", "StateStore", "_AgentSlot", "_ToolRecord",
            "render_frame", "build_agent_lines", "format_tool_record",
            "_get_tool_color", "_SPINNER_FRAMES",
        ):
            assert name in sub.__all__, f"subagent 门面 __all__ 缺失 {name}"


class TestInputDomainStaysTopLevel:
    """input 子域保持顶层（input.py 门面命名冲突约束）。"""

    def test_input_facade_reexport_kept(self) -> None:
        """input.py 门面保持（Input/KeyEvent 旧导入路径兼容）。"""
        import src.tui.input as input_facade
        from src.tui import _input
        from src.tui import _input_parser
        assert input_facade.Input is _input.Input
        assert input_facade.KeyEvent is _input_parser.KeyEvent

    def test_no_input_package_conflict(self) -> None:
        """不创建 input/ 包目录（与 input.py 门面命名冲突）。"""
        assert not (_src_tui() / "input" / "__init__.py").exists()

    def test_input_metrics_reexport_kept(self) -> None:
        """度量层 re-export 兼容（app.input_area 旧路径）。"""
        from src.tui.app import input_area
        from src.tui import _input_metrics
        assert input_area._completion_height is _input_metrics._completion_height
        assert input_area._is_search_active is _input_metrics._is_search_active
