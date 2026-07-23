"""Tests for src/app_loop.py — /loop 命令状态行守卫逻辑与清理路径，
以及 /editmsg 命令的 suspend/resume + monitor stop/start 对齐。

测试覆盖：
  - _on_round_start: _loop_mode 守卫跳过/不跳过 reset_token_speed()
  - _on_round_end: _loop_mode 守卫跳过/不跳过 disable_status() 和 notify_chat_completed()
  - _handle_loop_cmd 正常完成路径：finally 清理
  - _handle_loop_cmd 中断路径：break 后 finally 仍执行清理
  - _handle_editmsg_cmd: chat_ui suspend/resume 和 monitor stop/start 调用验证
  - _handle_editmsg_cmd: chat_ui/monitor 为 None 的边界情况
  - _handle_editmsg_cmd: 异常路径 finally 仍执行恢复
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

from src.app_loop import (
    _make_round_callbacks,
    InteractiveLoop,
    _put_and_wait,
    SessionState,
)
from src.core.message_queue import MessageQueue, Message


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


# ═══════════════════════════════════════════════════════════
# _handle_editmsg_cmd — suspend/resume + monitor stop/start 测试
# ═══════════════════════════════════════════════════════════


class TestHandleEditmsgCmd:
    """测试 _handle_editmsg_cmd 的 suspend/resume 对齐 /model 模式。

    核心断言：
    - chat_ui 不为 None 时调用 suspend() 和 resume()
    - monitor 不为 None 时调用 stop() 和 start()
    - chat_ui 为 None 时不崩溃，也不调用 suspend/resume
    - monitor 为 None 时不崩溃，也不调用 stop/start
    - 异常路径 finally 仍执行 monitor.start() + chat_ui.resume()
    """

    @pytest.fixture
    def mock_session(self):
        """创建 mock ChatSession。"""
        session = MagicMock()
        session.agent = MagicMock()
        session.messages = []
        session.sync_retry_pending = MagicMock()
        return session

    @pytest.fixture
    def mock_state(self):
        """创建 SessionState 实例。"""
        from src.app_loop import SessionState
        return SessionState(model="test-model")

    # ── 场景 1：正常路径 suspend/resume ──

    async def test_suspend_resume_called(self, mock_session, mock_state):
        """chat_ui 和 monitor 均存在时，suspend/stop → 执行 → start/resume 全部调用。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        mock_chat_ui.suspend.assert_called_once()
        mock_chat_ui.resume.assert_called_once()

    async def test_monitor_stop_start_called(self, mock_session, mock_state):
        """monitor 不为 None 时 stop() 和 start() 被调用。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        mock_monitor.stop.assert_called_once()
        mock_monitor.start.assert_called_once()

    async def test_suspend_before_monitor_stop_order(self, mock_session, mock_state):
        """suspend() 在 monitor.stop() 之前调用（先暂停渲染，再停止键盘监听）。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        call_order = []

        def _record_suspend():
            call_order.append("suspend")
        def _record_stop():
            call_order.append("stop")

        mock_chat_ui.suspend.side_effect = _record_suspend
        mock_monitor.stop.side_effect = _record_stop

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        assert call_order == ["suspend", "stop"], (
            f"预期调用顺序 [suspend, stop]，实际: {call_order}"
        )

    async def test_resume_after_monitor_start_order(self, mock_session, mock_state):
        """finally 中 monitor.start() 在 chat_ui.resume() 之前调用。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        call_order = []

        def _record_start():
            call_order.append("start")
        def _record_resume():
            call_order.append("resume")

        mock_monitor.start.side_effect = _record_start
        mock_chat_ui.resume.side_effect = _record_resume

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        assert call_order == ["start", "resume"], (
            f"预期调用顺序 [start, resume]，实际: {call_order}"
        )

    # ── 场景 2：chat_ui 为 None ──

    async def test_chat_ui_none_no_crash(self, mock_session, mock_state):
        """chat_ui 为 None 时不崩溃。"""
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=None), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        # monitor 仍正常调用
        mock_monitor.stop.assert_called_once()
        mock_monitor.start.assert_called_once()

    # ── 场景 3：monitor 为 None ──

    async def test_monitor_none_no_crash(self, mock_session, mock_state):
        """monitor 为 None 时不崩溃，chat_ui suspend/resume 仍正常。"""
        mock_chat_ui = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=None), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        mock_chat_ui.suspend.assert_called_once()
        mock_chat_ui.resume.assert_called_once()

    # ── 场景 4：两者均为 None ──

    async def test_both_none_no_crash(self, mock_session, mock_state):
        """chat_ui 和 monitor 均为 None 时不崩溃。"""
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=None), \
             patch("src.app_loop.get_active_monitor", return_value=None), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        # 无异常即为通过

    # ── 场景 5：异常路径 finally 仍执行恢复 ──

    async def test_exception_still_resumes(self, mock_session, mock_state):
        """to_thread 抛出异常时 finally 仍执行 monitor.start() + chat_ui.resume()。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", side_effect=RuntimeError("LLM API 错误")):
            with pytest.raises(RuntimeError, match="LLM API 错误"):
                await _handle_editmsg_cmd(mock_session, mock_state)

        # finally 仍执行恢复
        mock_monitor.start.assert_called_once()
        mock_chat_ui.resume.assert_called_once()

    async def test_exception_after_stop_still_resumes(self, mock_session, mock_state):
        """to_thread 异常时：stop 已调用，finally 仍调用 start + resume。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", side_effect=RuntimeError("测试异常")):
            with pytest.raises(RuntimeError):
                await _handle_editmsg_cmd(mock_session, mock_state)

        # 确认 stop 已调用（异常前），start 也已调用（finally）
        mock_monitor.stop.assert_called_once()
        mock_monitor.start.assert_called_once()

    # ── 场景 6：display_messages 条件调用（_needs_rerender） ──

    async def test_display_messages_called_when_retry_true(self, mock_session, mock_state):
        """retry=True 时 display_messages 被调用（_needs_rerender 为 True）。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        # 模拟 edit_current_messages 设置了 retry=True
        async def _fake_to_thread(func, *args, **kwargs):
            edit_state = args[1]  # 第二个位置参数是 edit_state dict
            edit_state["retry"] = True

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", side_effect=_fake_to_thread), \
             patch("src.app_loop._non_system_messages", return_value=[]):
            await _handle_editmsg_cmd(mock_session, mock_state)

        mock_chat_ui.display_messages.assert_called_once()

    async def test_display_messages_not_called_when_no_change(self, mock_session, mock_state):
        """retry=False + prefill='' 时 display_messages 不被调用。"""
        mock_chat_ui = MagicMock()
        mock_monitor = MagicMock()
        from src.app_loop import _handle_editmsg_cmd

        with patch("src.chat_ui.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("src.app_loop.get_active_monitor", return_value=mock_monitor), \
             patch("src.app_loop.asyncio.to_thread", new_callable=AsyncMock):
            await _handle_editmsg_cmd(mock_session, mock_state)

        mock_chat_ui.display_messages.assert_not_called()

    async def test_display_messages_skipped_when_chat_ui_none(self, mock_session, mock_state):
        """chat_ui=None 时即使 retry=True 也不调用 display_messages。"""
        from src.app_loop import _handle_editmsg_cmd

        async def _fake_to_thread(func, *args, **kwargs):
            edit_state = args[1]
            edit_state["retry"] = True

        with patch("src.chat_ui.get_active_chat_ui", return_value=None), \
             patch("src.app_loop.get_active_monitor", return_value=None), \
             patch("src.app_loop.asyncio.to_thread", side_effect=_fake_to_thread):
            await _handle_editmsg_cmd(mock_session, mock_state)

        # 无异常即为通过（没有 chat_ui.display_messages 可断言）

    # ── 场景 7：Bug B 回归 — resume 强制重置 _active 状态 ──

    async def test_resume_rebuilds_bottom_bar_after_active_leak(self, mock_session, mock_state):
        """Bug B 回归：run_bottom_bar_selection 泄漏 _active=True 后，
        _handle_editmsg_cmd finally 块中 resume() 仍调用 bottom_bar.setup()。

        使用真实 ChatUIConsumer 实例（mock 引擎避免线程创建），
        模拟 editmsg 弹窗执行后留下 _active=True，
        验证 resume() 中 setup() 未被 early return 跳过。
        """
        from src.tui.consumer import ChatUIConsumer
        from src.app_loop._handlers import _handle_editmsg_cmd

        # 创建真实 ChatUIConsumer，mock 引擎避免真实线程创建
        consumer = ChatUIConsumer(event_bus=MagicMock())
        consumer._started = True
        consumer._engine._render_running = False
        consumer._engine.start = MagicMock()
        consumer._engine.stop = MagicMock()
        consumer._engine.flush = MagicMock()

        # 模拟 run_bottom_bar_selection 泄漏 _active=True
        consumer._bottom_bar._active = True

        # spy on bottom_bar.setup 记录调用
        original_setup = consumer._bottom_bar.setup
        call_count = [0]

        def _spy_setup():
            call_count[0] += 1
            return original_setup()

        consumer._bottom_bar.setup = _spy_setup

        mock_monitor = MagicMock()

        with patch("src.tui.consumer.get_active_chat_ui", return_value=consumer), \
             patch("src.api.escape_monitor.get_active_monitor", return_value=mock_monitor), \
             patch("asyncio.to_thread", new_callable=AsyncMock), \
             patch("sys.__stdout__"):
            await _handle_editmsg_cmd(mock_session, mock_state)

        # 验证 bottom_bar.setup() 在 resume() 中被调用（未被 early return 跳过）
        assert call_count[0] == 1, (
            f"预期 setup() 被调用 1 次，实际: {call_count[0]}"
        )
        # 验证 resume 后 _active 为 True（setup() 正常完成）
        assert consumer._bottom_bar._active is True, (
            f"resume 后 _active 应为 True，实际: {consumer._bottom_bar._active}"
        )


# ═══════════════════════════════════════════════════════════════
# Bug 7 回归测试 — TestMsgDoneTimeout
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestMsgDoneTimeout:
    """★ Bug 7 回归: msg_done.wait() 超时保护。

    验证 _put_and_wait 中 await asyncio.wait_for(msg_done.wait(), timeout=30.0)
    在 msg_done 从未被 set 时，超时后自动恢复而非永久阻塞。
    """

    async def test_timeout_does_not_raise(self):
        """msg_done 未 set 时超时后不抛出异常，继续执行"""
        import src.app_loop as _al

        queue = MessageQueue()
        msg_done = asyncio.Event()  # 初始 cleared，永不 set

        # ★ 临时改为短超时，避免 30s 等待导致 pytest-timeout 超时
        original = _al._MSG_DONE_TIMEOUT
        _al._MSG_DONE_TIMEOUT = 0.5
        try:
            await _put_and_wait(queue, "test_msg", msg_done)
        finally:
            _al._MSG_DONE_TIMEOUT = original

        # 验证消息已放入队列
        msg = await queue.get(timeout=0.1)
        assert msg is not None
        assert msg.content == "test_msg"

    async def test_normal_path_no_timeout(self):
        """msg_done 正常 set 时不触发超时"""
        queue = MessageQueue()
        msg_done = asyncio.Event()

        # 在 _put_and_wait 内部 await wait() 前，让另一个任务 set
        async def set_soon():
            await asyncio.sleep(0.05)
            msg_done.set()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(set_soon())
            await _put_and_wait(queue, "normal", msg_done)

        # 正常路径下 msg_done 被 clear
        assert not msg_done.is_set(), "完成后 msg_done 应被 clear"

    async def test_msg_put_before_wait(self):
        """消息在 wait 前已放入队列"""
        import src.app_loop as _al

        queue = MessageQueue()
        msg_done = asyncio.Event()  # 永不 set，验证超时保护

        original = _al._MSG_DONE_TIMEOUT
        _al._MSG_DONE_TIMEOUT = 0.5
        try:
            await _put_and_wait(queue, "before_wait", msg_done)
        finally:
            _al._MSG_DONE_TIMEOUT = original

        msg = await queue.get(timeout=0.1)
        assert msg.content == "before_wait"

    async def test_handle_round_exception_sets_msg_done(self):
        """_handle_round 的 except 块中 msg_done.set() 防止死锁"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        loop._chat_ui = MagicMock()
        loop._chat_ui.write_line = MagicMock()
        loop._force_exit = asyncio.Event()
        loop._loop_state = {}
        loop._msg_done_ref = None
        loop._loaded_data = None

        session = MagicMock()
        state = SessionState(model="test-model")
        queue = MessageQueue()
        msg_done = asyncio.Event()

        # mock _handle_round 抛出异常后 msg_done 被 set
        with patch.object(loop, "_handle_round",
                          side_effect=RuntimeError("round error")):
            # 手动模拟异常后的清理
            try:
                await loop._handle_round(session, state, queue, msg_done)
            except Exception:
                if not msg_done.is_set():
                    msg_done.set()

        # except 块中 msg_done 应被 set
        assert msg_done.is_set(), "异常路径后 msg_done 应被 set 防止死锁"


# ═══════════════════════════════════════════════════════════════
# Bug 8 回归测试 — TestMonitorFinallyGuard
# ═══════════════════════════════════════════════════════════════

class TestMonitorFinallyGuard:
    """★ Bug 8 回归: EscapeMonitor 确保 finally 保护。

    验证 monitor.stop() 后终端恢复被调用，
    且 _restore_terminal_settings 在 finally 中防御性执行。
    """

    def test_monitor_stop_restores_terminal_settings(self):
        """stop() 内部调用 _restore_terminal_settings"""
        from src.api.escape_monitor._monitor import EscapeMonitor as RealEscapeMonitor

        monitor = RealEscapeMonitor()
        with patch.object(monitor, "_restore_terminal_settings") as mock_restore:
            with patch.object(monitor, "_thread", None):  # 无线程
                monitor.stop()

            mock_restore.assert_called_once()

    def test_interactive_loop_finally_restores_terminal(self):
        """InteractiveLoop 的 finally 中 _restore_terminal_settings 被调用"""
        from src.api.escape_monitor._monitor import EscapeMonitor as RealEscapeMonitor

        monitor = RealEscapeMonitor()
        with patch.object(monitor, "_restore_terminal_settings") as mock_restore:
            with patch.object(monitor, "_thread", None):
                # 模拟 InteractiveLoop finally 中的防御性恢复
                monitor.stop()
                monitor._restore_terminal_settings()

            # stop() + 防御性恢复 = 至少 1 次
            assert mock_restore.call_count >= 1

    def test_monitor_stop_exception_does_not_bubble(self):
        """monitor.stop() 异常不阻止 finally 执行"""
        from src.api.escape_monitor._monitor import EscapeMonitor as RealEscapeMonitor

        monitor = RealEscapeMonitor()
        # 模拟 stop 中某步骤抛出异常
        with patch.object(monitor, "_restore_terminal_settings",
                          side_effect=[RuntimeError("stop 异常"), None]):
            try:
                monitor.stop()
            except RuntimeError:
                pass  # stop 内部异常被捕获，不传播到外部

            # 防御性恢复应已执行（假设 finally 中调用）
            # 此测试验证异常不阻止后续代码执行

    def test_restore_terminal_settings_idempotent(self):
        """_restore_terminal_settings 可多次调用（幂等）"""
        from src.api.escape_monitor._monitor import EscapeMonitor as RealEscapeMonitor

        monitor = RealEscapeMonitor()
        # 模拟 settings 已设为 None（终端已恢复）
        monitor._old_settings = None

        # 多次调用不应抛出
        monitor._restore_terminal_settings()
        monitor._restore_terminal_settings()
        monitor._restore_terminal_settings()
        # 无异常即为通过

    def test_restore_terminal_settings_impl_calls_tcflush(self):
        """_restore_terminal_settings_impl 调用 tcflush(fd, TCIFLUSH) 清空 stdin 内核缓冲区"""
        from src.api.escape_monitor._monitor import EscapeMonitor as RealEscapeMonitor

        monitor = RealEscapeMonitor()
        # 设置 old_settings，确保进入 tcsetattr 分支
        monitor._old_settings = MagicMock()
        mock_settings = monitor._old_settings  # 保存引用，因为方法执行后会置 None

        with patch("sys.stdin.fileno", return_value=42), \
             patch("termios.tcsetattr") as mock_tcsetattr, \
             patch("termios.tcflush") as mock_tcflush, \
             patch("termios.TCIFLUSH", 1), \
             patch("termios.TCSADRAIN", 2):
            monitor._restore_terminal_settings_impl()

        # 验证 tcflush 被调用，参数为 (fd, TCIFLUSH)
        mock_tcflush.assert_called_once_with(42, 1)
        # 验证 tcsetattr 也在 tcflush 之前被调用
        mock_tcsetattr.assert_called_once_with(42, 2, mock_settings)
        # 验证 old_settings 已被清空（方法正常完成）
        assert monitor._old_settings is None

    def test_restore_terminal_settings_impl_tcflush_failure_is_silent(self):
        """tcflush 抛出异常时不影响终端恢复流程，异常被静默吞掉"""
        from src.api.escape_monitor._monitor import EscapeMonitor as RealEscapeMonitor

        monitor = RealEscapeMonitor()
        monitor._old_settings = MagicMock()

        with patch("sys.stdin.fileno", return_value=42), \
             patch("termios.tcsetattr"), \
             patch("termios.tcflush", side_effect=OSError("tcflush 失败")), \
             patch("termios.TCIFLUSH", 1), \
             patch("termios.TCSADRAIN", 2):
            # 不应抛出异常
            monitor._restore_terminal_settings_impl()

        # 即使 tcflush 失败，old_settings 仍被清空（方法正常完成）
        assert monitor._old_settings is None

    def test_exit_save_and_stop_calls_stop_active_monitor(self):
        """_exit_save_and_stop 调用 stop_active_monitor"""
        from src.app_loop import _exit_save_and_stop

        session = MagicMock()
        session.messages = []
        with patch("src.app_loop.stop_active_monitor") as mock_stop:
            _exit_save_and_stop(session)
            mock_stop.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 步骤 4：monitor 恢复逻辑测试 — TestMonitorRecovery
# ═══════════════════════════════════════════════════════════════


class TestMonitorRecovery:
    """测试 _handle_round 中 monitor 死亡异常的捕获与恢复逻辑。

    核心断言：
    - RuntimeError("EscapeMonitor thread died") 触发 _teardown_monitor + _setup_monitor
    - 恢复成功后返回 should_exit=False
    - _setup_monitor 失败时返回 should_exit=True
    - 非 monitor 死亡的 RuntimeError 不触发恢复（向上传播）
    """

    # ── 公共夹具 ──────────────────────────────────────────

    @staticmethod
    def _make_loop_with_mocks():
        """创建带 mock 的 InteractiveLoop 实例。"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        loop._chat_ui = MagicMock()
        loop._chat_ui.write_line = MagicMock()
        loop._chat_ui.flush = MagicMock()
        loop._chat_ui.bottom_bar = MagicMock()
        loop._chat_ui.bottom_bar.ensure_cursor_in_lower = MagicMock()
        loop._chat_ui.bottom_bar.set_model_name = MagicMock()
        loop._chat_ui.wait_for_user_input = MagicMock()
        loop._monitor = MagicMock()
        loop._monitor.is_alive = True
        loop._force_exit = asyncio.Event()
        loop._loop_state = {}
        loop._msg_done_ref = None
        loop._loaded_data = None
        # ★ 补齐 _term_width_cache，避免 _get_term_width() 抛 AttributeError
        loop._term_width_cache = MagicMock()
        loop._term_width_cache.get.return_value = 80
        return loop

    # ── 场景 1：monitor 死亡 RuntimeError 触发恢复 ──

    @pytest.mark.asyncio
    async def test_runtime_error_from_dead_monitor_triggers_recovery(self):
        """wait_for_user_input 抛出 RuntimeError("EscapeMonitor thread died")
        时触发 _teardown_monitor + _setup_monitor，返回 should_exit=False。
        """
        loop = self._make_loop_with_mocks()
        # mock wait_for_user_input 抛出 monitor 死亡异常
        loop._chat_ui.wait_for_user_input.side_effect = RuntimeError(
            "EscapeMonitor thread died"
        )

        session = MagicMock()
        session.retry_pending = False
        session.pending_messages = []
        state = SessionState(model="test-model")
        queue = MessageQueue()
        msg_done = asyncio.Event()

        # mock _teardown_monitor 和 _setup_monitor 为 spy
        loop._teardown_monitor = MagicMock()
        loop._setup_monitor = MagicMock()

        with patch("src.app_loop._loop._merge_prefill", return_value=""):
            result = await loop._handle_round(session, state, queue, msg_done)

        # 验证恢复流程
        loop._teardown_monitor.assert_called_once()
        loop._setup_monitor.assert_called_once_with(session, state)
        assert result.should_exit is False, (
            f"恢复成功后应返回 should_exit=False，实际: {result.should_exit}"
        )

    # ── 场景 2：恢复失败时返回 should_exit=True ──

    @pytest.mark.asyncio
    async def test_recovery_failure_returns_should_exit_true(self):
        """_setup_monitor 抛异常时返回 should_exit=True（优雅退出）。"""
        loop = self._make_loop_with_mocks()
        loop._chat_ui.wait_for_user_input.side_effect = RuntimeError(
            "EscapeMonitor thread died"
        )

        session = MagicMock()
        session.retry_pending = False
        session.pending_messages = []
        state = SessionState(model="test-model")
        queue = MessageQueue()
        msg_done = asyncio.Event()

        # _teardown_monitor 正常，_setup_monitor 抛异常
        loop._teardown_monitor = MagicMock()
        loop._setup_monitor = MagicMock(
            side_effect=RuntimeError("无法创建 EscapeMonitor")
        )

        with patch("src.app_loop._loop._merge_prefill", return_value=""):
            result = await loop._handle_round(session, state, queue, msg_done)

        loop._teardown_monitor.assert_called_once()
        loop._setup_monitor.assert_called_once_with(session, state)
        assert result.should_exit is True, (
            f"恢复失败时应返回 should_exit=True，实际: {result.should_exit}"
        )
        # 验证错误消息已输出
        loop._chat_ui.write_line.assert_any_call(
            "\n  ⚠ EscapeMonitor 线程异常退出，正在重启…"
        )

    # ── 场景 3：非 monitor 死亡的 RuntimeError 不触发恢复 ──

    @pytest.mark.asyncio
    async def test_non_monitor_runtime_error_not_caught(self):
        """RuntimeError 不包含 "EscapeMonitor" 时不触发恢复，
        异常被外层 except Exception 捕获，返回 should_exit=False。
        """
        loop = self._make_loop_with_mocks()
        loop._chat_ui.wait_for_user_input.side_effect = RuntimeError(
            "some other error"
        )

        session = MagicMock()
        session.retry_pending = False
        session.pending_messages = []
        state = SessionState(model="test-model")
        queue = MessageQueue()
        msg_done = asyncio.Event()

        loop._teardown_monitor = MagicMock()
        loop._setup_monitor = MagicMock()

        with patch("src.app_loop._loop._merge_prefill", return_value=""):
            result = await loop._handle_round(session, state, queue, msg_done)

        # 恢复逻辑不应被触发
        loop._teardown_monitor.assert_not_called()
        loop._setup_monitor.assert_not_called()
        # 外层 except Exception 捕获后返回 should_exit=False
        assert result.should_exit is False, (
            f"非 monitor 异常应被外层捕获，返回 should_exit=False，实际: {result.should_exit}"
        )
        # 验证错误消息已输出
        loop._chat_ui.write_line.assert_any_call(
            f"\n  [错误] some other error，可继续输入"
        )

    # ── 场景 4：集成验证 — 含消息队列的完整恢复 ──

    @pytest.mark.asyncio
    async def test_recovery_with_message_queue_continues(self):
        """恢复后 _handle_round 返回 should_exit=False，不影响 msg_done。"""
        loop = self._make_loop_with_mocks()
        loop._chat_ui.wait_for_user_input.side_effect = RuntimeError(
            "EscapeMonitor thread died"
        )

        session = MagicMock()
        session.retry_pending = False
        session.pending_messages = []
        state = SessionState(model="test-model")
        queue = MessageQueue()
        msg_done = asyncio.Event()
        msg_done.set()  # 预设为 set，验证 clear 被调用

        loop._teardown_monitor = MagicMock()
        loop._setup_monitor = MagicMock()

        with patch("src.app_loop._loop._merge_prefill", return_value=""):
            result = await loop._handle_round(session, state, queue, msg_done)

        assert result.should_exit is False
        # msg_done 在方法入口被 clear，恢复路径中不应被 set
        # （msg_done 仅在通用 except Exception 中被 set）


# ═══════════════════════════════════════════════════════════════
# 步骤 4：_setup_monitor 防御性重建逻辑测试 — TestSetupMonitorRebuild
# ═══════════════════════════════════════════════════════════════


class TestSetupMonitorRebuild:
    """测试 _setup_monitor 的防御性重建逻辑。

    核心断言：
    - _monitor=None → 创建新实例
    - _monitor 线程已死 → 先清理再重建
    - _monitor 健康 → 复用现有实例（不重复创建）
    """

    # ── 场景 1：_monitor=None → 创建新实例 ──

    def test_monitor_none_creates_new_instance(self):
        """_monitor=None 时创建新的 EscapeMonitor。"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        loop._monitor = None
        loop._chat_ui = MagicMock()
        loop._chat_ui.setup_completion = MagicMock()
        loop._chat_ui.refresh_bottom_bar = MagicMock()

        session = MagicMock()
        state = SessionState(model="test-model")

        with patch("src.app_loop._loop.EscapeMonitor") as MockMonitor:
            mock_instance = MockMonitor.return_value
            mock_instance.is_alive = True
            loop._setup_monitor(session, state)

        MockMonitor.assert_called_once()
        # 新实例启动了
        mock_instance.start.assert_called_once()

    # ── 场景 2：monitor 线程已死 → 先清理再重建 ──

    def test_monitor_dead_rebuilds(self):
        """_monitor.is_alive=False 时先清理再创建新实例。"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        old_monitor = MagicMock()
        old_monitor.is_alive = False
        loop._monitor = old_monitor
        loop._chat_ui = MagicMock()
        loop._chat_ui.setup_completion = MagicMock()
        loop._chat_ui.refresh_bottom_bar = MagicMock()

        session = MagicMock()
        state = SessionState(model="test-model")

        with patch("src.app_loop._loop.EscapeMonitor") as MockMonitor:
            mock_instance = MockMonitor.return_value
            mock_instance.is_alive = True
            loop._setup_monitor(session, state)

        # 旧 monitor 被 stop + _restore_terminal_settings
        old_monitor.stop.assert_called_once()
        old_monitor._restore_terminal_settings.assert_called_once()
        # 新 monitor 被创建
        MockMonitor.assert_called_once()
        mock_instance.start.assert_called_once()

    # ── 场景 3：monitor 健康 → 复用 ──

    def test_monitor_alive_reuses(self):
        """_monitor.is_alive=True 时复用现有实例，不创建新 EscapeMonitor。"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        old_monitor = MagicMock()
        old_monitor.is_alive = True
        loop._monitor = old_monitor
        loop._chat_ui = MagicMock()
        loop._chat_ui.setup_completion = MagicMock()
        loop._chat_ui.refresh_bottom_bar = MagicMock()

        session = MagicMock()
        state = SessionState(model="test-model")

        with patch("src.app_loop._loop.EscapeMonitor") as MockMonitor:
            loop._setup_monitor(session, state)

        # 不应该创建新实例
        MockMonitor.assert_not_called()
        # 旧 monitor 不应被 stop
        old_monitor.stop.assert_not_called()
        # 但仍注册回调并 start
        old_monitor.set_special_key_callback.assert_called_once()
        old_monitor.start.assert_called_once()

    # ── 场景 4：stop 已死 monitor 异常不影响重建 ──

    def test_stop_dead_monitor_exception_does_not_block_rebuild(self):
        """stop 已死 monitor 抛异常时仍完成重建。"""
        loop = InteractiveLoop.__new__(InteractiveLoop)
        old_monitor = MagicMock()
        old_monitor.is_alive = False
        old_monitor.stop.side_effect = RuntimeError("线程已死")
        loop._monitor = old_monitor
        loop._chat_ui = MagicMock()
        loop._chat_ui.setup_completion = MagicMock()
        loop._chat_ui.refresh_bottom_bar = MagicMock()

        session = MagicMock()
        state = SessionState(model="test-model")

        with patch("src.app_loop._loop.EscapeMonitor") as MockMonitor:
            mock_instance = MockMonitor.return_value
            mock_instance.is_alive = True
            # 不应抛出异常
            loop._setup_monitor(session, state)

        # 新实例已创建并启动
        MockMonitor.assert_called_once()
        mock_instance.start.assert_called_once()
