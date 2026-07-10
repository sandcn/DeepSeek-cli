"""测试 ChatSession._emit_round_events 中 save_checkpoint 的异常保护

覆盖内容：
1. save_checkpoint 失败时 round_end 事件仍正常发射
2. save_checkpoint 异常被记录但不传播
3. 正常情况下的行为不变
"""

import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from src.core.session import ChatSession
from src.core.state_machine import SessionStateMachine, SessionState


# ===============================================================
# Fixtures
# ===============================================================

@pytest.fixture
def session():
    """创建一个最小化 ChatSession 实例（mock Agent 以避免外部依赖）"""
    with patch("src.core.session.Agent") as MockAgent:
        mock_agent = MagicMock()
        mock_agent.messages = []
        mock_agent.pipeline.async_middlewares = []
        MockAgent.return_value = mock_agent

        with patch("src.core.session.create_sandbox_manager"):
            with patch("src.core.session.get_sandbox_manager"):
                s = ChatSession(model="test-model")
                # 模拟 initialize 完成后的状态
                s._state_machine = SessionStateMachine()
                s._state_machine._state = SessionState.IDLE
                yield s


# ===============================================================
# _emit_round_events — save_checkpoint 异常保护
# ===============================================================

class TestEmitRoundEventsCheckpointProtection:
    """验证 _emit_round_events 中 save_checkpoint 的异常保护"""

    def test_save_checkpoint_failure_does_not_block_round_end(self, session):
        """save_checkpoint 失败时 round_end 事件仍正常发射"""
        # 准备
        emitted_events = []
        session._emit = lambda event, **kw: emitted_events.append(event)

        # mock save_checkpoint 抛出异常
        session.save_checkpoint = MagicMock(side_effect=IOError("磁盘空间不足"))

        # 被中断的场景
        interrupted = True
        session_id = "test-session-id"
        delta = {"input": 100, "output": 50, "calls": 1}
        current = {"input": 1000, "output": 500, "calls": 10}

        # 执行 - 不应抛出异常
        result = session._emit_round_events(interrupted, session_id, delta, current)

        # 验证 round_end 事件被发射
        assert "round_end" in emitted_events, "round_end 事件应被发射"
        assert "interrupted" in emitted_events, "interrupted 事件应被发射"
        # 验证 save_checkpoint 被调用
        session.save_checkpoint.assert_called_once()
        # 验证返回结果正确
        assert result["interrupted"] is True
        assert result["session_id"] == session_id

    def test_save_checkpoint_failure_logs_warning(self, session, caplog):
        """save_checkpoint 失败时记录 WARNING 日志"""
        # 准备
        session._emit = MagicMock()
        session.save_checkpoint = MagicMock(side_effect=IOError("磁盘空间不足"))

        # 执行
        with caplog.at_level(logging.WARNING):
            session._emit_round_events(True, "test-id", {"input": 1, "output": 1, "calls": 1}, {})

        # 验证日志
        assert "save_checkpoint 失败" in caplog.text
        assert "磁盘空间不足" in caplog.text

    def test_normal_checkpoint_succeeds(self, session):
        """正常情况：save_checkpoint 成功时行为不变"""
        # 准备
        emitted_events = []
        session._emit = lambda event, **kw: emitted_events.append(event)
        session.save_checkpoint = MagicMock()

        # 执行
        session._emit_round_events(True, "test-id", {"input": 1, "output": 1, "calls": 1}, {})

        # 验证
        session.save_checkpoint.assert_called_once()
        assert "interrupted" in emitted_events
        assert "round_end" in emitted_events

    def test_non_interrupted_round_skips_checkpoint(self, session):
        """非中断场景不调用 save_checkpoint"""
        # 准备
        emitted_events = []
        session._emit = lambda event, **kw: emitted_events.append(event)
        session.save_checkpoint = MagicMock()

        # 执行 - interrupted=False
        session._emit_round_events(False, "test-id", {"input": 1, "output": 1, "calls": 1}, {})

        # 验证 save_checkpoint 未被调用
        session.save_checkpoint.assert_not_called()
        assert "round_end" in emitted_events
        assert "interrupted" not in emitted_events

    def test_pipeline_checkpoint_requested_still_works(self, session):
        """Pipeline CancelledError 标记仍然有效"""
        # 准备
        emitted_events = []
        session._emit = lambda event, **kw: emitted_events.append(event)
        session.save_checkpoint = MagicMock()

        # mock pipeline._last_ctx
        mock_ctx = MagicMock()
        mock_ctx.checkpoint_requested = True
        session._agent.pipeline._last_ctx = mock_ctx

        # 执行
        session._emit_round_events(True, "test-id", {"input": 1, "output": 1, "calls": 1}, {})

        # 验证 save_checkpoint 被调用
        session.save_checkpoint.assert_called_once()
        assert "interrupted" in emitted_events

    def test_pipeline_checkpoint_failure_does_not_block(self, session):
        """Pipeline checkpoint 请求失败时仍继续"""
        # 准备
        emitted_events = []
        session._emit = lambda event, **kw: emitted_events.append(event)
        session.save_checkpoint = MagicMock(side_effect=IOError("IO 错误"))

        # mock pipeline._last_ctx
        mock_ctx = MagicMock()
        mock_ctx.checkpoint_requested = True
        session._agent.pipeline._last_ctx = mock_ctx

        # 执行 - 不应抛出异常
        session._emit_round_events(True, "test-id", {"input": 1, "output": 1, "calls": 1}, {})

        # 验证 round_end 事件被发射
        assert "round_end" in emitted_events
        assert "interrupted" in emitted_events


# ===============================================================
# SessionPersistenceManager.save_checkpoint — 异常保护
# ===============================================================

class TestSessionPersistenceManagerCheckpoint:
    """验证 SessionPersistenceManager.save_checkpoint 的异常保护"""

    def test_save_checkpoint_io_exception_logged_and_reraised(self):
        """IO异常时记录 WARNING 日志并重新抛出"""
        from src.core.internal.session._session_persistence_manager import SessionPersistenceManager

        # 准备
        mock_checkpoint = MagicMock()
        mock_checkpoint.save.side_effect = IOError("磁盘空间不足")

        mgr = SessionPersistenceManager(
            messages_getter=lambda: [],
            model_getter=lambda: "test",
            model_setter=lambda v: None,
            session_id_getter=lambda: None,
            session_id_setter=lambda v: None,
            persistence_port=MagicMock(),
            checkpoint_port=mock_checkpoint,
            state_machine=MagicMock(),
            emit_fn=MagicMock(),
            observability_port=MagicMock(),
        )

        # 执行 - 应抛出异常
        with pytest.raises(IOError, match="磁盘空间不足"):
            mgr.save_checkpoint()

        # 验证 emit 未被调用（保存失败不应发射 checkpoint_saved）
        mgr._emit.assert_not_called()

    def test_save_checkpoint_success_emits_event(self):
        """保存成功时发射 checkpoint_saved 事件"""
        from src.core.internal.session._session_persistence_manager import SessionPersistenceManager

        # 准备
        mock_checkpoint = MagicMock()
        mock_emit = MagicMock()

        mgr = SessionPersistenceManager(
            messages_getter=lambda: [{"role": "user", "content": "test"}],
            model_getter=lambda: "test",
            model_setter=lambda v: None,
            session_id_getter=lambda: None,
            session_id_setter=lambda v: None,
            persistence_port=MagicMock(),
            checkpoint_port=mock_checkpoint,
            state_machine=MagicMock(),
            emit_fn=mock_emit,
            observability_port=MagicMock(),
        )

        # 执行
        mgr.save_checkpoint()

        # 验证
        mock_checkpoint.save.assert_called_once()
        mock_emit.assert_called_once_with("checkpoint_saved")
