"""Tests for src/app_loop.py — /loop 命令状态行守卫逻辑与清理路径

测试覆盖：
  - _on_round_start: _loop_mode 守卫跳过/不跳过 reset_token_speed()
  - _on_round_end: _loop_mode 守卫跳过/不跳过 disable_status() 和 notify_chat_completed()
  - _handle_loop_cmd 正常完成路径：finally 清理
  - _handle_loop_cmd 中断路径：break 后 finally 仍执行清理
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

from src.app_loop import _make_round_callbacks, InteractiveLoop


# ═══════════════════════════════════════════════════════════
# _make_round_callbacks — _on_round_start 守卫测试
# ═══════════════════════════════════════════════════════════


class TestOnRoundStartLoopModeGuard:
    """测试 _on_round_start 回调中 _loop_mode 守卫逻辑。

    核心断言：
    - _loop_mode=True → 跳过 reset_token_speed()，仍调用 enable_status()
    - _loop_mode=False → 调用 reset_token_speed() + enable_status()
    """

    def _make_mocks(self):
        """创建标准 mock 对象套件。"""
        return {
            "session": MagicMock(),
            "monitor": MagicMock(),
            "chat_ui": MagicMock(),
        }

    # ── 场景 1：_loop_mode=True → 跳过 reset_token_speed ──

    def test_loop_mode_true_skips_reset_token_speed(self):
        """_loop_mode=True 时 reset_token_speed() 不被调用。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.reset_token_speed") as mock_reset:
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_start"]()

        mock_reset.assert_not_called()

    def test_loop_mode_true_still_calls_enable_status(self):
        """_loop_mode=True 时 enable_status() 仍被调用（状态行保持活跃）。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.reset_token_speed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_start"]()

        mocks["chat_ui"].bottom_bar.enable_status.assert_called_once()

    # ── 场景 2：_loop_mode=False → 正常调用 ──

    def test_loop_mode_false_calls_reset_token_speed(self):
        """_loop_mode=False 时 reset_token_speed() 正常调用。"""
        mocks = self._make_mocks()
        loop_state = {}  # 无 _loop_mode 键 → falsy

        with patch("src.app_loop.reset_token_speed") as mock_reset:
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_start"]()

        mock_reset.assert_called_once()

    def test_loop_mode_false_calls_enable_status(self):
        """_loop_mode=False 时 enable_status() 正常调用。"""
        mocks = self._make_mocks()
        loop_state = {}

        with patch("src.app_loop.reset_token_speed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_start"]()

        mocks["chat_ui"].bottom_bar.enable_status.assert_called_once()

    def test_loop_mode_explicit_false_calls_reset_token_speed(self):
        """_loop_mode 显式为 False 时 reset_token_speed() 正常调用。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": False}

        with patch("src.app_loop.reset_token_speed") as mock_reset:
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_start"]()

        mock_reset.assert_called_once()

    # ── chat_ui=None 边界 ──

    def test_chat_ui_none_does_not_crash_on_start(self):
        """chat_ui=None 时 _on_round_start 不崩溃。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.reset_token_speed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, chat_ui=None
            )
            # 不应抛出异常
            callbacks["on_start"]()


# ═══════════════════════════════════════════════════════════
# _make_round_callbacks — _on_round_end 守卫测试
# ═══════════════════════════════════════════════════════════


class TestOnRoundEndLoopModeGuard:
    """测试 _on_round_end 回调中 _loop_mode 守卫逻辑。

    核心断言：
    - _loop_mode=True → 跳过 disable_status()、request_bottom_redraw()、
      notify_chat_completed()，仍调用 drain_stream_input()
    - _loop_mode=False → 调用全部（disable_status + 通知 + drain）
    """

    def _make_mocks(self):
        """创建标准 mock 对象套件。"""
        chat_ui = MagicMock()
        # get_status_elapsed 默认返回 0.0（避免 MagicMock > 0 比较报错）
        chat_ui.bottom_bar.get_status_elapsed.return_value = 0.0
        # drain_stream_input 默认返回 (None, "") — 无排队输入
        monitor = MagicMock()
        monitor.drain_stream_input.return_value = (None, "")
        monitor.drain_captured_input.return_value = ""
        return {
            "session": MagicMock(),
            "monitor": monitor,
            "chat_ui": chat_ui,
        }

    # ── 场景 3：_loop_mode=True → 跳过冻结和通知 ──

    def test_loop_mode_true_skips_disable_status(self):
        """_loop_mode=True 时 disable_status() 不被调用。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.notify_chat_completed") as mock_notify:
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mocks["chat_ui"].bottom_bar.disable_status.assert_not_called()
        mocks["chat_ui"].request_bottom_redraw.assert_not_called()

    def test_loop_mode_true_skips_notify_chat_completed(self):
        """_loop_mode=True 时 notify_chat_completed() 不被调用。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.notify_chat_completed") as mock_notify:
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mock_notify.assert_not_called()

    def test_loop_mode_true_still_drains_stream_input(self):
        """_loop_mode=True 时 drain_stream_input() 仍被调用（排出键盘输入）。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mocks["monitor"].drain_stream_input.assert_called_once()

    def test_loop_mode_true_still_drains_captured_input(self):
        """_loop_mode=True 时 drain_captured_input() 仍被调用。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mocks["monitor"].drain_captured_input.assert_called_once()

    # ── 场景 4：_loop_mode=False → 正常冻结 + 通知 ──

    def test_loop_mode_false_calls_disable_status(self):
        """_loop_mode=False 时 disable_status() 正常调用。"""
        mocks = self._make_mocks()
        loop_state = {}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mocks["chat_ui"].bottom_bar.disable_status.assert_called_once()
        mocks["chat_ui"].request_bottom_redraw.assert_called_once()

    def test_loop_mode_false_calls_notify_chat_completed(self):
        """_loop_mode=False 时 notify_chat_completed() 正常调用。"""
        mocks = self._make_mocks()
        loop_state = {}

        with patch("src.app_loop.notify_chat_completed") as mock_notify:
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mock_notify.assert_called_once()

    def test_loop_mode_false_drains_stream_input(self):
        """_loop_mode=False 时 drain_stream_input() 正常调用。"""
        mocks = self._make_mocks()
        loop_state = {}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        mocks["monitor"].drain_stream_input.assert_called_once()

    # ── 排队输入路由 ──

    def test_queued_input_stored_in_loop_state(self):
        """drain_stream_input 返回 queued 时存入 loop_state["queued_input"]。"""
        mocks = self._make_mocks()
        mocks["monitor"].drain_stream_input.return_value = ("hello", "")
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        assert loop_state.get("queued_input") == "hello"

    def test_buffer_text_stored_as_prefill(self):
        """drain_stream_input 返回 buffer_text 时存入 session.captured_prefill。"""
        mocks = self._make_mocks()
        mocks["monitor"].drain_stream_input.return_value = (None, "partial_input")
        loop_state = {"_loop_mode": True}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, mocks["chat_ui"]
            )
            callbacks["on_end"]()

        assert "partial_input" in mocks["session"].captured_prefill

    # ── chat_ui=None 边界 ──

    def test_chat_ui_none_does_not_crash_on_end(self):
        """chat_ui=None 时 _on_round_end 不崩溃。"""
        mocks = self._make_mocks()
        loop_state = {"_loop_mode": False}

        with patch("src.app_loop.notify_chat_completed"):
            callbacks = _make_round_callbacks(
                mocks["session"], mocks["monitor"], loop_state, chat_ui=None
            )
            # 不应抛出异常
            callbacks["on_end"]()


# ═══════════════════════════════════════════════════════════
# _handle_loop_cmd — 清理路径测试
# ═══════════════════════════════════════════════════════════


class TestHandleLoopCmdCleanup:
    """测试 _handle_loop_cmd 中 try/finally 清理逻辑。

    核心断言：
    - 正常完成路径：finally 执行清理（_loop_mode=False、disable_status、
      reset_tool_count、reset_token_speed）
    - 中断路径（interrupted=True → break）：finally 仍执行清理
    """

    @pytest.fixture
    def loop_instance(self):
        """创建 InteractiveLoop 实例，mock 核心依赖。"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        # Mock _chat_ui
        loop._chat_ui = MagicMock()
        loop._chat_ui.bottom_bar = MagicMock()
        loop._chat_ui.write_line = MagicMock()
        # Mock _loop_state
        loop._loop_state = {}
        # Mock _force_exit
        loop._force_exit = MagicMock()
        loop._force_exit.clear = MagicMock()
        # Mock _loaded_data
        loop._loaded_data = None
        return loop

    @pytest.fixture
    def mock_session(self):
        """创建 mock ChatSession。"""
        session = MagicMock()
        session.run_round = AsyncMock()
        session.clear_messages = MagicMock()
        session.messages = []
        return session

    @pytest.fixture
    def mock_state(self):
        """创建 mock SessionState。"""
        from src.app_loop import SessionState
        return SessionState(model="test-model")

    # ── 场景 5：正常完成路径 ──

    async def test_normal_completion_cleans_up(self, loop_instance, mock_session, mock_state):
        """正常完成 2 轮循环后 finally 执行全部清理操作。"""
        # run_round 返回正常（未中断）
        mock_session.run_round.return_value = {"interrupted": False}

        with (
            patch("src.app_loop.reset_token_speed") as mock_reset_speed,
            patch("src.app_loop.reset_interrupt_async") as mock_reset_int,
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock) as mock_save,
        ):
            await loop_instance._handle_loop_cmd(
                "/loop 2 hello", mock_session, mock_state
            )

        # ── 验证 finally 清理 ──
        # 1. _loop_mode 被重置
        assert loop_instance._loop_state.get("_loop_mode") is False

        # 2. disable_status() 被调用
        loop_instance._chat_ui.bottom_bar.disable_status.assert_called_once()

        # 3. reset_tool_count() 被调用
        loop_instance._chat_ui.bottom_bar.reset_tool_count.assert_called_once()

        # 4. reset_token_speed() 被调用
        mock_reset_speed.assert_called_once()

        # 5. _save_loop_snapshot 在 finally 之后被调用（循环前后各一次）
        assert mock_save.call_count == 2

        # ── 验证循环执行 ──
        # clear_messages 每轮调用 1 次，2 轮 = 2 次
        assert mock_session.clear_messages.call_count == 2
        # run_round 每轮调用 2 次（第1次 + 第2次），2 轮 = 4 次
        assert mock_session.run_round.call_count == 4

    # ── 场景 6：中断路径 ──

    async def test_interrupted_cleans_up(self, loop_instance, mock_session, mock_state):
        """中断时 break 后 finally 仍执行全部清理操作。"""
        # 第1次 run_round 返回中断 → break
        mock_session.run_round.return_value = {"interrupted": True}

        with (
            patch("src.app_loop.reset_token_speed") as mock_reset_speed,
            patch("src.app_loop.reset_interrupt_async") as mock_reset_int,
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock) as mock_save,
        ):
            await loop_instance._handle_loop_cmd(
                "/loop 2 hello", mock_session, mock_state
            )

        # ── 验证 finally 清理 ──
        # 1. _loop_mode 被重置
        assert loop_instance._loop_state.get("_loop_mode") is False

        # 2. disable_status() 被调用
        loop_instance._chat_ui.bottom_bar.disable_status.assert_called_once()

        # 3. reset_tool_count() 被调用
        loop_instance._chat_ui.bottom_bar.reset_tool_count.assert_called_once()

        # 4. reset_token_speed() 被调用
        mock_reset_speed.assert_called_once()

        # 5. _save_loop_snapshot 在 finally 之后仍被调用（循环前 + 循环后）
        assert mock_save.call_count == 2

        # ── 验证循环提前退出 ──
        # 第1轮第1次即中断 → 仅调用 1 次 run_round
        assert mock_session.run_round.call_count == 1

    # ── 中断在第二轮 ──

    async def test_interrupted_in_second_round_cleans_up(self, loop_instance, mock_session, mock_state):
        """第二轮中断时 finally 仍执行清理。"""
        # 第1轮正常（2次 run_round 均不中断），第2轮第1次中断
        mock_session.run_round.side_effect = [
            {"interrupted": False},  # 第1轮·第1次
            {"interrupted": False},  # 第1轮·第2次
            {"interrupted": True},   # 第2轮·第1次 → break
        ]

        with (
            patch("src.app_loop.reset_token_speed") as mock_reset_speed,
            patch("src.app_loop.reset_interrupt_async"),
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock),
        ):
            await loop_instance._handle_loop_cmd(
                "/loop 3 hello", mock_session, mock_state
            )

        # finally 清理正常执行
        assert loop_instance._loop_state.get("_loop_mode") is False
        loop_instance._chat_ui.bottom_bar.disable_status.assert_called_once()
        loop_instance._chat_ui.bottom_bar.reset_tool_count.assert_called_once()
        mock_reset_speed.assert_called_once()

        # 第1轮×2 + 第2轮×1 = 3 次 run_round
        assert mock_session.run_round.call_count == 3

    # ── _loop_mode 生命周期 ──

    async def test_loop_mode_set_before_loop(self, loop_instance, mock_session, mock_state):
        """验证 _loop_mode 在循环开始前被设置为 True。"""
        mock_session.run_round.return_value = {"interrupted": False}

        # 记录 run_round 首次调用时的 _loop_mode 值
        _captured_mode = []

        async def capture_mode(*args, **kwargs):
            _captured_mode.append(loop_instance._loop_state.get("_loop_mode"))
            return {"interrupted": False}

        mock_session.run_round = capture_mode

        with (
            patch("src.app_loop.reset_token_speed"),
            patch("src.app_loop.reset_interrupt_async"),
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock),
        ):
            await loop_instance._handle_loop_cmd(
                "/loop 1 hello", mock_session, mock_state
            )

        # run_round 调用时 _loop_mode 应为 True
        assert _captured_mode, "run_round 应至少被调用一次"
        assert all(m is True for m in _captured_mode), (
            f"run_round 调用时 _loop_mode 应为 True，实际: {_captured_mode}"
        )

    # ── enable_status 在循环前调用 ──

    async def test_enable_status_called_before_loop(self, loop_instance, mock_session, mock_state):
        """验证 enable_status() 在循环开始前被调用。"""
        mock_session.run_round.return_value = {"interrupted": False}

        with (
            patch("src.app_loop.reset_token_speed"),
            patch("src.app_loop.reset_interrupt_async"),
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock),
        ):
            await loop_instance._handle_loop_cmd(
                "/loop 1 hello", mock_session, mock_state
            )

        # enable_status 应在循环前被调用
        loop_instance._chat_ui.bottom_bar.enable_status.assert_called_once()

    # ── 异常路径 ──

    async def test_exception_still_cleans_up(self, loop_instance, mock_session, mock_state):
        """run_round 抛出异常时 finally 仍执行清理。"""
        mock_session.run_round.side_effect = RuntimeError("LLM API 错误")

        with (
            patch("src.app_loop.reset_token_speed") as mock_reset_speed,
            patch("src.app_loop.reset_interrupt_async"),
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock) as mock_save,
        ):
            with pytest.raises(RuntimeError, match="LLM API 错误"):
                await loop_instance._handle_loop_cmd(
                    "/loop 2 hello", mock_session, mock_state
                )

        # finally 清理正常执行
        assert loop_instance._loop_state.get("_loop_mode") is False
        loop_instance._chat_ui.bottom_bar.disable_status.assert_called_once()
        loop_instance._chat_ui.bottom_bar.reset_tool_count.assert_called_once()
        mock_reset_speed.assert_called_once()
        # 异常时不应保存循环后快照（finally 后代码不执行）
        # _save_loop_snapshot 仅在循环前被调用一次
        assert mock_save.call_count == 1, (
            f"异常路径仅应保存循环前快照，实际调用 {mock_save.call_count} 次"
        )

    # ── 参数校验：无效输入不触发清理 ──

    async def test_invalid_usage_does_not_trigger_cleanup(self, loop_instance, mock_session, mock_state):
        """无效用法（参数不足）时不应设置 _loop_mode 也不触发清理。"""
        loop_instance._loop_state = {}

        with (
            patch("src.app_loop.reset_token_speed") as mock_reset_speed,
            patch("src.app_loop._save_loop_snapshot", new_callable=AsyncMock) as mock_save,
        ):
            await loop_instance._handle_loop_cmd(
                "/loop", mock_session, mock_state
            )

        # _loop_mode 未被设置
        assert "_loop_mode" not in loop_instance._loop_state
        # 清理函数未被调用
        loop_instance._chat_ui.bottom_bar.disable_status.assert_not_called()
        loop_instance._chat_ui.bottom_bar.reset_tool_count.assert_not_called()
        mock_reset_speed.assert_not_called()
        mock_save.assert_not_called()
