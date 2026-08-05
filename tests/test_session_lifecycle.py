"""test_session_lifecycle — 会话生命周期编排逻辑单元测试。

覆盖 _finalize_round / _execute_round 等核心编排函数的异常恢复路径。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest




# ═══════════════════════════════════════════════════════════
# 辅助 Mock 工厂
# ═══════════════════════════════════════════════════════════

def _make_mock_session():
    """创建最小化的 mock session 实例。

    模拟 ChatSession 的关键属性，供 _finalize_round 等函数使用。
    """
    session = MagicMock()
    session._state_machine = MagicMock()
    session._state_machine.is_ = MagicMock(return_value=False)
    session._state_machine.name = "RUNNING"
    session._state_machine.interrupt = MagicMock()
    session._state_machine.complete_round = MagicMock()
    session._state = MagicMock()
    session._state.round_lock = MagicMock()
    session._ctx_mgr = MagicMock()
    session._config_port = MagicMock()
    session._persistence_mgr = MagicMock()
    session._persistence_port = MagicMock()
    session._agent = MagicMock()
    session._agent.messages = []
    session._emit = MagicMock()
    session.save_checkpoint = AsyncMock()
    return session


# ═══════════════════════════════════════════════════════════
# TestFinalizeRoundAutoSaveCancelledError
# ═══════════════════════════════════════════════════════════

class TestFinalizeRoundAutoSaveCancelledError:
    """验证 Bug 1 修复：_auto_save 抛出 CancelledError 时状态机强制恢复。

    Bug 场景：_finalize_round 中 _auto_save 抛出 asyncio.CancelledError，
    状态机残留在 COMPLETED/INTERRUPTED 状态，_ensure_idle() 只处理 INIT，
    下次 start_round() 抛出 InvalidTransitionError 导致死锁。

    修复：在 except (KeyboardInterrupt, asyncio.CancelledError) 分支中，
    先调用 _force_state_recovery(session) 强制恢复状态机至 IDLE，
    再重新抛出 CancelledError，确保取消信号仍可传播。
    """

    @pytest.mark.asyncio
    async def test_auto_save_cancelled_error_triggers_recovery(self):
        """_auto_save 抛出 CancelledError → _force_state_recovery 被调用。

        验证：
        1. _force_state_recovery 在 raise 之前被调用（恢复状态机）
        2. CancelledError 仍传播（raise 语义不变）
        3. _force_state_recovery 调用早于异常传播
        """
        from src.core.internal.session._session_lifecycle import _finalize_round

        session = _make_mock_session()

        # 构造前置参数
        interrupted = True
        prev_stats = (0, 0, 0)

        # mock _auto_save 抛出 CancelledError
        with patch(
            "src.core.internal.session._session_lifecycle._auto_save",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ) as mock_auto_save:
            with patch(
                "src.core.internal.session._session_lifecycle._force_state_recovery",
            ) as mock_force_recovery:
                # 执行 _finalize_round，预期抛出 CancelledError
                with pytest.raises(asyncio.CancelledError):
                    await _finalize_round(session, interrupted, prev_stats)

                # 验证 _auto_save 被调用
                mock_auto_save.assert_awaited_once()

                # 验证 _force_state_recovery 被调用（在 raise 之前）
                mock_force_recovery.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_auto_save_cancelled_error_does_not_block_raise(self):
        """CancelledError 仍被重新抛出，不因 _force_state_recovery 而吞没。

        确保修复不改变 raise 语义，取消信号仍可传播至调用方。
        """
        from src.core.internal.session._session_lifecycle import _finalize_round

        session = _make_mock_session()
        interrupted = True
        prev_stats = (0, 0, 0)

        # mock _force_state_recovery 正常执行
        with patch(
            "src.core.internal.session._session_lifecycle._auto_save",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ):
            with patch(
                "src.core.internal.session._session_lifecycle._force_state_recovery",
                return_value=None,
            ):
                # 验证 CancelledError 被传播
                with pytest.raises(asyncio.CancelledError):
                    await _finalize_round(session, interrupted, prev_stats)

    @pytest.mark.asyncio
    async def test_auto_save_normal_path_unaffected(self):
        """_auto_save 正常返回时，_force_state_recovery 不被调用。

        验证修复不影响正常路径（无 CancelledError 时行为不变）。
        """
        from src.core.internal.session._session_lifecycle import _finalize_round

        session = _make_mock_session()
        # 让 save_checkpoint 正常执行
        session.save_checkpoint = MagicMock()
        # _auto_save 返回 session_id
        session._persistence_mgr.auto_save = AsyncMock(return_value="test-session-id")
        interrupted = False
        prev_stats = (0, 0, 0)

        with patch(
            "src.core.internal.session._session_lifecycle._force_state_recovery",
        ) as mock_force_recovery:
            result = await _finalize_round(session, interrupted, prev_stats)

            # 正常路径下 _force_state_recovery 不被调用
            mock_force_recovery.assert_not_called()
            assert result["interrupted"] is False
            assert "session_id" in result
            assert "delta" in result




# ═══════════════════════════════════════════════════════════
# TestMaybeSpawnTitleGeneration
# ═══════════════════════════════════════════════════════════

class TestMaybeSpawnTitleGeneration:
    """_maybe_spawn_title_generation — 后台 AI 标题生成触发逻辑。"""

    def _session(self):
        """构造满足生成条件的最小 session mock。"""
        session = MagicMock()
        session._state = MagicMock()
        session._state.ai_title_done = False
        agent = MagicMock()
        agent.messages = [
            {"role": "user", "content": "帮我分析项目"},
            {"role": "assistant", "content": "好的，正在分析"},
        ]
        agent.get_async_model_port.return_value = MagicMock()
        session._agent = agent
        session._model = "model-a"
        return session

    @pytest.mark.asyncio
    async def test_spawns_task_and_marks_done(self):
        """条件满足 → 创建后台 task → 完成后标记 ai_title_done。"""
        from src.core.internal.session._session_lifecycle import _maybe_spawn_title_generation
        import src.core.title_generator as tg

        session = self._session()
        tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def fake_create_task(coro):
            t = real_create_task(coro)
            tasks.append(t)
            return t

        async def fake_update(model_port, messages, model, sid):
            return "AI 标题"

        with patch("asyncio.create_task", side_effect=fake_create_task), patch.object(
            tg, "maybe_update_title_async", new=fake_update,
        ):
            _maybe_spawn_title_generation(session, "sess-1")
            assert len(tasks) == 1
            # 在 patch 生效期间等待 task 完成（task 内 from-import 取到 fake）
            await tasks[0]

        assert session._state.ai_title_done is True

    @pytest.mark.asyncio
    async def test_skip_when_no_session_id(self):
        from src.core.internal.session._session_lifecycle import _maybe_spawn_title_generation

        session = self._session()
        with patch("asyncio.create_task") as mock_task:
            _maybe_spawn_title_generation(session, None)
        mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_not_enough_messages(self):
        from src.core.internal.session._session_lifecycle import _maybe_spawn_title_generation

        session = self._session()
        session._agent.messages = [{"role": "user", "content": "只有一条"}]
        with patch("asyncio.create_task") as mock_task:
            _maybe_spawn_title_generation(session, "sess-1")
        mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_ai_title_done(self):
        from src.core.internal.session._session_lifecycle import _maybe_spawn_title_generation

        session = self._session()
        session._state.ai_title_done = True
        with patch("asyncio.create_task") as mock_task:
            _maybe_spawn_title_generation(session, "sess-1")
        mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_model_port_none(self):
        from src.core.internal.session._session_lifecycle import _maybe_spawn_title_generation

        session = self._session()
        session._agent.get_async_model_port.return_value = None
        with patch("asyncio.create_task") as mock_task:
            _maybe_spawn_title_generation(session, "sess-1")
        mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_failure_is_silent(self):
        """后台 task 内部异常被消化，不标记 done，不向外传播。"""
        from src.core.internal.session._session_lifecycle import _maybe_spawn_title_generation
        import src.core.title_generator as tg

        session = self._session()
        tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def fake_create_task(coro):
            t = real_create_task(coro)
            tasks.append(t)
            return t

        async def boom(*args, **kwargs):
            raise RuntimeError("model down")

        with patch("asyncio.create_task", side_effect=fake_create_task), patch.object(
            tg, "maybe_update_title_async", new=boom,
        ):
            _maybe_spawn_title_generation(session, "sess-1")
            assert len(tasks) == 1
            # 在 patch 生效期间等待 task 完成
            await tasks[0]

        # task 异常被内部消化（不 raise）
        # 失败不标记 done（下轮可重试）
        assert session._state.ai_title_done is False
