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


# ═══════════════════════════════════════════════════════════════
# 4. Bug 1 回归测试 — TestAtomicAutoSave
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAtomicAutoSave:
    """★ Bug 1 回归: _auto_save 原子快照三字段打包一致性。

    验证 (snapshot, snapshot_model, snapshot_sid) 三字段在同一赋值语句中
    打包，利用 GIL 字节码原子性消除竞态窗口。
    """

    async def test_auto_save_calls_save_session_with_correct_args(self, session):
        """_auto_save 将非 system 消息、model、session_id 正确传递给 save_session"""
        session._agent.messages.append({"role": "user", "content": "hello"})
        session._state.session_id = "test-sid-bug1"

        with patch("src.core.session.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = "test-sid-bug1"

            result = await session._auto_save()

            assert result == "test-sid-bug1"
            mock_thread.assert_awaited_once()
            # to_thread(func, non_system, snapshot_model, snapshot_sid)
            _func, non_system, model, sid = mock_thread.await_args.args
            assert non_system == [{"role": "user", "content": "hello"}]
            assert model == "test-model"
            assert sid == "test-sid-bug1"

    async def test_auto_save_with_system_messages_filters_correctly(self, session):
        """system 消息应被过滤掉，不传给 save_session"""
        session._agent.messages.append({"role": "system", "content": "You are helpful"})
        session._agent.messages.append({"role": "user", "content": "hi"})
        session._state.session_id = "test-sid-filter"

        with patch("src.core.session.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = "test-sid-filter"

            result = await session._auto_save()

            assert result == "test-sid-filter"
            mock_thread.assert_awaited_once()
            _func, non_system, model, sid = mock_thread.await_args.args
            # system 消息不应出现在 non_system 中
            assert all(m.get("role") != "system" for m in non_system)
            assert non_system == [{"role": "user", "content": "hi"}]

    async def test_auto_save_no_non_system_skips_save_session(self, session):
        """没有非 system 消息时不调用 save_session"""
        session._agent.messages.append({"role": "system", "content": "You are helpful"})
        session._state.session_id = "test-sid-empty"

        with patch("src.core.session.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            result = await session._auto_save()

            assert result == "test-sid-empty"  # 返回 session_id 即使没有保存
            mock_thread.assert_not_awaited()

    async def test_auto_save_exception_returns_none(self, session):
        """_auto_save 异常时返回 None，不抛出"""
        session._agent.messages.append({"role": "user", "content": "will crash"})
        session._state.session_id = "test-sid-crash"

        with patch("src.core.session.asyncio.to_thread",
                   side_effect=RuntimeError("IO error")):
            result = await session._auto_save()

            assert result is None  # 异常时返回 None

    async def test_snapshot_is_copy_independent_of_original(self, session):
        """snapshot 是 messages 的副本，后续修改不影响已保存的快照"""
        session._agent.messages.append({"role": "user", "content": "original"})
        session._state.session_id = "test-sid-copy"

        captured_non_system = None

        async def capture_args(func, *args):
            nonlocal captured_non_system
            captured_non_system = list(args[0])  # non_system 参数
            return args[2]  # session_id

        with patch("src.core.session.asyncio.to_thread", side_effect=capture_args):
            await session._auto_save()

            # 修改原始 messages
            session._agent.messages.append({"role": "assistant", "content": "later"})

            # 验证快照不受影响（仍为 1 条）
            assert len(captured_non_system) == 1
            assert captured_non_system[0]["content"] == "original"


# ═══════════════════════════════════════════════════════════════
# 5. Bug 2 回归测试 — TestRollbackProtectsAiContent
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestRollbackProtectsAiContent:
    """★ Bug 2 回归: 异常回滚时保留 AI assistant 消息。

    当 run_round 的 _execute_round 已部分执行（已有 assistant 回复），
    回滚应保留 AI 已生成的内容，不 pop 对应的 user 消息。
    """

    async def _prepare_run_round(self, session):
        """为 run_round 测试准备必要状态

        由于 session fixture 中 session._agent 是 MagicMock，
        其 add_user_message 默认不追加到 messages 列表。
        此方法覆盖 add_user_message 使其实际追加，确保 run_round
        的异常回滚逻辑能正确判断最后消息的角色。
        """
        session._agent.messages = []
        session._agent.add_user_message = lambda content: session._agent.messages.append(
            {"role": "user", "content": content}
        )
        session._state_machine = SessionStateMachine()
        session._state_machine._state = SessionState.IDLE
        return session

    async def test_assistant_message_preserved_on_exception(self, session):
        """最后消息为 assistant 时不 pop user 消息，保留 AI 内容"""
        session = await self._prepare_run_round(session)
        session._ctx_mgr = MagicMock()

        async def mock_execute_round():
            # 模拟 _execute_round 已部分执行：添加 assistant 消息后异常
            session._agent.messages.append({"role": "assistant", "content": "partial AI response"})
            raise RuntimeError("模拟 _execute_round 执行途中异常")

        session._execute_round = AsyncMock(side_effect=mock_execute_round)

        with pytest.raises(RuntimeError):
            await session.run_round("user message")

        # 验证：user 消息和 assistant 消息都应保留
        roles = [m["role"] for m in session._agent.messages]
        assert "user" in roles, "user 消息应被保留"
        assert "assistant" in roles, "assistant 消息应被保留"
        assert len(session._agent.messages) == 2, "两条消息都应存在"

    async def test_user_message_rolled_back_when_not_started(self, session):
        """最后消息为 user（_execute_round 未开始）时回滚 user 消息"""
        session = await self._prepare_run_round(session)
        session._ctx_mgr = MagicMock()

        session._execute_round = AsyncMock(side_effect=RuntimeError("模拟异常"))

        with pytest.raises(RuntimeError):
            await session.run_round("user message")

        # 验证：user 消息被回滚（_execute_round 未执行）
        assert len(session._agent.messages) == 0, "user 消息应被回滚"

    async def test_context_manager_invalidated_on_assistant_rollback(self, session):
        """assistant 路径中 invalidate_cache 被调用"""
        session = await self._prepare_run_round(session)
        session._ctx_mgr = MagicMock()

        async def mock_execute_round():
            session._agent.messages.append({"role": "assistant", "content": "partial"})
            raise RuntimeError("模拟异常")

        session._execute_round = AsyncMock(side_effect=mock_execute_round)

        with pytest.raises(RuntimeError):
            await session.run_round("user message")

        # 验证 assistant 分支调用了 invalidate_cache
        session._ctx_mgr.invalidate_cache.assert_called_once()

    async def test_context_manager_invalidated_on_user_rollback(self, session):
        """user 回滚路径中 invalidate_cache 被调用（notify_messages_removed 也在 user 分支被调用）"""
        session = await self._prepare_run_round(session)
        session._ctx_mgr = MagicMock()

        session._execute_round = AsyncMock(side_effect=RuntimeError("模拟异常"))

        with pytest.raises(RuntimeError):
            await session.run_round("user message")

        # 验证 user 分支调用了 invalidate_cache 和 notify_messages_removed
        session._ctx_mgr.invalidate_cache.assert_called_once()
        session._ctx_mgr.notify_messages_removed.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 6. Bug 3 回归测试 — TestIncrementalCheckpoint
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestIncrementalCheckpoint:
    """★ Bug 3 回归: run_pending_loop 增量 checkpoint。

    每成功处理一条排队消息后立即保存增量 checkpoint，
    save_checkpoint 异常不阻断消息处理。
    """

    async def test_checkpoint_after_each_successful_round(self, session):
        """每条成功处理的排队消息后调用 save_checkpoint"""
        session._state.pending_messages = ["msg1", "msg2", "msg3"]
        session.run_round = AsyncMock(return_value={"interrupted": False})
        session.save_checkpoint = MagicMock()

        await session.run_pending_loop(max_iter=10)

        assert session.save_checkpoint.call_count == 3, (
            f"期望 3 次 save_checkpoint 调用（每条消息一次），实际: {session.save_checkpoint.call_count}"
        )

    async def test_no_checkpoint_on_failed_round(self, session):
        """异常消息后不调用 save_checkpoint（异常前的消息已保存）"""
        call_count = [0]

        async def mock_run_round(msg):
            call_count[0] += 1
            if call_count[0] == 2:  # 第二个消息抛异常
                raise RuntimeError("模拟异常")
            return {"interrupted": False}

        session._state.pending_messages = ["ok1", "crash", "ok2"]
        session.run_round = AsyncMock(side_effect=mock_run_round)
        session.save_checkpoint = MagicMock()

        with pytest.raises(RuntimeError):
            await session.run_pending_loop(max_iter=10)

        # 第一个消息成功 → 1 次 save_checkpoint
        assert session.save_checkpoint.call_count == 1, (
            f"期望 1 次 save_checkpoint（仅第一个成功消息），实际: {session.save_checkpoint.call_count}"
        )

    async def test_save_checkpoint_exception_does_not_block(self, session):
        """save_checkpoint 异常不阻断后续消息处理"""
        session._state.pending_messages = ["msg1", "msg2"]

        save_count = [0]

        def mock_save_checkpoint():
            save_count[0] += 1
            if save_count[0] == 1:  # 第一次保存抛异常
                raise RuntimeError("save_checkpoint IO 错误")

        session.run_round = AsyncMock(return_value={"interrupted": False})
        session.save_checkpoint = MagicMock(side_effect=mock_save_checkpoint)

        # 不应抛出异常
        result = await session.run_pending_loop(max_iter=10)

        assert save_count[0] == 2, "两个消息都应尝试 save_checkpoint"
        assert result == (False, []), "所有消息应处理完毕"

    async def test_pending_loop_no_messages_no_checkpoint(self, session):
        """没有排队消息时不调用 save_checkpoint"""
        session.save_checkpoint = MagicMock()

        result = await session.run_pending_loop(max_iter=10)

        assert result == (False, [])
        session.save_checkpoint.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# 7. Bug 6 回归测试 — TestContextManagerSyncAfterRollback
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestContextManagerSyncAfterRollback:
    """★ Bug 6 回归: 异常回滚后 context_manager 缓存失效。

    验证 run_round 异常回滚 pop user 消息后 invalidate_cache() 被正确调用，
    且下次访问 _ensure_cache 时自动重新同步。
    """

    async def _setup_for_run_round(self, session):
        """统一为 run_round 测试设置 add_user_message mock"""
        session._agent.add_user_message = lambda content: session._agent.messages.append(
            {"role": "user", "content": content}
        )
        return session

    async def test_invalidate_cache_called_on_user_rollback(self, session):
        """user 回滚路径中 invalidate_cache 被调用"""
        await self._setup_for_run_round(session)
        session._state_machine = SessionStateMachine()
        session._state_machine._state = SessionState.IDLE
        session._ctx_mgr = MagicMock()
        session._agent.messages = []

        session._execute_round = AsyncMock(side_effect=RuntimeError("模拟异常"))

        with pytest.raises(RuntimeError):
            await session.run_round("test")

        session._ctx_mgr.invalidate_cache.assert_called_once()

    async def test_invalidate_cache_called_on_assistant_rollback(self, session):
        """assistant 路径中 invalidate_cache 也被调用"""
        await self._setup_for_run_round(session)
        session._state_machine = SessionStateMachine()
        session._state_machine._state = SessionState.IDLE
        session._ctx_mgr = MagicMock()
        session._agent.messages = []

        async def mock_execute_round():
            session._agent.messages.append({"role": "assistant", "content": "partial"})
            raise RuntimeError("模拟异常")

        session._execute_round = AsyncMock(side_effect=mock_execute_round)

        with pytest.raises(RuntimeError):
            await session.run_round("test")

        session._ctx_mgr.invalidate_cache.assert_called_once()

    async def test_ctx_mgr_none_does_not_crash(self, session):
        """_ctx_mgr 为 None 时不崩溃"""
        await self._setup_for_run_round(session)
        session._state_machine = SessionStateMachine()
        session._state_machine._state = SessionState.IDLE
        session._ctx_mgr = None
        session._agent.messages = []

        session._execute_round = AsyncMock(side_effect=RuntimeError("模拟异常"))

        with pytest.raises(RuntimeError):
            await session.run_round("test")

        # 无异常即为通过

    async def test_notify_messages_removed_called(self, session):
        """user 回滚路径中 notify_messages_removed 被调用"""
        await self._setup_for_run_round(session)
        session._state_machine = SessionStateMachine()
        session._state_machine._state = SessionState.IDLE
        session._ctx_mgr = MagicMock()
        session._agent.messages = []

        session._execute_round = AsyncMock(side_effect=RuntimeError("模拟异常"))

        with pytest.raises(RuntimeError):
            await session.run_round("test")

        session._ctx_mgr.notify_messages_removed.assert_called_once()


