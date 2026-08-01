"""test_render_integration — Render 线程 stdin 集成测试。

验证 InkSession._drain_queue() 在每帧渲染周期中正确调用
Input.process_events() → read_stdin_once()，
且 stdin 读取在 output_lock 之外执行。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.tui.input import Input
from src.tui.ink.session import InkSession
from src.tui.app.model import AppModel


class TestRenderIntegration:
    """测试 InkSession._drain_queue() 与 Input.read_stdin_once() 的集成。"""

    @pytest.fixture
    def mock_input(self, tmp_path: Path) -> Input:
        """创建 mock Input 实例。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            yield inp
        finally:
            os.close(fd)

    def test_drain_queue_calls_process_events(self, mock_input: Input) -> None:
        """验证 InkSession._drain_queue() 调用 Input.process_events()。"""
        session = InkSession(model=AppModel())
        mock_input.process_events = MagicMock()
        session._input = mock_input
        session._phase_process_input()
        mock_input.process_events.assert_called_once()

    def test_drain_queue_without_input_no_crash(self) -> None:
        """验证 InkSession._drain_queue() 在无 _input 时不崩溃（向后兼容）。"""
        session = InkSession(model=AppModel())
        session._input = None
        session._phase_process_input()  # 不应抛异常

    def test_process_events_calls_read_stdin_once(self, mock_input: Input, tmp_path: Path) -> None:
        """验证 Input.process_events() 委托 read_stdin_once()。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "test_history")
            os.write(w_fd, b"x")
            time.sleep(0.05)
            inp.process_events()
            assert inp.get_current_text() == "x"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_stdin_read_outside_output_lock(self) -> None:
        """验证 _phase_process_input() 在 output_lock 之前执行。"""
        import inspect

        source = inspect.getsource(InkSession._drain_queue)
        phase_pos = source.find("_phase_process_input()")
        lock_pos = source.find("_try_acquire_output_lock")
        assert phase_pos >= 0, "_phase_process_input() 应在 _drain_queue 中"
        assert lock_pos >= 0, "_try_acquire_output_lock 应在 _drain_queue 中"
        assert phase_pos < lock_pos, (
            "_phase_process_input() 应在 _try_acquire_output_lock 之前调用"
        )


# ═══════════════════════════════════════════════════════════
# 横切步骤18 — 组件树渲染含新 props（history_search / 工具状态）
# ═══════════════════════════════════════════════════════════

class TestRenderIntegrationNewProps:
    """横切步骤18 — build_app_element 传入新 props 后帧输出正确。"""

    def _render_frame_to_stream(self, model, width=80):
        """渲染一帧到 StringIO，返回 (stream, session)。"""
        import io

        from src.tui.ink.session import InkSession
        from src.tui.app.app import build_app_element
        from src.tui._config import TuiConfig

        cache = MagicMock()
        cache.get_width.return_value = width
        cache.get_height.return_value = 24
        stream = io.StringIO()
        session = InkSession(
            model=model,
            apply_cmd=None,
            build_tree=build_app_element,
            width_cache=cache,
            config=TuiConfig.defaults(),
            stream=stream,
        )
        session._render_frame()
        return stream, session

    def test_history_search_props_renders_search_line_regression(self):
        """history_search 激活时帧输出含 (reverse-i-search) 覆盖行。"""
        from src.tui.app.model import AppModel, HistorySearchState

        model = AppModel()
        model.history_search = HistorySearchState(
            query="foo", matches=["foobar"], index=0, active=True,
        )
        stream, session = self._render_frame_to_stream(model)
        try:
            out = stream.getvalue()
            assert "(reverse-i-search)" in out
            assert "foo" in out
        finally:
            session.stop()

    def test_history_search_inactive_no_search_line_regression(self):
        """history_search 为 None（未激活）时帧输出不含搜索覆盖行。"""
        from src.tui.app.model import AppModel

        model = AppModel()
        model.history_search = None
        stream, session = self._render_frame_to_stream(model)
        try:
            assert "(reverse-i-search)" not in stream.getvalue()
        finally:
            session.stop()

    def test_tool_card_status_props_render_regression(self):
        """工具块折叠（done + 折叠）时帧输出含折叠提示行。"""
        from src.tui._const import ToolOpenCmd, ToolOutputCmd, ToolCloseCmd
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tools.registry import get_tool_display_name

        model = AppModel()
        apply_cmd(model, ToolOpenCmd(tool_id="t9", tool_name="bash"))
        for i in range(10):
            apply_cmd(model, ToolOutputCmd(tool_id="t9", text=f"line{i}"))
        apply_cmd(model, ToolCloseCmd(tool_id="t9", success=True))

        stream, session = self._render_frame_to_stream(model)
        try:
            out = stream.getvalue()
            # 折叠提示行渲染到帧输出
            assert "已折叠" in out
            # 工具显示名标题渲染（工具名经 registry 显示名映射）
            display = get_tool_display_name("bash") or "bash"
            assert display in out
        finally:
            session.stop()
