"""测试 ink/session.py — InkSession 队列/优先级/恢复。

保留 test_engine_priority 优先级/seq 不变式 + test_renderer 队列/崩溃恢复。
纯逻辑断言，无终端依赖。
"""

from __future__ import annotations

import queue as _queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.tui.ink.session import (
    InkSession,
    _get_cmd_priority,
    _get_cmd_id,
    _CRITICAL_CMDS,
    _STREAM_CMDS,
    _CONTENT_COMMANDS,
)
from src.tui._const import (
    ReasoningCmd,
    ContentCmd,
    PhaseDoneCmd,
    SubagentFrameCmd,
    WriteLineCmd,
    NotificationCmd,
    ToolCountIncCmd,
    ToolCountDecCmd,
    ToolFailIncCmd,
    MainPhaseCmd,
    SplashCmd,
    RenderCommand,
    CONTENT_COMMANDS,
)


class _Model:
    """极简 AppModel 桩（session 测试用）。"""

    def __init__(self):
        self.input_text = ""
        self.input_cursor = 0


def _make_session(**kwargs) -> InkSession:
    return InkSession(
        model=kwargs.pop("model", _Model()),
        config=kwargs.pop("config", None),
        **kwargs,
    )


class TestPriority:
    """命令优先级不变式（与 test_engine_priority 等价）。"""

    def test_stream_cmds_same_priority_as_phase_done(self):
        assert _get_cmd_priority(ReasoningCmd(text="x")) == 0
        assert _get_cmd_priority(ContentCmd(text="x")) == 0
        assert _get_cmd_priority(PhaseDoneCmd(phase="reasoning")) == 0

    def test_critical_cmds_set_unchanged(self):
        assert RenderCommand.REASONING not in _CRITICAL_CMDS
        assert RenderCommand.CONTENT not in _CRITICAL_CMDS
        assert RenderCommand.PHASE_DONE in _CRITICAL_CMDS
        for cid in _CRITICAL_CMDS:
            assert cid not in _STREAM_CMDS

    def test_content_commands_single_source(self):
        assert _CONTENT_COMMANDS is CONTENT_COMMANDS

    def test_get_cmd_id(self):
        assert _get_cmd_id(ContentCmd(text="x")) == RenderCommand.CONTENT

    def test_clear_msgs_low_priority_like_display(self):
        """CLEAR_MSGS 与 DISPLAY_MSGS 同为 LOW 优先级（同批按序处理）。

        /editmsg 编辑生效后 push 顺序：ClearMsgsCmd → DisplayMsgsCmd →
        WriteLineCmd（全 LOW），保证「先清空旧显示，再重渲染剩余消息，
        后写沙盒恢复提示」严格按序执行。
        """
        from src.tui._const import ClearMsgsCmd, DisplayMsgsCmd
        assert _get_cmd_priority(ClearMsgsCmd()) == 3  # _CMD_PRIORITY_LOW
        assert _get_cmd_priority(DisplayMsgsCmd(messages=[])) == 3
        assert _get_cmd_priority(WriteLineCmd(text="")) == 3


class TestCommandQueue:
    """InkSession 命令队列。"""

    def test_push_cmd(self):
        s = _make_session()
        s.push_cmd(ReasoningCmd(text="test"))
        assert s._cmd_queue.qsize() == 1

    def test_push_cmd_queue_full_handling(self):
        s = _make_session()
        s._cmd_queue.maxsize = 3
        for i in range(5):
            s.push_cmd(ReasoningCmd(text=f"test{i}"))
        assert s._cmd_queue.qsize() <= 3

    def test_push_cmd_stream_commands_nonblocking(self):
        """满队列时 REASONING/CONTENT 的 put 以 block=False 调用。"""
        s = _make_session()
        s._cmd_queue = MagicMock()
        s._cmd_queue.put.side_effect = _queue.Full
        s.push_cmd(ReasoningCmd(text="r"))
        s.push_cmd(ContentCmd(text="c"))
        calls = s._cmd_queue.put.call_args_list
        assert len(calls) == 2
        for call in calls:
            _, kwargs = call
            assert kwargs["block"] is False
        assert s._cmd_queue_dropped == 2
        assert s._consecutive_full == 2

    def test_push_cmd_critical_blocks(self):
        """队列满时 push_cmd_critical 走紧急直写兜底（BUG-T2，不抛异常）。"""
        s = _make_session()
        s._cmd_queue = MagicMock()
        s._cmd_queue.put.side_effect = _queue.Full
        s._write_emergency = MagicMock()
        s.push_cmd_critical(PhaseDoneCmd(phase="done"))
        _, kwargs = s._cmd_queue.put.call_args
        assert kwargs["block"] is True
        assert kwargs["timeout"] == 1.0
        # 兜底：不抛异常、丢弃计数增加、紧急直写被调用
        assert s._cmd_queue_dropped == 1
        assert s._consecutive_full == 1
        s._write_emergency.assert_called_once()

    def test_push_cmd_critical_full_fallback_regression(self):
        """BUG-T2 回归：队列满时不抛异常，关键命令经紧急路径直写（不静默丢失）。"""
        s = _make_session()
        s._cmd_queue = MagicMock()
        s._cmd_queue.put.side_effect = _queue.Full
        s._write_emergency = MagicMock()
        # 关键命令（PHASE_DONE 属 _CRITICAL_CMDS）
        s.push_cmd_critical(PhaseDoneCmd(phase="done"))
        assert s._cmd_queue_dropped == 1
        assert s._consecutive_full == 1
        s._write_emergency.assert_called_once()
        # 紧急直写内容包含命令名（不静默）
        emergency_text = s._write_emergency.call_args[0][0]
        assert "PHASE_DONE" in emergency_text
        # 成功路径（非 Full）维持原语义：_consecutive_full 复位 + 事件 set
        s2 = _make_session()
        s2._cmd_event = MagicMock()
        s2._cmd_queue = MagicMock()
        s2.push_cmd_critical(PhaseDoneCmd(phase="done"))
        assert s2._consecutive_full == 0
        s2._cmd_event.set.assert_called_once()

    def test_push_cmd_critical_no_drop_regression(self):
        """方向2 — 队列满且无 LOW 可腾位时 CRITICAL 命令不静默丢弃（紧急直写兜底）。"""
        s = _make_session()
        s._cmd_queue = MagicMock()
        s._cmd_queue.put.side_effect = _queue.Full
        s._cmd_queue.mutex = MagicMock()  # 腾位扫描需要 mutex 上下文
        s._cmd_queue.queue = []           # 队列无 LOW 可腾位
        s._write_emergency = MagicMock()
        # blocking（CRITICAL）命令：PHASE_DONE 属 _CRITICAL_CMDS
        s.push_cmd(PhaseDoneCmd(phase="content"))
        # 不静默丢弃：经 push_cmd_critical 紧急直写兜底
        s._write_emergency.assert_called_once()
        assert s._cmd_queue_dropped == 1
        emergency_text = s._write_emergency.call_args[0][0]
        assert "PHASE_DONE" in emergency_text

    def test_same_batch_order_by_seq(self):
        """同批命令出队顺序保持插入序（内容命令先于完成命令）。"""
        s = _make_session()
        s.push_cmd(ReasoningCmd(text="tail"))
        s.push_cmd(PhaseDoneCmd(phase="reasoning"))
        s.push_cmd(ContentCmd(text="first"))
        s.push_cmd(ContentCmd(text="tail"))
        s.push_cmd(PhaseDoneCmd(phase="content"))
        # 直接排空队列（不启动线程）
        out = []
        while not s._cmd_queue.empty():
            _, _, cmd = s._cmd_queue.get_nowait()
            out.append(cmd)
        cids = [c.cid for c in out]
        assert cids == [
            RenderCommand.REASONING,
            RenderCommand.PHASE_DONE,
            RenderCommand.CONTENT,
            RenderCommand.CONTENT,
            RenderCommand.PHASE_DONE,
        ]

    def test_flush_drains_queue(self):
        s = _make_session()
        for i in range(5):
            s._cmd_queue.put((0, i, WriteLineCmd(text=f"test{i}")))
        s.flush(timeout=1.0)
        assert s._cmd_queue.qsize() == 0

    def test_request_bottom_redraw(self):
        s = _make_session()
        assert s._bottom_redraw_requested.is_set() is False
        s.request_bottom_redraw()
        assert s._bottom_redraw_requested.is_set() is True


class TestLifecycle:
    """start/stop 生命周期。"""

    def test_duplicate_start_race_prevention(self):
        s = _make_session()
        s.start()
        assert s._render_thread is not None
        original = s._render_thread
        s.start()
        assert s._render_thread is original
        s.stop()

    def test_start_after_stop_works(self):
        s = _make_session()
        s.start()
        s.stop()
        assert s._render_running is False
        old = s._render_thread
        s.start()
        assert s._render_running is True
        assert s._render_thread is not old
        s.stop()

    def test_render_running_flag(self):
        s = _make_session()
        assert s.is_render_running() is False
        s.start()
        assert s.is_render_running() is True
        s.stop()
        assert s.is_render_running() is False

    def test_ensure_cursor_upper_noop(self):
        s = _make_session()
        s.ensure_cursor_upper()  # 不抛异常

    def test_stop_version_change_joins_new_thread_regression(self):
        """BUG-T9 回归：版本变化时 stop 不提前返回，重新捕获新线程并 join。"""
        s = _make_session()
        s._ink_renderer.suspend = MagicMock()
        s._drain_queue_safe = MagicMock()

        old_thread = MagicMock()
        new_thread = MagicMock()

        # 第一轮：old_thread 在 join 时触发"崩溃恢复"（版本变化 + 线程替换）
        def old_join(timeout):
            s._render_version = 2          # 版本变化
            s._render_thread = new_thread  # 崩溃恢复重启新线程

        old_thread.join.side_effect = old_join
        old_thread.is_alive.return_value = True  # join 后仍存活（触发版本检查）

        # 第二轮：new_thread join 后死亡
        new_thread.join.side_effect = lambda timeout: None
        new_thread.is_alive.return_value = False

        s._render_thread = old_thread
        s._render_version = 1

        s.stop()

        # 新线程被 join 且最终 _render_running 为 False
        old_thread.join.assert_called_once()
        new_thread.join.assert_called_once()
        assert s._render_running is False
        s._drain_queue_safe.assert_called()


class TestCrashRecovery:
    """崩溃恢复（与 test_renderer TestTuiEngineCrashRecovery 等价）。"""

    @pytest.fixture
    def session(self):
        s = _make_session()
        s._config = s._config.with_overrides(recover_delay=0.01)
        return s

    def test_recovering_event_in_init(self, session):
        assert isinstance(session._recovering_event, threading.Event)
        assert not session._recovering_event.is_set()

    def test_recovering_event_set_on_crash(self, session):
        session._drain_queue_safe = MagicMock(return_value=0)
        session._render_running = True
        session._recover_attempts = 0
        exc = RuntimeError("模拟崩溃")
        result = session._handle_render_crash(exc)
        assert session._recovering_event.is_set()
        assert result is True  # 已恢复

    def test_finally_checks_version_instead_of_event(self, session):
        session._drain_queue_safe = MagicMock(return_value=0)
        session._render_running = False
        session._render()
        session._drain_queue_safe.assert_called_once()

    def test_finally_skips_drain_on_version_change(self, session):
        session._drain_queue_safe = MagicMock(return_value=0)
        session._render_running = True

        def _drain_and_bump():
            session._render_version += 1
            session._render_running = False
            return False

        session._drain_queue = _drain_and_bump
        session._render()
        session._drain_queue_safe.assert_not_called()

    def test_no_recovering_bool_attribute(self, session):
        assert not hasattr(session, "_recovering")

    def test_render_crashed_event(self, session):
        assert session.render_crashed is False
        session._render_crashed.set()
        assert session.render_crashed is True


class TestDrainQueue:
    """drain 阶段顺序（面板刷新在锁外）。"""

    def test_panel_refresh_outside_lock(self):
        s = _make_session()
        call_order = []
        s._phase_process_input = lambda: call_order.append("process_input")
        s._phase_pre_update_panels = lambda: call_order.append("pre_update_panels")
        s._cmd_queue = MagicMock()
        s._cmd_queue.get_nowait.side_effect = _queue.Empty

        class _FakeLock:
            def __enter__(self):
                call_order.append("acquire_lock")
                return True
            def __exit__(self, *a):
                call_order.append("release_lock")
                return False

        with patch(
            "src.tui.ink.session._try_acquire_output_lock",
            return_value=_FakeLock(),
        ):
            s._drain_queue()

        assert call_order.index("pre_update_panels") < call_order.index("acquire_lock")
        assert call_order.index("process_input") < call_order.index("acquire_lock")

    def test_event_cleared_before_wait(self):
        s = _make_session()
        s._cmd_event = MagicMock()
        s._drain_queue = MagicMock(return_value=True)
        s._drain_queue()
        s._cmd_event.clear()
        s._cmd_event.wait(timeout=s._config.render_interval)
        calls = s._cmd_event.mock_calls
        clear_idx = next(i for i, c in enumerate(calls) if c[0] == "clear")
        wait_idx = next(i for i, c in enumerate(calls) if c[0] == "wait")
        assert clear_idx < wait_idx

    def test_session_drain_polls_sigwinch_regression(self):
        """BUG-T4 回归：渲染循环 _phase_process_sigwinch 轮询 process_sigwinch。"""
        s = _make_session()
        with patch("src.tui.ink.session.process_sigwinch") as mock_ps:
            mock_ps.return_value = False
            s._phase_process_sigwinch()
            mock_ps.assert_called_once()

    def test_drain_queue_invokes_sigwinch_phase(self):
        """_drain_queue 前置阶段调用 _phase_process_sigwinch（SIGWINCH 轮询）。"""
        s = _make_session()
        call_order = []
        s._phase_process_sigwinch = lambda: call_order.append("sigwinch")
        s._phase_process_input = lambda: call_order.append("process_input")
        s._phase_pre_update_panels = lambda: call_order.append("pre_update_panels")
        s._update_system_stats = lambda: call_order.append("sys_stats")
        s._cmd_queue = MagicMock()
        s._cmd_queue.get_nowait.side_effect = _queue.Empty

        class _FakeLock:
            def __enter__(self):
                call_order.append("acquire_lock")
                return True
            def __exit__(self, *a):
                call_order.append("release_lock")
                return False

        with patch(
            "src.tui.ink.session._try_acquire_output_lock",
            return_value=_FakeLock(),
        ):
            s._drain_queue()

        assert "sigwinch" in call_order
        assert call_order.index("sigwinch") < call_order.index("process_input")

    def test_apply_cmd_called(self):
        """_apply_commands 调用注入的 apply_cmd。"""
        applied = []
        s = InkSession(model=_Model(), apply_cmd=lambda m, cmd: applied.append(cmd))
        s._apply_commands([ContentCmd(text="x"), WriteLineCmd(text="y")])
        assert [c.cid for c in applied] == [RenderCommand.CONTENT, RenderCommand.WRITE_LINE]

    def test_apply_cmd_exception_isolated(self):
        """apply_cmd 异常不中断批次。"""
        applied = []

        def bad(m, cmd):
            if cmd.cid == RenderCommand.CONTENT:
                raise ValueError("boom")
            applied.append(cmd)

        s = InkSession(model=_Model(), apply_cmd=bad)
        s._apply_commands([ContentCmd(text="x"), WriteLineCmd(text="y")])
        assert [c.cid for c in applied] == [RenderCommand.WRITE_LINE]

    def test_update_input_updates_model(self):
        s = _make_session()
        s.update_input("hello", 3)
        assert s._model.input_text == "hello"
        assert s._model.input_cursor == 3
        s.update_input("world")
        assert s._model.input_cursor == 5


class TestRenderBackoff:
    """方向2 P7 — _drain_queue 渲染异常指数退避（递增间隔，成功复位）。"""

    class _FakeLock:
        """锁桩：恒可获取（模拟 _try_acquire_output_lock 成功路径）。"""

        def __enter__(self):
            return True

        def __exit__(self, *a):
            return False

    def test_render_failure_backoff_exponential_regression(self):
        """连续异常时 sleep 间隔 0.1→0.2→0.4（≤1.0 封顶）；成功后复位。"""
        s = _make_session()
        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=self._FakeLock()), \
             patch.object(s, "_should_render", return_value=True), \
             patch.object(s, "_render_frame", side_effect=[
                 RuntimeError("boom1"),
                 RuntimeError("boom2"),
                 RuntimeError("boom3"),
                 None,  # 成功
             ]), \
             patch("src.tui.ink.session.time.sleep") as mock_sleep:
            s._drain_queue()  # 第 1 次失败 → sleep 0.1
            s._drain_queue()  # 第 2 次失败 → sleep 0.2
            s._drain_queue()  # 第 3 次失败 → sleep 0.4
            s._drain_queue()  # 成功渲染 → 复位
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays == [0.1, 0.2, 0.4]
        assert all(d <= 1.0 for d in delays)
        assert s._consecutive_render_failures == 0

    def test_render_backoff_caps_at_one_second_regression(self):
        """连续 6 次失败 → 间隔 0.1,0.2,0.4,0.8,1.0,1.0（1.0 封顶）。"""
        s = _make_session()
        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=self._FakeLock()), \
             patch.object(s, "_should_render", return_value=True), \
             patch.object(s, "_render_frame", side_effect=[
                 RuntimeError(f"boom{i}") for i in range(6)
             ]), \
             patch("src.tui.ink.session.time.sleep") as mock_sleep:
            for _ in range(6):
                s._drain_queue()
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert delays == [0.1, 0.2, 0.4, 0.8, 1.0, 1.0]
        assert s._consecutive_render_failures == 6

    def test_render_failure_sets_dirty_for_retry_regression(self):
        """渲染失败后 _dirty 仍为 True（下拍重试，修复前失败帧不重试）。"""
        s = _make_session()
        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=self._FakeLock()), \
             patch.object(s, "_should_render", return_value=True), \
             patch.object(s, "_render_frame", side_effect=RuntimeError("boom")), \
             patch("src.tui.ink.session.time.sleep"):
            s._dirty = True
            s._drain_queue()
        # 失败帧补置脏标记（下一 10Hz 拍重试）
        assert s._dirty is True
        assert s._consecutive_render_failures == 1

    def test_render_success_clears_dirty_and_resets_failures(self):
        """成功渲染后 _dirty 清空且连续失败计数复位（协同：失败→重试→成功）。"""
        s = _make_session()
        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=self._FakeLock()), \
             patch.object(s, "_should_render", return_value=True), \
             patch.object(s, "_render_frame", side_effect=[RuntimeError("boom"), None]), \
             patch("src.tui.ink.session.time.sleep"):
            s._dirty = True
            s._drain_queue()  # 失败 → _dirty 补置
            assert s._dirty is True
            s._drain_queue()  # 成功 → 复位
        assert s._consecutive_render_failures == 0


class TestRenderFailureSleepOutsideLock:
    """方向1 步骤4 — 渲染失败退避 sleep 移出输出锁块（锁外退避）。"""

    class _RecorderLock:
        """锁桩：记录进入/退出（验证 sleep 在锁外）。"""

        def __enter__(self):
            return True

        def __exit__(self, *a):
            return False

    def test_render_failure_sleep_outside_lock_runtime_regression(self):
        """渲染失败时 sleep 在锁释放后调用（锁外退避，不阻塞其他写入方）。"""
        s = _make_session()
        order = []

        class RecorderLock(self._RecorderLock):
            def __enter__(self):
                order.append("lock_enter")
                return True

            def __exit__(self, *a):
                order.append("lock_exit")
                return False

        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=RecorderLock()), \
             patch.object(s, "_should_render", return_value=True), \
             patch.object(s, "_render_frame", side_effect=RuntimeError("boom")), \
             patch("src.tui.ink.session.time.sleep", side_effect=lambda d: order.append("sleep")):
            s._drain_queue()
        assert order.index("lock_exit") < order.index("sleep"), (
            f"sleep 应在锁释放后调用（锁外退避），实际 order={order}"
        )

    def test_render_failure_sleep_outside_lock_source_regression(self):
        """源码层面：渲染决策与 sleep 位于 _try_acquire_output_lock 锁块外。

        锁块为 ``with _try_acquire_output_lock(...)``（块内缩进 > 锁行缩进）；
        渲染调用经 ``self._should_render(changed)`` 决策、位于锁块外
        （缩进 <= 锁行缩进），sleep 在其后。
        """
        import inspect
        src = inspect.getsource(InkSession._drain_queue)
        lines = src.splitlines()
        lock_line_idx = next(i for i, l in enumerate(lines) if "_try_acquire_output_lock" in l)
        should_render_idx = next(i for i, l in enumerate(lines) if "self._should_render(changed)" in l)
        render_line_idx = next(i for i, l in enumerate(lines) if "self._render_frame()" in l)
        sleep_line_idx = next(i for i, l in enumerate(lines) if "time.sleep(delay)" in l)
        lock_indent = len(lines[lock_line_idx]) - len(lines[lock_line_idx].lstrip())
        should_indent = len(lines[should_render_idx]) - len(lines[should_render_idx].lstrip())
        assert should_render_idx > lock_line_idx, "渲染决策应在锁行之后"
        assert should_indent <= lock_indent, (
            f"_should_render 调用应位于锁块外（缩进 {should_indent} <= 锁行 {lock_indent}）"
        )
        assert render_line_idx > should_render_idx, "渲染调用应在决策之后"
        assert sleep_line_idx > render_line_idx, "sleep 应在渲染调用之后"


class TestPositionCursorMissingCompletionAttr:
    """方向1 步骤4 — session._position_cursor 缺失 completion 属性守卫。"""

    class _MissingItemsCompletion:
        """缺 items 属性的 completion 桩（_completion_height 访问 items 抛 AttributeError）。"""
        visible = True

        def __getattr__(self, name):
            raise AttributeError(f"missing attr: {name}")

    def test_position_cursor_missing_completion_attr_regression(self):
        """构造缺 items 属性的 completion → _position_cursor 不抛异常、正常放置光标。"""
        from src.tui.ink.fiber import Fiber

        s = _make_session()
        fiber = Fiber("host", "input-area", {
            "text": "abc",
            "cursor_pos": 2,
            "prompt": "> ",
            "completion": self._MissingItemsCompletion(),
        })
        fiber.layout_box = _Box(x=1, y=0, w=30, h=4)
        s._root_fiber = fiber
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()  # 不抛异常（缺属性回退 popup_height=0）
            mock_pc.assert_called_once()
        # popup_height 回退 0 → row = box.y(0) + 0 + 1 + vis_row + 1
        row, col = mock_pc.call_args[0]
        assert row >= 1, f"光标行应正常计算（popup_height=0），实际 {row}"

    def test_position_cursor_completion_normal_regression(self):
        """正常 completion（含 items）行为不变（回归）。"""
        from src.tui.ink.fiber import Fiber
        from src.tui.app.model import CompletionState

        s = _make_session()
        fiber = Fiber("host", "input-area", {
            "text": "abc",
            "cursor_pos": 2,
            "prompt": "> ",
            "completion": CompletionState(
                visible=True, items=["a", "b"], texts=["a", "b"], selected=0,
            ),
        })
        fiber.layout_box = _Box(x=1, y=0, w=30, h=4)
        s._root_fiber = fiber
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()  # 不抛异常
            mock_pc.assert_called_once()
        # popup_height = 2（items 2 个 + 2）→ row 计入弹窗高度
        row, col = mock_pc.call_args[0]
        assert row >= 4, f"含弹窗时光标行应计入 popup_height，实际 {row}"


class TestSyncRenderLock:
    """方向1 — request_bottom_redraw 同步渲染路径加锁（suspend 竞态修复）。"""

    class _FakeLockAcquired:
        """锁桩：可获取（_try_acquire_output_lock 成功路径）。"""

        def __enter__(self):
            return True

        def __exit__(self, *a):
            return False

    class _FakeLockUnavailable:
        """锁桩：不可获取（locked=False）。"""

        def __enter__(self):
            return False

        def __exit__(self, *a):
            return False

    def test_sync_render_holds_output_lock_regression(self):
        """render 线程停止时同步渲染持有 output lock（patch _try_acquire_output_lock 断言进入）。"""
        s = _make_session()
        s._render_running = False
        entered = []

        class _RecorderLock(self._FakeLockAcquired):
            def __enter__(self):
                entered.append("enter")
                return super().__enter__()

        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=_RecorderLock()) as mock_lock, \
             patch.object(s, "_render_frame") as mock_rf:
            s.request_bottom_redraw()
        mock_lock.assert_called_once()
        assert entered == ["enter"]
        mock_rf.assert_called_once()

    def test_sync_render_lock_unavailable_skips_regression(self):
        """同步渲染锁超时（locked=False）→ 跳过渲染（弹窗延迟一帧可接受）。"""
        s = _make_session()
        s._render_running = False
        with patch("src.tui.ink.session._try_acquire_output_lock", return_value=self._FakeLockUnavailable()), \
             patch.object(s, "_render_frame") as mock_rf:
            s.request_bottom_redraw()
        mock_rf.assert_not_called()

    def test_sync_render_only_when_thread_stopped_regression(self):
        """render 线程运行时不走同步渲染（不加锁不渲染）。"""
        s = _make_session()
        s._render_running = True
        with patch("src.tui.ink.session._try_acquire_output_lock") as mock_lock, \
             patch.object(s, "_render_frame") as mock_rf:
            s.request_bottom_redraw()
        mock_lock.assert_not_called()
        mock_rf.assert_not_called()


class TestEventBatching:
    """脏标记 + 窗口内事件批处理（不单独渲染，等 10Hz 拍；空闲跳过渲染）。"""

    def test_event_within_window_waits_for_tick(self):
        """事件到达但 render_interval 未到期 → 不渲染（等待批处理）。"""
        import time
        s = _make_session()
        s._last_bottom_redraw = time.monotonic()
        assert s._should_render(changed=True) is False, "窗口内事件不应立即渲染"

    def test_tick_renders_batched_events(self):
        """脏且 render_interval 到期 → 渲染（批处理窗口内全部事件）。"""
        import time
        s = _make_session()
        s._last_bottom_redraw = time.monotonic()
        time.sleep(s._config.render_interval + 0.01)
        assert s._should_render(changed=True) is True, "10Hz 拍应渲染"

    def test_idle_no_render(self):
        """空闲（无变化）→ 跳过渲染（避免 CPU 100%）。"""
        s = _make_session()
        s._last_bottom_redraw = 0.0
        assert s._should_render(changed=False) is False, "无脏变化不应渲染"

    def test_dirty_initial_render(self):
        """脏标记且间隔到期 → 渲染并清除脏。"""
        s = _make_session()
        s._dirty = True
        s._last_bottom_redraw = 0.0
        assert s._should_render(changed=False) is True
        assert s._dirty is False
        # 渲染后未到期且已清除脏 → 不再渲染
        assert s._should_render(changed=False) is False

    def test_render_clears_dirty(self):
        """渲染后清除脏标记（后续空闲不再渲染）。"""
        import time
        s = _make_session()
        s._dirty = True
        s._last_bottom_redraw = time.monotonic() - s._config.render_interval - 0.01
        assert s._should_render(changed=False) is True
        assert s._dirty is False
        assert s._should_render(changed=False) is False


class TestRenderInvariants:
    """render 相关不变式。"""

    def test_critical_cmd_set_contents(self):
        assert RenderCommand.PHASE_DONE in _CRITICAL_CMDS
        assert RenderCommand.TOOL_SUMMARY in _CRITICAL_CMDS
        assert RenderCommand.SPLASH in _CRITICAL_CMDS
        assert RenderCommand.CONTENT not in _CRITICAL_CMDS
        assert RenderCommand.SUBAGENT_FRAME not in _CRITICAL_CMDS

    def test_critical_cmd_blocking_semantics(self):
        """关键命令 push 走阻塞路径（block=True, timeout=0.1）。"""
        s = _make_session()
        s._cmd_queue = MagicMock()
        s.push_cmd(PhaseDoneCmd(phase="content"))
        _, kwargs = s._cmd_queue.put.call_args
        assert kwargs["block"] is True
        assert kwargs["timeout"] == 0.1

    def test_non_critical_nonblocking(self):
        s = _make_session()
        s._cmd_queue = MagicMock()
        s.push_cmd(NotificationCmd(text="n"))
        _, kwargs = s._cmd_queue.put.call_args
        assert kwargs["block"] is False


class TestInputRouterInjection:
    """INK-1 — session 接线 use_input router → Input.set_input_hook_router。"""

    def test_session_injects_router_to_input_regression(self):
        """_on_input_router 经 Input.set_input_hook_router 接线（消费端只读注入点）。"""
        s = _make_session()
        mock_input = MagicMock()
        s.set_input(mock_input)
        s._on_input_router(lambda ev: True)
        mock_input.set_input_hook_router.assert_called()
        router = mock_input.set_input_hook_router.call_args[0][0]
        assert callable(router)

    def test_session_pending_router_replayed_on_set_input_regression(self):
        """构造期（_input 未注入）发布的 router 缓存，set_input 后补发。"""
        s = _make_session()
        s._on_input_router(lambda ev: False)  # _input 为 None → 缓存
        assert s._pending_input_router is not None
        mock_input = MagicMock()
        s.set_input(mock_input)
        mock_input.set_input_hook_router.assert_called_once()
        assert s._pending_input_router is None

    def test_session_on_input_router_sets_pending(self):
        """_on_input_router 始终记录最新 router（含 _input 已注入场景）。"""
        s = _make_session()
        mock_input = MagicMock()
        s.set_input(mock_input)
        s._on_input_router(lambda ev: True)
        assert s._pending_input_router is not None
        mock_input.set_input_hook_router.assert_called()


class TestPositionCursorReusesInputCache:
    """PERF-1 — session._position_cursor 复用 input_area 缓存布局。"""

    def test_position_cursor_reuses_input_cache_regression(self):
        """同 text/max_input 时 _position_cursor 走缓存布局（不重新整段换行）。"""
        from src.tui.app.input_area import _compute_input_layout
        from src.tui.ink.fiber import Fiber
        from src.tui.ink.output import Frame, Line

        s = _make_session()
        # 手工构造 input-area fiber + 布局缓存（模拟 measure 已建立缓存）
        fiber = Fiber("host", "input-area", {
            "text": "hello world",
            "cursor_pos": 5,
            "prompt": "> ",
            "completion": None,
        })
        fiber.layout_box = _Box(x=1, y=0, w=30, h=4)
        text = "hello world"
        max_input = 30 - len("> ")
        rows, wrapped = _compute_input_layout(text, max_input)
        fiber._input_layout_cache = ((text, max_input), (rows, wrapped))
        s._root_fiber = fiber
        # 验证 _position_cursor 不抛异常且 place_cursor 被调用（复用缓存路径）
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()
            mock_pc.assert_called_once()
        # 缓存路径与回退路径结果一致：手工比对光标位置
        from src.tui._input import _compute_cursor_visual_pos as old
        new_row, new_col = _cursor_visual_from_cached(fiber)
        old_row, old_col = old("hello world", 5, max_input)
        assert (new_row, new_col) == (old_row, old_col)

    def test_position_cursor_writes_back_cache_regression(self):
        """未命中时 _position_cursor 计算并写回 fiber._input_layout_cache（PERF-1 写回）。"""
        from src.tui.app.input_area import _compute_input_layout
        from src.tui.ink.fiber import Fiber

        s = _make_session()
        fiber = Fiber("host", "input-area", {
            "text": "缓存写回验证",
            "cursor_pos": 3,
            "prompt": "> ",
            "completion": None,
        })
        fiber.layout_box = _Box(x=1, y=0, w=30, h=4)
        s._root_fiber = fiber
        assert not hasattr(fiber, "_input_layout_cache")
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()
            mock_pc.assert_called_once()
        # 写回缓存：键 = (text, max_input)，值 = (rows, wrapped_by_logical)
        text = "缓存写回验证"
        max_input = 30 - len("> ")
        assert hasattr(fiber, "_input_layout_cache")
        key, (rows, wrapped) = fiber._input_layout_cache
        assert key == (text, max_input)
        expect_rows, expect_wrapped = _compute_input_layout(text, max_input)
        assert (rows, wrapped) == (expect_rows, expect_wrapped)

    def test_position_cursor_falls_back_on_miss_regression(self):
        """缓存未命中（text 变化）时回退 _compute_cursor_visual_pos。"""
        from src.tui.ink.fiber import Fiber

        s = _make_session()
        fiber = Fiber("host", "input-area", {
            "text": "abc",
            "cursor_pos": 2,
            "prompt": "> ",
            "completion": None,
        })
        fiber.layout_box = _Box(x=1, y=0, w=30, h=4)
        # 不设置 _input_layout_cache（模拟未建立缓存）→ 走回退路径
        s._root_fiber = fiber
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()
            mock_pc.assert_called_once()


class TestInputFiberCache:
    """方向2 P5 — session._input_fiber 缓存输入区 fiber（免每帧全树递归查找）。

    ★ 标准 React Ink 组件化：输入区为 InputArea 标准组件（返回 Column +
    dataInputArea 标记容器）——查找条件为 props.dataInputArea 或旧
    "input-area" host（兼容）。
    """

    def _make_session_with_input_tree(self):
        """构造 build_tree 产出 InputArea 的会话。"""
        from src.tui.ink.element import h, BOX
        from src.tui.app.input_area import InputArea
        state = {"key": "ia-1"}

        def build(model, width):
            return h(BOX, None, h(InputArea, {
                "key": state["key"],
                "text": "hello",
                "cursor_pos": 0,
                "prompt": "> ",
                "completion": None,
                "status_active": False, "cpu": 0, "mem": 0,
                "history_search": None, "width": 80,
            }))

        s = _make_session(build_tree=build)
        return s, state

    def test_position_cursor_uses_cached_fiber_regression(self):
        """渲染一帧建立缓存后，再次 _position_cursor 不触发 _find_input_fiber。"""
        s, _ = self._make_session_with_input_tree()
        s._ink_renderer = MagicMock()  # 避免真实终端输出
        s._render_frame()
        assert s._input_fiber is not None
        assert s._input_fiber.props.get("dataInputArea") is True
        with patch.object(s, "_find_input_fiber", wraps=s._find_input_fiber) as mock_find:
            s._position_cursor()
            mock_find.assert_not_called()

    def test_render_frame_rebuilds_on_fiber_replaced_regression(self):
        """输入区 fiber 被替换（旧 fiber 删除）→ _render_frame 重建缓存。"""
        s, state = self._make_session_with_input_tree()
        s._ink_renderer = MagicMock()
        s._render_frame()
        old = s._input_fiber
        assert old is not None
        # 替换输入区（不同 key）→ 调和器删除旧 fiber（deleted=True 保持）→
        # _render_frame 缓存失效重建（找到新 fiber）
        state["key"] = "ia-2"
        with patch.object(s, "_find_input_fiber", wraps=s._find_input_fiber) as mock_find:
            s._render_frame()
            mock_find.assert_called_once()
            assert s._input_fiber is not None
            assert s._input_fiber is not old
            assert s._input_fiber.props.get("dataInputArea") is True
            assert s._input_fiber.props.get("key") == "ia-2"


class _Box:
    """极简 LayoutBox 桩（含 x/y/w/h 属性）。"""

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x=0, y=0, w=0, h=0):
        self.x = x
        self.y = y
        self.w = w
        self.h = h


class TestDirection6CursorAndWidth:
    """方向6 — 光标列右边界 clamp + resize 后流式渲染宽度传播。"""

    def test_position_cursor_clamps_col_regression(self):
        """超宽输入（vis_col 超 width）→ place_cursor 收到 col ≤ width（右边界 clamp）。"""
        from src.tui.ink.fiber import Fiber

        s = _make_session()
        s._width_cache = MagicMock()
        s._width_cache.get_width.return_value = 20  # 窄终端
        fiber = Fiber("host", "input-area", {
            "text": "x" * 100,
            "cursor_pos": 100,
            "prompt": "> ",
            "completion": None,
        })
        fiber.layout_box = _Box(x=1, y=0, w=30, h=4)
        s._root_fiber = fiber
        with patch.object(s._ink_renderer, "place_cursor") as mock_pc:
            s._position_cursor()
            row, col = mock_pc.call_args[0]
            assert col <= 20, f"光标列应 clamp 到 width(20)，实际 {col}"

    def test_render_frame_propagates_width_to_open_renderers_regression(self):
        """宽度变化 → 开放通道 renderer.set_width 传播；宽度未变不重复；renderer None 跳过。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.app.app import build_app_element
        from src.tui._const import ContentCmd, PhaseDoneCmd

        model = AppModel()
        apply_cmd(model, ContentCmd(text="# Hi\n"))
        renderer = model.content_renderer
        assert renderer is not None
        s = InkSession(model=model, apply_cmd=apply_cmd, build_tree=build_app_element)
        s._width_cache = MagicMock()
        s._width_cache.get_width.return_value = 80
        s._width_cache.get_height.return_value = 24
        s._ink_renderer = MagicMock()
        s._last_render_width = 0

        # 首帧（0→80）：set_width(80) 传播
        s._render_frame()
        assert renderer._width == 80, f"开放 renderer 应收到 set_width(80)，实际 {renderer._width}"
        assert s._last_render_width == 80

        # 同宽度再次渲染：不重复传播（_last_render_width 已更新）
        s._render_frame()
        assert renderer._width == 80

        # 宽度变化 80→100：再次传播
        s._width_cache.get_width.return_value = 100
        s._render_frame()
        assert renderer._width == 100, f"resize 后应传播 set_width(100)，实际 {renderer._width}"

        # 已关闭通道 renderer=None：跳过不抛
        apply_cmd(model, PhaseDoneCmd(phase="content"))
        assert model.content_renderer is None
        s._width_cache.get_width.return_value = 120
        s._render_frame()  # 不抛异常

    def test_render_frame_reflows_committed_on_width_change(self):
        """宽度变化 → _render_frame 触发 reflow_committed（committed_lines 重排 ≤ 新宽度）。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.app.app import build_app_element
        from src.tui._const import WriteLineCmd

        model = AppModel()
        # 提交一个超宽行（80 宽下 wrap 成多行；write_line 无头无尾空行）
        apply_cmd(model, WriteLineCmd(text="a" * 120))
        assert all(ln.width <= 80 for ln in model.committed_lines)
        n_wide = len(model.committed_lines)
        old_committed = model.committed_lines

        s = InkSession(model=model, apply_cmd=apply_cmd, build_tree=build_app_element)
        s._width_cache = MagicMock()
        s._width_cache.get_width.return_value = 80
        s._width_cache.get_height.return_value = 24
        s._ink_renderer = MagicMock()
        s._last_render_width = 0
        s._render_frame()  # 首帧 80：宽度未变（model.width=80）→ 不重排

        # 宽度 80→40：重排 committed_lines ≤ 40 且产出新列表对象
        s._width_cache.get_width.return_value = 40
        s._render_frame()
        assert model.width == 40
        assert all(ln.width <= 40 for ln in model.committed_lines), (
            "resize 后 committed_lines 应重排 ≤ 40"
        )
        assert model.committed_lines is not old_committed, "重排应产出新列表对象"
        assert len(model.committed_lines) > n_wide, "缩窄后行数应增加"

        # 同宽度再渲染：不重复重排（引用不变）
        committed_ref = model.committed_lines
        s._render_frame()
        assert model.committed_lines is committed_ref


def _cursor_visual_from_cached(fiber):
    """经 session._position_cursor 同款逻辑读取缓存并计算光标（测试辅助）。"""
    from src.tui.app.input_area import _cursor_visual_from_layout
    text = str(fiber.props.get("text", ""))
    cursor_pos = int(fiber.props.get("cursor_pos", -1))
    prompt = str(fiber.props.get("prompt", "> "))
    max_input = max(1, fiber.layout_box.w - len(prompt))
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        _, wrapped_by_logical = cached[1]
        return _cursor_visual_from_layout(text, cursor_pos, wrapped_by_logical)
    from src.tui._input import _compute_cursor_visual_pos
    return _compute_cursor_visual_pos(text, cursor_pos, max_input)


class TestAppControl:
    """方向B 步骤10 — session 接线 useApp control（exit/clear）。"""

    def test_session_wires_app_control_regression(self):
        """session 构造后 _app_control 注入 exit/clear（对齐既有装配测试契约）。"""
        from src.tui.ink import hooks as _hooks
        try:
            s = _make_session()
            ctrl = _hooks._app_control
            assert ctrl is not None
            assert callable(ctrl["exit"])
            assert callable(ctrl["clear"])
        finally:
            _hooks.set_app_control(None)

    def test_request_exit_sets_flag_and_stops(self):
        """request_exit 置 exit_requested 并停止渲染（幂等）。"""
        s = _make_session()
        s.stop = MagicMock()
        assert s.exit_requested is False
        s.request_exit()
        assert s.exit_requested is True
        s.stop.assert_called_once()

    def test_render_thread_exit_resets_running_flag(self):
        """BUG-12 — 渲染线程内 exit 后 _render_running 置 False（可重启）。

        request_exit 在渲染线程内不调用 stop（防 join 自身死锁）——线程
        退出后须自行置 ``_render_running=False``，否则 start() 判 True 直接
        return → 无法重启（状态一致性：exit 后渲染状态与「线程已停止」一致）。
        """
        s = _make_session()
        s._render_running = True
        s._exit_requested = True
        # 同步直接运行 _render：首轮循环即检查 _exit_requested → 置 False 退出
        s._render()
        assert s._render_running is False, (
            "渲染线程 exit 后 _render_running 应为 False"
        )
        assert s.exit_requested is True

    def test_use_app_exit_sets_session_exit_requested(self):
        """useApp().exit() 后 session.exit_requested 置位。"""
        from src.tui.ink.hooks import useApp, set_app_control
        try:
            s = _make_session()
            s.stop = MagicMock()  # 避免真实 stop 干扰
            set_app_control({"exit": s.request_exit, "clear": s.request_clear})
            ctrl = useApp()
            ctrl["exit"]()
            assert s.exit_requested is True
        finally:
            set_app_control(None)

    def test_request_clear_resets_renderer_and_requests_redraw(self):
        """request_clear 重置渲染器 + 请求重绘（非全屏模型强制全量重绘）。"""
        s = _make_session()
        s._ink_renderer.reset = MagicMock()
        s.request_bottom_redraw = MagicMock()
        s.request_clear()
        s._ink_renderer.reset.assert_called_once()
        s.request_bottom_redraw.assert_called_once()


class TestResizeFullRefresh:
    """方向3 — resize 后全量刷新（终端尺寸变化时旧帧与屏幕不对齐）。"""

    def test_width_change_resets_renderer(self):
        """宽度变化 → InkRenderer.reset() 被调用（全量刷新）。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.app.app import build_app_element
        from src.tui._const import WriteLineCmd

        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="hello"))
        s = InkSession(model=model, apply_cmd=apply_cmd, build_tree=build_app_element)
        s._width_cache = MagicMock()
        s._width_cache.get_width.return_value = 80
        s._width_cache.get_height.return_value = 24
        renderer = MagicMock()
        s._ink_renderer = renderer
        s._last_render_width = 0
        s._last_render_height = 0

        s._render_frame()  # 首帧（尺寸 80x24）
        assert renderer.reset.call_count >= 1  # 首帧尺寸初始化也重置

        renderer.reset.reset_mock()
        s._width_cache.get_width.return_value = 100  # resize
        s._render_frame()
        assert renderer.reset.call_count >= 1, "宽度变化应触发全量刷新（reset）"

        # 同尺寸再渲染：不重置（增量 diff 路径）
        renderer.reset.reset_mock()
        s._render_frame()
        assert renderer.reset.call_count == 0, "同尺寸渲染不应重置（增量）"

    def test_height_change_resets_renderer(self):
        """高度变化 → InkRenderer.reset() 被调用（全量刷新）。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui.app.app import build_app_element
        from src.tui._const import WriteLineCmd

        model = AppModel()
        apply_cmd(model, WriteLineCmd(text="hello"))
        s = InkSession(model=model, apply_cmd=apply_cmd, build_tree=build_app_element)
        s._width_cache = MagicMock()
        s._width_cache.get_width.return_value = 80
        s._width_cache.get_height.return_value = 24
        renderer = MagicMock()
        s._ink_renderer = renderer
        s._last_render_width = 0
        s._last_render_height = 0

        s._render_frame()  # 首帧
        renderer.reset.reset_mock()
        s._width_cache.get_height.return_value = 30  # resize 高度
        s._render_frame()
        assert renderer.reset.call_count >= 1, "高度变化应触发全量刷新（reset）"


class TestRenderLoopThrottle:
    """PERF-8 — 渲染循环防忙循环（高频命令不破坏 10Hz 节流）。

    回归：subagent 执行工具期间高频命令（ToolCountInc/DecCmd、SUBAGENT_FRAME
    等）持续 ``_cmd_event.set()``——修复前 ``_cmd_event.wait(timeout=0.1)``
    被 set 立即唤醒，渲染循环失去 10Hz 节流失忙循环（CPU 100%）。
    """

    def test_high_frequency_events_throttled(self):
        """高频命令持续 push 时渲染循环保持 ~10Hz（非忙循环）。"""
        import io as _io
        import time as _time

        s = _make_session()
        s._ink_renderer._stream = _io.StringIO()
        with patch.object(s, "_drain_queue", wraps=s._drain_queue) as mock_drain:
            s.start()
            try:
                _time.sleep(0.15)  # 首帧稳定
                # 高频事件：模拟 subagent 工具事件（每 1ms 一对，持续 0.4s）
                deadline = _time.monotonic() + 0.4
                while _time.monotonic() < deadline:
                    s.push_cmd(ToolCountIncCmd())
                    s.push_cmd(ToolCountDecCmd())
                    _time.sleep(0.001)
                _time.sleep(0.2)
            finally:
                s.stop()
        total = mock_drain.call_count
        # 总时长 ~0.75s：10Hz 节流 → ~8 次；忙循环（修复前）→ 数十次以上
        assert total < 25, (
            f"渲染循环失去节流（忙循环）: {total} 次 _drain_queue 调用"
        )
        assert total >= 3, (
            f"渲染循环应正常迭代: {total} 次 _drain_queue 调用"
        )
