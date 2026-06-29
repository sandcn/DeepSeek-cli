"""测试 ChatSession — 聚焦 run_pending_loop 和 _force_state_recovery 修复

覆盖内容：
  1. run_pending_loop 使用 enumerate 避免 O(n²) 和 index 误匹配
  2. _force_state_recovery 方法存在性检查
  3. 所有 except BaseException 有日志记录
"""

import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.core.session import ChatSession
from src.core.state_machine import SessionStateMachine, SessionState, InvalidTransitionError


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


@pytest.fixture
def state_machine():
    """纯状态机实例（不含会话）"""
    return SessionStateMachine()


# ===============================================================
# 1. run_pending_loop — enumerate 修复
# ===============================================================

@pytest.mark.asyncio
class TestRunPendingLoopEnumerate:
    """验证 run_pending_loop 用 enumerate 替代 pending.index(msg)"""

    async def test_exception_remaining_correct(self, session):
        """异常时 remaining 使用 i+1 而非 index(msg)，确保即使有重复消息也正确"""
        # 构造 _pending_messages 有重复项的场景
        session._state.pending_messages = ["msg_A", "msg_B", "msg_A"]  # 重复 msg_A

        # mock run_round 在第二个 msg_A 上抛异常
        call_count = 0

        async def mock_run_round(msg):
            nonlocal call_count
            call_count += 1
            if call_count == 3:  # 第三个消息 (第二个 "msg_A")
                raise RuntimeError("模拟异常")

        session.run_round = AsyncMock(side_effect=mock_run_round)

        # 执行
        with pytest.raises(RuntimeError):
            await session.run_pending_loop(max_iter=10)

        # 验证：异常时 remaining = pending[i+1:] 应只包含后续消息
        # pending = ["msg_A", "msg_B", "msg_A"], i=2 时异常 → remaining = []
        assert len(session._state.pending_messages) == 0, (
            "第三个消息异常时 i=2，remaining = pending[3:] = []，不应有消息重新入队"
        )

    async def test_enumerate_index_matches_order(self, session):
        """enumerate 的索引与消息顺序一致，确保剩余消息计算正确"""
        session._state.pending_messages = ["first", "second", "third"]

        call_log = []

        async def mock_run_round(msg):
            call_log.append(msg)
            if msg == "second":
                raise ValueError("中段异常")

        session.run_round = AsyncMock(side_effect=mock_run_round)

        # 执行：第二个消息抛异常
        with pytest.raises(ValueError):
            await session.run_pending_loop(max_iter=10)

        # pending = ["first", "second", "third"]
        # 第二个消息(i=1)异常 → remaining = pending[2:] = ["third"]
        # 应被重新放回 _pending_messages
        assert session._state.pending_messages == ["third"], (
            f"期望 remaining=['third']，实际={session._state.pending_messages}"
        )


# ===============================================================
# 2. _force_state_recovery — 方法存在性检查
# ===============================================================

class TestForceStateRecovery:
    """验证 _force_state_recovery 的方法存在性检查"""

    def test_method_not_found_logs_warning(self, session, caplog):
        """当状态机缺少某个方法时记录 warning 日志"""
        # 将状态机置于 RUNNING 状态
        sm = MagicMock(spec=SessionStateMachine)
        sm.name = "RUNNING"
        sm.is_.return_value = False  # 既不是 IDLE 也不是 INIT

        # 模拟方法不存在 — 用实例属性 None 覆盖 spec 的自动 mock
        sm.complete_round = None

        # 移除 complete_round 然后模拟 _state_machine
        session._state_machine = sm

        with caplog.at_level(logging.WARNING):
            session._force_state_recovery()

            warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
            method_warnings = [m for m in warning_messages if "不存在于状态机" in m]
            assert len(method_warnings) == 1, (
                f"期望 1 条方法不存在警告，实际: {method_warnings}"
            )

    def test_force_recovery_logs_debug_for_invalid_transition(self, session, caplog):
        """InvalidTransitionError 记录 debug 日志"""
        # 将状态机置于 RUNNING 状态
        sm = MagicMock(spec=SessionStateMachine)
        sm.name = "RUNNING"
        sm.is_.return_value = False

        # complete_round / interrupt / clear / save 抛出 InvalidTransitionError
        # reset 是保底方案，不抛出（实践中 reset() 总能回到 INIT）
        sm.complete_round.side_effect = InvalidTransitionError(
            SessionState.RUNNING, SessionState.IDLE
        )
        sm.interrupt.side_effect = InvalidTransitionError(
            SessionState.RUNNING, SessionState.IDLE
        )
        sm.clear.side_effect = InvalidTransitionError(
            SessionState.RUNNING, SessionState.IDLE
        )
        sm.save.side_effect = InvalidTransitionError(
            SessionState.RUNNING, SessionState.IDLE
        )
        # reset 不抛异常（保底方案）

        session._state_machine = sm

        with caplog.at_level(logging.DEBUG):
            session._force_state_recovery()

            debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
            invalid_msgs = [m for m in debug_messages if "转换无效" in m]
            assert len(invalid_msgs) == 4, (
                f"期望 4 条转换无效 debug 日志（complete/interrupt/clear/save），"
                f"实际: {len(invalid_msgs)} — {invalid_msgs}"
            )


# ===============================================================
# 3. except BaseException 日志记录
# ===============================================================

@pytest.mark.asyncio
class TestBaseExceptionLogging:
    """验证所有 except BaseException 块都有日志记录"""

    async def test_run_round_add_user_message_exception_logged(self, session, caplog):
        """run_round 中 add_user_message 的 except 记录异常日志"""
        # 直接测试：构造一个会抛异常的 add_user_message
        session._agent.add_user_message = MagicMock(
            side_effect=ValueError("模拟 add_user_message 异常")
        )

        # 需要状态机允许 start_round
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                await session.run_round("测试输入")

            log_messages = [r.message for r in caplog.records]
            add_msg_logs = [m for m in log_messages if "add_user_message 异常" in m]
            assert len(add_msg_logs) >= 1, (
                f"期望有 add_user_message 异常日志，实际日志: {log_messages}"
            )

    async def test_run_round_execute_round_exception_logged(self, session, caplog):
        """run_round 中 _execute_round 的 except 记录异常日志"""
        session._execute_round = AsyncMock(
            side_effect=RuntimeError("模拟 _execute_round 异常")
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError):
                await session.run_round("测试输入")

            log_messages = [r.message for r in caplog.records]
            exec_logs = [m for m in log_messages if "_execute_round 异常" in m]
            assert len(exec_logs) >= 1, (
                f"期望有 _execute_round 异常日志，实际日志: {log_messages}"
            )

    async def test_retry_exception_logged(self, session, caplog):
        """retry 中 _execute_round 的 except 记录异常日志"""
        session._execute_round = AsyncMock(
            side_effect=RuntimeError("模拟 retry 异常")
        )

        with caplog.at_level(logging.ERROR):
            # retry 需要先有 start_round 使状态进入可 retry 的状态
            # 直接 mock 状态机允许 retry
            session._state_machine.retry = MagicMock()
            with pytest.raises(RuntimeError):
                await session.retry()

            log_messages = [r.message for r in caplog.records]
            retry_logs = [m for m in log_messages if "retry: _execute_round 异常" in m]
            assert len(retry_logs) >= 1, (
                f"期望有 retry 异常日志，实际日志: {log_messages}"
            )

    async def test_run_single_exception_logged(self, session, caplog):
        """run_single 中 _execute_round 的 except 记录异常日志"""
        session._execute_round = AsyncMock(
            side_effect=RuntimeError("模拟 run_single 异常")
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError):
                await session.run_single("测试输入")

            log_messages = [r.message for r in caplog.records]
            single_logs = [m for m in log_messages if "run_single: _execute_round 异常" in m]
            assert len(single_logs) >= 1, (
                f"期望有 run_single 异常日志，实际日志: {log_messages}"
            )



