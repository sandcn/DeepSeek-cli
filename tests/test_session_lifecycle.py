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
