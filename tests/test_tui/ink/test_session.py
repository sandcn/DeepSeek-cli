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
        s = _make_session()
        s._cmd_queue = MagicMock()
        s._cmd_queue.put.side_effect = _queue.Full
        with pytest.raises(_queue.Full):
            s.push_cmd_critical(PhaseDoneCmd(phase="done"))
        _, kwargs = s._cmd_queue.put.call_args
        assert kwargs["block"] is True
        assert kwargs["timeout"] == 1.0

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
