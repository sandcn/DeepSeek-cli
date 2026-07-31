"""渲染状态关闭/重开回归测试（步骤 3 渲染状态层兜底）。

覆盖：
- content 关闭后 get_content() 返回 None（不重建不错位）
- reopen_content() 后惰性重建（多轮会话语义保留）
- _do_main_phase("answering"/"thinking") 触发 reopen_content（先于新内容渲染）
- close_reasoning 后 get_reasoning 返回 None（既有行为回归）
- 渲染器关闭后到达的内容仅显式丢弃（debug 日志），不抛异常、不重建
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.tui._const import MainPhaseCmd, ContentCmd, ReasoningCmd
from src.tui.state.render_state import ChatRenderState


class TestRenderStateCloseReopen:
    """ChatRenderState content 关闭/重开语义。"""

    def test_get_content_after_close_returns_none(self):
        """close_content 后 get_content 返回 None 且不重建渲染器。"""
        rs = ChatRenderState()
        rs.set_output_adapter(MagicMock())
        with patch("src.renderer.IncrementalRenderer") as mock_renderer:
            r1 = rs.get_content()
            assert r1 is not None
            rs.close_content()
            r2 = rs.get_content()
            assert r2 is None
        # 仅构造一次：关闭后不重建（不错位）
        assert mock_renderer.call_count == 1

    def test_reopen_content_rebuilds(self):
        """close 后 reopen_content 再 get_content 惰性重建（多轮会话语义）。"""
        rs = ChatRenderState()
        rs.set_output_adapter(MagicMock())
        with patch("src.renderer.IncrementalRenderer") as mock_renderer:
            r1 = rs.get_content()
            assert r1 is not None
            rs.close_content()
            assert rs.get_content() is None
            rs.reopen_content()
            r2 = rs.get_content()
            assert r2 is not None
        # 重开后惰性重建：渲染器构造恰 2 次（关闭后不重建、重开后重建）
        assert mock_renderer.call_count == 2

    def test_reopen_content_idempotent(self):
        """reopen_content 未关闭时调用无副作用（幂等）。"""
        rs = ChatRenderState()
        rs._content_closed = False
        rs.reopen_content()
        assert rs._content_closed is False

    def test_get_reasoning_closed_returns_none(self):
        """close_reasoning 后 get_reasoning 返回 None（既有行为回归）。"""
        rs = ChatRenderState()
        rs.set_output_adapter(MagicMock())
        with patch("src.renderer.IncrementalRenderer"):
            r1 = rs.get_reasoning()
            assert r1 is not None
            rs.close_reasoning()
            assert rs.get_reasoning() is None

    def test_close_all_idempotent(self):
        """close_all 幂等（reasoning/content 均关闭，重复调用不抛异常）。"""
        rs = ChatRenderState()
        rs.set_output_adapter(MagicMock())
        with patch("src.renderer.IncrementalRenderer"):
            rs.get_content()
            rs.get_reasoning()
            rs.close_all()
            rs.close_all()
        assert rs.get_content() is None
        assert rs.get_reasoning() is None


class TestRenderStateRendererIntegration:
    """TuiRenderer 与渲染状态的集成语义。"""

    def test_do_main_phase_answering_reopens(self):
        """MainPhaseCmd("answering") 触发 reopen_content（先于新内容渲染）。"""
        from src.tui._renderer import TuiRenderer

        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(MainPhaseCmd(phase="answering"))
        rs.reopen_content.assert_called_once()
        bb.set_main_phase.assert_called_once_with("answering")

    def test_do_main_phase_thinking_reopens(self):
        """MainPhaseCmd("thinking") 同时触发 reopen_reasoning 与 reopen_content。"""
        from src.tui._renderer import TuiRenderer

        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(MainPhaseCmd(phase="thinking"))
        rs.reopen_reasoning.assert_called_once()
        rs.reopen_content.assert_called_once()
        bb.set_main_phase.assert_called_once_with("thinking")

    def test_do_content_closed_drops_with_log(self):
        """content 渲染器关闭后到达的内容显式丢弃（debug 日志），不抛异常。"""
        from src.tui._renderer import TuiRenderer

        rs = MagicMock()
        rs.get_content.return_value = None
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        # 不抛异常
        renderer.render(ContentCmd(text="x"))
        rs.get_content.assert_called_once()
        adapter.write.assert_not_called()

    def test_do_reasoning_closed_drops_with_log(self):
        """推理渲染器关闭后到达的文本显式丢弃（debug 日志），不抛异常。"""
        from src.tui._renderer import TuiRenderer

        rs = MagicMock()
        rs.get_reasoning.return_value = None
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        # 不抛异常
        renderer.render(ReasoningCmd(text="x"))
        rs.get_reasoning.assert_called_once()
        adapter.write.assert_not_called()
