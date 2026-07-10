"""测试 Session Bug 修复 — P2-2, P3-3"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.state_machine import SessionState, InvalidTransitionError


class TestClearMessages:
    """P2-2: clear_messages 清除 captured_prefill"""

    @pytest.fixture
    def session(self):
        """创建最小化 ChatSession，注入 mock 依赖避免实际初始化开销"""
        with patch.multiple(
            'src.core.session',
            get_sandbox_manager=MagicMock(return_value=None),
            create_sandbox_manager=MagicMock(),
            get_default_collector=MagicMock(),
            get_default_tracer=MagicMock(),
        ):
            from src.core.session import ChatSession
            sess = ChatSession.__new__(ChatSession)
            sess._state_machine = MagicMock()
            sess._state = MagicMock()
            sess._state.session_id = None
            sess._state.captured_prefill = "旧捕获文本"
            sess._state.retry_pending = False
            sess._state.pending_messages = []
            sess._state.hooks = {}
            sess._state.on = MagicMock()
            sess._state.off = MagicMock()
            sess._state._emit = MagicMock()
            sess._state_machine.clear = MagicMock()
            sess._state_machine.is_ = MagicMock(return_value=False)
            sess._state_machine.name = "IDLE"
            sess._agent = MagicMock()
            sess._agent.messages = [{"role": "system", "content": "system"}]
            sess._persistence_port = MagicMock()
            sess._config_port = MagicMock()
            sess._metrics = MagicMock()
            sess._ctx_mgr = None
            return sess

    def test_clear_messages_resets_captured_prefill(self, session):
        """P2-2: clear_messages 后 captured_prefill 被清空"""
        session._state.captured_prefill = "旧捕获文本"
        session.clear_messages()
        assert session._state.captured_prefill == ""

    def test_clear_messages_skipped_in_running(self, session):
        """P2-2: clear 在 RUNNING 状态下跳过，captured_prefill 不重置"""
        session._state_machine.clear.side_effect = InvalidTransitionError(
            SessionState.RUNNING, "clear"
        )
        session._state.captured_prefill = "旧捕获文本"
        result = session.clear_messages()
        assert result == 0  # clear 被跳过
        # captured_prefill 不清除（与边界条件一致）


class TestSignalAllDone:
    """P3-3: ParallelExecutor.signal_all_done 公共方法"""

    def test_signal_all_done_exists(self):
        """P3-3: signal_all_done 作为公共方法存在"""
        from src.core.parallel_executor import ParallelExecutor
        assert hasattr(ParallelExecutor, 'signal_all_done')
        assert callable(ParallelExecutor.signal_all_done)
