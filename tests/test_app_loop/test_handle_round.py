"""test_handle_round — _handle_round prefill 优先路由单元测试。

验证 _handle_round 中 _merge_prefill 移至 retry_pending 检查之前后，
prefill 与 retry_pending 并发的路由优先级：prefill 非空时跳过 retry 哨兵路径。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ═══════════════════════════════════════════════════════════
# 辅助 Mock 工厂
# ═══════════════════════════════════════════════════════════

def _make_mock_loop():
    """创建最小化的 mock InteractiveLoop 实例。"""
    loop = MagicMock()
    loop._chat_ui = MagicMock()
    loop._chat_ui.bottom_bar = MagicMock()
    loop._chat_ui.input = MagicMock()
    loop._chat_ui.flush = MagicMock()
    loop._monitor = MagicMock()
    loop._loop_state = {}
    loop._get_term_width = MagicMock(return_value=80)
    loop._monitor_recovery_count = 0
    loop._force_exit = asyncio.Event()
    return loop


def _make_mock_session():
    """创建 mock ChatSession。"""
    s = MagicMock()
    s.retry_pending = False
    s.captured_prefill = ""
    return s


def _make_mock_state(**kwargs):
    """创建 mock SessionState。"""
    defaults = {"model": "deepseek", "retry": False, "prefill": ""}
    defaults.update(kwargs)
    state = MagicMock()
    state.model = defaults["model"]
    state.retry = defaults["retry"]
    state.prefill = defaults["prefill"]
    return state


# ═══════════════════════════════════════════════════════════
# TestHandleRoundPrefillPriority
# ═══════════════════════════════════════════════════════════

class TestHandleRoundPrefillPriority:
    """验证 prefill 优先于 retry_pending 的路由逻辑。"""

    @pytest.mark.asyncio
    async def test_prefill_priority_over_retry_pending(self):
        """prefill 非空 + retry_pending=True → 走 prefill 路径，不走 retry 哨兵。

        Bug 场景：/deitmsg 后 state.prefill="旧内容"，同时截断消息导致
        messages[-1].role="user" → sync_retry_pending 设置 retry_pending=True。
        旧代码中 retry 哨兵优先短接，prefill 被跳过，用户回车后触发自动重试。
        修复后 prefill 先于 retry 检查被消费，prefill 非空时跳过 retry 路径。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill, _RETRY_SENTINEL

        loop = _make_mock_loop()
        session = _make_mock_session()
        session.retry_pending = True  # 模拟 sync_retry_pending 副作用
        state = _make_mock_state(prefill="旧内容")

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        # asyncio.to_thread 在 mock 环境下无效，mock 掉
        with patch(
            "src.app_loop._loop._merge_prefill",
            wraps=_merge_prefill,
        ) as mock_merge:
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.return_value = "用户新输入"

                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # 验证 _merge_prefill 被调用（先于 retry 检查）
        mock_merge.assert_called_once()
        # state.prefill 已被消费
        assert state.prefill == ""

        # 验证 retry 路径未被触发（_RETRY_SENTINEL 未入队）
        call_args_list = queue.put.call_args_list
        for call_args in call_args_list:
            put_arg = call_args[0][0]
            assert put_arg is not _RETRY_SENTINEL, (
                f"_RETRY_SENTINEL should not be put in queue when prefill is set"
            )

        # 验证 wait_for_user_input 被调用且 prefill 参数非空
        mock_to_thread.assert_called()
        # to_thread(func, arg1, arg2, kwarg=...) → call_args[0]=func, [1]=monitor, [2]=prefill
        thread_args = mock_to_thread.call_args[0]
        assert thread_args[2] == "旧内容", (
            f"Expected prefill='旧内容', got {thread_args[2]}"
        )

        # should_exit 应为 False（正常继续）
        assert result.should_exit is False

    @pytest.mark.asyncio
    async def test_retry_pending_when_no_prefill(self):
        """prefill 为空 + retry_pending=True → 走 retry 哨兵路径。

        验证回退路径：当无 prefill 时，原有 retry_pending 逻辑保持不变。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill, _RETRY_SENTINEL

        loop = _make_mock_loop()
        session = _make_mock_session()
        session.retry_pending = True
        state = _make_mock_state(prefill="")  # 空 prefill
        session.captured_prefill = ""

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        with patch(
            "src.app_loop._loop._merge_prefill",
            wraps=_merge_prefill,
        ) as mock_merge:
            with patch(
                "src.app_loop._loop.reset_interrupt_async",
            ) as mock_reset:
                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # _merge_prefill 被调用，返回空
        mock_merge.assert_called_once()
        assert state.prefill == ""

        # retry 路径被触发：_RETRY_SENTINEL 入队
        queue.put.assert_called_once()
        put_arg = queue.put.call_args[0][0]
        assert put_arg is _RETRY_SENTINEL, (
            f"Expected _RETRY_SENTINEL, got {put_arg}"
        )

        # 验证 reset_interrupt_async 被调用
        mock_reset.assert_called_once()

        assert result.should_exit is False

    @pytest.mark.asyncio
    async def test_prefill_skips_queued_input(self):
        """prefill 非空时跳过 queued_input 处理。

        queued_input 是流式输出期间用户按 Enter 的排队输入，
        当 prefill 非空时（编辑场景），不应处理排队输入。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill

        loop = _make_mock_loop()
        loop._loop_state["queued_input"] = "排队输入"
        session = _make_mock_session()
        state = _make_mock_state(prefill="编辑内容")

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        with patch(
            "src.app_loop._loop._merge_prefill",
            wraps=_merge_prefill,
        ):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.return_value = "用户修改后提交"

                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # queued_input 未被消费（仍保留在 _loop_state 中，
        # 因为 prefill 非空时跳过了 pop 操作）
        assert loop._loop_state.get("queued_input") == "排队输入", (
            "queued_input should not be popped when prefill is non-empty"
        )

        # wait_for_user_input 被调用且 prefill 参数正确
        mock_to_thread.assert_called_once()
        thread_args = mock_to_thread.call_args[0]
        assert thread_args[2] == "编辑内容"

        assert result.should_exit is False

    @pytest.mark.asyncio
    async def test_normal_input_no_prefill_no_retry(self):
        """正常输入场景：无 prefill、无 retry → 走正常 wait_for_user_input。

        验证重构后正常路径不受影响。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill

        loop = _make_mock_loop()
        session = _make_mock_session()
        state = _make_mock_state(prefill="")

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        with patch(
            "src.app_loop._loop._merge_prefill",
            wraps=_merge_prefill,
        ):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.return_value = "正常用户输入"

                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # _merge_prefill 被调用，返回空
        assert state.prefill == ""

        # wait_for_user_input 被调用，prefill 为空字符串
        mock_to_thread.assert_called_once()
        thread_args = mock_to_thread.call_args[0]
        assert thread_args[2] == "", (
            f"Expected prefill='', got {thread_args[2]}"
        )

        # 正常返回
        assert result.should_exit is False


# ═══════════════════════════════════════════════════════════
# TestHandleRoundMonitorRecoveryExcessive
# ═══════════════════════════════════════════════════════════

class TestHandleRoundMonitorRecoveryExcessive:
    """验证 Bug 2 修复：EscapeMonitor 累计恢复超过 10 次时终止主循环。

    Bug 场景：EscapeMonitor 累计恢复超过 10 次后仍返回 should_exit=False，
    主循环继续执行，下轮立即再次抛出 RuntimeError，形成紧密错误循环。

    修复：累计恢复次数 > 10 时返回 should_exit=True，终止主循环，
    避免无限死循环。
    """

    @pytest.mark.asyncio
    async def test_exit_on_excessive_recovery_count(self):
        """_monitor_recovery_count > 10 → should_exit=True。

        验证超出阈值时 _handle_round 返回 should_exit=True，
        主循环收到后调用 _exit_save_and_stop 并退出。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill

        loop = _make_mock_loop()
        # 设置累计恢复计数 > 10 触发阈值
        loop._monitor_recovery_count = 11
        session = _make_mock_session()
        state = _make_mock_state(prefill="")

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        with patch("src.app_loop._loop._merge_prefill", wraps=_merge_prefill):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                # 模拟 EscapeMonitor 死亡 → RuntimeError
                mock_to_thread.side_effect = RuntimeError(
                    "EscapeMonitor I/O 超时"
                )

                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # 验证 should_exit=True（终止主循环）
        assert result.should_exit is True, (
            f"Expected should_exit=True, got should_exit={result.should_exit}"
        )

    @pytest.mark.asyncio
    async def test_normal_recovery_below_threshold(self):
        """_monitor_recovery_count <= 10 → 正常恢复路径，不退出。

        验证低于阈值时仍走正常恢复路径，返回 should_exit=False。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill

        loop = _make_mock_loop()
        # 设置累计恢复计数 < 10，不触发阈值
        loop._monitor_recovery_count = 5
        session = _make_mock_session()
        state = _make_mock_state(prefill="")

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        with patch("src.app_loop._loop._merge_prefill", wraps=_merge_prefill):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                # 模拟 EscapeMonitor 死亡 → RuntimeError
                mock_to_thread.side_effect = RuntimeError(
                    "EscapeMonitor I/O 超时"
                )

                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # 低于阈值时走正常恢复路径，返回 should_exit=False
        assert result.should_exit is False, (
            f"Expected should_exit=False, got should_exit={result.should_exit}"
        )

    @pytest.mark.asyncio
    async def test_restored_monitor_resets_counter(self):
        """成功获取输入后 _monitor_recovery_count 被重置为 0。

        验证修复不破坏计数器重置逻辑，一次成功的输入即可重置计数。
        """
        from src.app_loop._loop import InteractiveLoop
        from src.app_loop._utils import _merge_prefill

        loop = _make_mock_loop()
        loop._monitor_recovery_count = 5  # 之前有恢复
        session = _make_mock_session()
        state = _make_mock_state(prefill="")

        queue = MagicMock()
        msg_done = asyncio.Event()
        queue.put = AsyncMock(side_effect=lambda msg: msg_done.set())

        with patch("src.app_loop._loop._merge_prefill", wraps=_merge_prefill):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                # 这次成功获取输入
                mock_to_thread.return_value = "正常用户输入"

                handle = InteractiveLoop._handle_round
                result = await handle(loop, session, state, queue, msg_done)

        # 成功获取输入后计数器被重置
        assert loop._monitor_recovery_count == 0, (
            f"Expected counter=0 after successful input, got "
            f"{loop._monitor_recovery_count}"
        )
        assert result.should_exit is False


# ═══════════════════════════════════════════════════════════
# TestHandleRegularMsgNoDupSaveCheckpoint
# ═══════════════════════════════════════════════════════════

class TestHandleRegularMsgNoDupSaveCheckpoint:
    """验证 Bug 3 修复：中断路径下不重复 save_checkpoint。

    Bug 场景：中断路径下 _emit_round_events 已调用 save_checkpoint()，
    _handle_regular_msg 无条件再次调用 save_checkpoint()，造成双重保存。

    修复：_handle_regular_msg 捕获 run_round 返回值，仅当非中断路径
    （result.get("interrupted") == False）时才调用 save_checkpoint()。
    """

    @pytest.mark.asyncio
    async def test_no_save_checkpoint_on_interrupted(self):
        """中断路径（interrupted=True）→ save_checkpoint 未被调用。

        验证 _emit_round_events 中已保存，_handle_regular_msg 不重复保存。
        """
        from src.app_loop._loop import InteractiveLoop

        loop = MagicMock(spec=InteractiveLoop)
        loop._chat_ui = MagicMock()
        loop._chat_ui.bottom_bar = MagicMock()
        loop._monitor = MagicMock()

        session = AsyncMock()
        session.run_round = AsyncMock(return_value={
            "interrupted": True,
            "session_id": "test-sid",
            "delta": {"input": 10, "output": 20, "calls": 1},
            "elapsed": 1.5,
        })
        session.save_checkpoint = MagicMock()
        session.run_pending_loop = AsyncMock(return_value=(False, []))
        session.model = "deepseek"

        state = MagicMock()
        state.model = "deepseek"

        with patch("src.app_loop._loop.reset_interrupt_async"):
            await InteractiveLoop._handle_regular_msg(
                loop, "测试内容", session, state,
            )

        # 验证 run_round 被调用
        session.run_round.assert_awaited_once_with("测试内容")
        # 验证 save_checkpoint 未被调用（中断路径下由 _emit_round_events 处理）
        session.save_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_checkpoint_on_normal_path(self):
        """非中断路径（interrupted=False）→ save_checkpoint 被调用一次。

        验证正常完成的对话，checkpoint 仍被保存。
        """
        from src.app_loop._loop import InteractiveLoop

        loop = MagicMock(spec=InteractiveLoop)
        loop._chat_ui = MagicMock()
        loop._chat_ui.bottom_bar = MagicMock()
        loop._monitor = MagicMock()

        session = AsyncMock()
        session.run_round = AsyncMock(return_value={
            "interrupted": False,
            "session_id": "test-sid",
            "delta": {"input": 10, "output": 20, "calls": 1},
            "elapsed": 1.5,
        })
        session.save_checkpoint = MagicMock()
        session.run_pending_loop = AsyncMock(return_value=(False, []))
        session.model = "deepseek"

        state = MagicMock()
        state.model = "deepseek"

        with patch("src.app_loop._loop.reset_interrupt_async"):
            await InteractiveLoop._handle_regular_msg(
                loop, "正常消息", session, state,
            )

        # 验证 run_round 被调用
        session.run_round.assert_awaited_once_with("正常消息")
        # 验证 save_checkpoint 被调用一次
        session.save_checkpoint.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_checkpoint_on_interrupted_still_called_in_emit(self):
        """验证中断路径下 _emit_round_events 仍调用 save_checkpoint。

        本测试验证在 _session_lifecycle.py 层面，_emit_round_events 的
        interrupted 分支仍调用 save_checkpoint()，确保中断时状态被持久化。
        """
        from src.core.internal.session._session_lifecycle import _emit_round_events

        session = MagicMock()
        session.save_checkpoint = MagicMock()
        session._emit = MagicMock()
        session._state = MagicMock()
        session._state.retry_pending = False

        # 模拟 token 统计（函数内局部 import，需 patch 源模块路径）
        with patch(
            "src.api.stats.get_token_stats",
            return_value={"input": 100, "output": 200, "calls": 5},
        ):
            with patch(
                "src.api.stats.get_session_start_time",
                return_value=1000.0,
            ):
                with patch("time.time", return_value=1001.5):
                    result = _emit_round_events(
                        session, interrupted=True,
                        session_id="test-sid",
                        delta={"input": 10, "output": 20, "calls": 1},
                    )

        # 验证 _emit_round_events 的 interrupted 分支仍调用 save_checkpoint
        session.save_checkpoint.assert_called_once()
        assert result["interrupted"] is True
