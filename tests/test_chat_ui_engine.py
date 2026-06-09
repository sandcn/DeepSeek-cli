"""RenderEngine 单元测试 — 命令队列 / render 线程 / 渲染循环。

测试范围：
1. TestRenderEnginePushCmd    — push_cmd 入队/满队列/连续满/ERROR 直写
2. TestRenderEngineStartStop  — start/stop 生命周期管理/幂等/重启
3. TestRenderEngineFlush      — flush 等待消费/render 未运行时清空/超时
4. TestRenderEngineDrainQueue — _drain_queue 空跳过/流水线/容错/底部栏触发
5. TestRenderEngineRender     — _render 循环/异常崩溃
6. TestRenderEnginePositionCursor — position_cursor / ensure_cursor_upper
"""

from __future__ import annotations

import io
import logging
import queue
import sys
import threading
from unittest.mock import MagicMock, call, patch

import pytest

# ── 将项目根目录加入 sys.path（Termux 环境需要） ──
sys.path.insert(0, "/home/DeepSeek-cli")

from src.chat_ui._const import RenderCommand
from src.chat_ui._utils import _cmd_name
from src.chat_ui._engine import RenderEngine


# ══════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════

@pytest.fixture
def mock_renderer():
    """Mock ContentRenderer 实例。"""
    return MagicMock()


@pytest.fixture
def mock_bottom_bar():
    """Mock _BottomBar 实例。

    模拟 _BottomBar 对外提供的所有属性/方法：
      - is_status_active / is_resize_pending（property）
      - check_resize / sync_bottom_lines / force_redraw / get_cursor_info
      - compute_cursor_position（公开 API）
      - ensure_cursor_in_upper
    """
    bb = MagicMock()
    bb.is_status_active = False
    bb.is_resize_pending = False
    bb.check_resize.return_value = False
    bb.get_cursor_info.return_value = ("hello", 5, 30, 80)
    bb.compute_cursor_position.return_value = (28, 8)
    return bb


@pytest.fixture
def engine(mock_renderer, mock_bottom_bar):
    """RenderEngine 实例，依赖均已 mock。"""
    return RenderEngine(mock_renderer, mock_bottom_bar)


# ══════════════════════════════════════════════════════
# 辅助上下文管理器：mock threading.Thread
# ══════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _mock_threading_thread():
    """全局 mock threading.Thread，避免实际启动线程。

    使用 autouse=True，所有依赖 engine.start() 的测试自动生效。
    start() 内部创建 Thread(target=self._render, daemon=True) 并 .start()。
    mock 后 Thread 不真正启动，_render 循环不会运行。
    需要测试 _render 方法时单独 patch。
    """
    with patch("threading.Thread") as mt:
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = False
        mt.return_value = mock_instance
        yield mt


# ══════════════════════════════════════════════════════
# TestRenderEnginePushCmd
# ══════════════════════════════════════════════════════

class TestRenderEnginePushCmd:
    """push_cmd 入队 / 满队列 / 连续满警告 / ERROR 直写路径。"""

    def test_push_cmd_enqueues_content_command(self, engine):
        """CONTENT 命令成功入队 → 队列不为空，cmd_event 被设置。"""
        assert engine._cmd_queue.empty()
        engine._cmd_event.clear()

        engine.push_cmd((RenderCommand.CONTENT, "hello"))

        assert engine._cmd_queue.qsize() == 1
        assert engine._cmd_event.is_set()
        assert engine._consecutive_full == 0

    def test_push_cmd_enqueues_phase_done(self, engine):
        """PHASE_DONE 命令成功入队。"""
        engine.push_cmd((RenderCommand.PHASE_DONE, "思考"))
        cmd = engine._cmd_queue.get_nowait()
        assert cmd == (RenderCommand.PHASE_DONE, "思考")

    def test_push_cmd_resets_consecutive_full_on_success(self, engine):
        """入队成功时 _consecutive_full 清零。"""
        engine._consecutive_full = 5
        engine.push_cmd((RenderCommand.CONTENT, "x"))
        assert engine._consecutive_full == 0

    def test_push_cmd_queue_full_logs_warning(self, engine, caplog):
        """队列满时丢弃命令并记录 warning（非 ERROR 命令）。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "已占位"), block=False)
        engine._cmd_queue = tiny_queue
        caplog.set_level(logging.WARNING)

        engine.push_cmd((RenderCommand.CONTENT, "被丢弃"))

        assert engine._consecutive_full >= 1
        assert "渲染命令队列已满" in caplog.text
        assert "CONTENT" in caplog.text

    def test_push_cmd_error_writes_directly_to_terminal(self, engine):
        """ERROR 命令在队列满时走直写终端路径，不记录日志。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue

        with patch.object(sys, "__stdout__") as mock_stdout:
            engine.push_cmd((RenderCommand.ERROR, "系统繁忙"))

        # 验证直写终端
        mock_stdout.write.assert_called_once()
        text = mock_stdout.write.call_args[0][0]
        assert "队列拥堵" in text
        assert "系统繁忙" in text
        mock_stdout.flush.assert_called_once()

    def test_push_cmd_error_no_message_fallback(self, engine):
        """ERROR 命令无消息时回退到「未知错误」。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue

        with patch.object(sys, "__stdout__") as mock_stdout:
            engine.push_cmd((RenderCommand.ERROR,))

        text = mock_stdout.write.call_args[0][0]
        assert "未知错误" in text

    def test_push_cmd_consecutive_full_warns_terminal(self, engine):
        """连续满超过阈值时输出终端警告。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue
        engine._CONSECUTIVE_FULL_THRESHOLD = 3

        with patch.object(sys, "__stdout__") as mock_stdout:
            for _ in range(3):
                engine.push_cmd((RenderCommand.CONTENT, "丢弃"))

        # 第3次 push 时 _consecutive_full == 3 >= 3，触发警告
        terminal_warning_calls = [
            c for c in mock_stdout.write.call_args_list
            if "渲染输出管线持续拥堵" in c[0][0]
        ]
        assert len(terminal_warning_calls) == 1

    def test_push_cmd_consecutive_full_below_threshold(self, engine):
        """连续满未达阈值时不输出终端警告。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue
        engine._CONSECUTIVE_FULL_THRESHOLD = 10

        with patch.object(sys, "__stdout__") as mock_stdout:
            for _ in range(5):
                engine.push_cmd((RenderCommand.CONTENT, "丢弃"))

        terminal_warning_calls = [
            c for c in mock_stdout.write.call_args_list
            if "渲染输出管线持续拥堵" in c[0][0]
        ]
        assert len(terminal_warning_calls) == 0

    def test_push_cmd_success_resets_consecutive_after_full(self, engine):
        """满队列后下一次成功入队 → _consecutive_full 清零。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue

        # 第一次满队列（队列已占位，再 push 触发 Full）
        engine.push_cmd((RenderCommand.CONTENT, "a"))
        assert engine._consecutive_full == 1

        # 重建正常队列后 push 成功 → 清零
        engine._cmd_queue = queue.Queue(maxsize=10000)
        engine.push_cmd((RenderCommand.CONTENT, "成功"))
        assert engine._consecutive_full == 0

    def test_push_cmd_cmd_event_set_on_success(self, engine):
        """成功入队时 cmd_event 被 set，唤醒 render 线程。"""
        engine._cmd_event.clear()
        engine.push_cmd((RenderCommand.NOTIFICATION, "测试"))
        assert engine._cmd_event.is_set()


# ══════════════════════════════════════════════════════
# TestRenderEngineStartStop
# ══════════════════════════════════════════════════════

class TestRenderEngineStartStop:
    """start / stop 生命周期管理、幂等性、重启。"""

    def test_start_creates_daemon_thread(self, engine):
        """start() 创建 daemon Thread 并启动。"""
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_t.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t

            engine.start()

            # 验证 Thread 构造函数参数
            mock_thread_cls.assert_called_once_with(
                target=engine._render, daemon=True,
            )
            mock_t.start.assert_called_once()
            assert engine._render_running is True
            assert engine._render_thread is mock_t

    def test_start_repeat_skips_when_alive(self, engine):
        """重复 start() 且线程存活 → 跳过（幂等）。"""
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_t.is_alive.return_value = True  # 第一次 start 后存活
            mock_thread_cls.return_value = mock_t

            engine.start()  # 第一次
            mock_thread_cls.reset_mock()
            mock_t.start.reset_mock()

            engine.start()  # 第二次（线程存活）

            # 不应创建新线程
            mock_thread_cls.assert_not_called()
            mock_t.start.assert_not_called()

    def test_start_repeat_joins_dead_and_creates_new(self, engine):
        """重复 start() 且线程已死 → join 清理后创建新线程。"""
        with patch("threading.Thread") as mock_thread_cls:
            mock_t1 = MagicMock()
            mock_t1.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t1

            engine.start()  # 第一次
            engine._render_running = False  # 模拟线程结束

            # 第二次 start
            mock_t2 = MagicMock()
            mock_t2.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t2

            engine.start()

            # 第一次的线程被 join
            mock_t1.join.assert_called_once()
            # 第二次创建了新线程
            assert engine._render_thread is mock_t2
            mock_t2.start.assert_called_once()

    def test_start_initial_state_no_thread(self, engine):
        """首次 start() 时 _render_thread 为 None → 直接创建新线程。"""
        assert engine._render_thread is None

        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_t.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t

            engine.start()

            mock_thread_cls.assert_called_once_with(
                target=engine._render, daemon=True,
            )

    def test_stop_stops_thread(self, engine):
        """stop() 设置 _render_running=False + 唤醒 + join。"""
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_t.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t
            engine._render_thread = mock_t
            engine._render_running = True

            engine.stop()

            assert engine._render_running is False
            assert engine._cmd_event.is_set()  # 被唤醒
            mock_t.join.assert_called_once_with(timeout=2.0)

    def test_stop_idempotent(self, engine):
        """stop() 空状态（无线程）→ 安全跳过。"""
        engine._render_thread = None
        engine._render_running = False
        # 不应抛出异常
        engine.stop()

    def test_stop_wakes_render_with_event(self, engine):
        """stop() 设置 cmd_event 唤醒 render 线程。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = False
        engine._render_thread = mock_t
        engine._render_running = True
        engine._cmd_event.clear()

        engine.stop()

        assert engine._cmd_event.is_set()

    def test_stop_retries_if_join_timeout(self, engine):
        """stop() join 超时后多次唤醒 + join 重试。"""
        mock_t = MagicMock()
        # is_alive 前 3 次 True（超时），第 4 次 False（重试后成功）
        mock_t.is_alive.side_effect = [True, True, True, False]
        engine._render_thread = mock_t
        engine._render_running = True

        engine.stop()

        # 在第 1 次 join(timeout=2) 超时后，有 3 轮唤醒+重试
        assert mock_t.is_alive.call_count >= 2
        assert engine._render_running is False

    def test_stop_retries_exhausted(self, engine):
        """stop() 所有重试都用完 → 不再尝试，返回。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True  # 始终存活
        engine._render_thread = mock_t
        engine._render_running = True

        engine.stop()

        # 1 次初期 join(2s) + 3 次重试 join(0.5s)
        join_calls = mock_t.join.call_args_list
        assert len(join_calls) == 4
        assert join_calls[0] == call(timeout=2.0)
        for jc in join_calls[1:]:
            assert jc == call(timeout=0.5)

    def test_start_after_stop_restart(self, engine):
        """start() → stop() → start() 可重启。"""
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_t.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t

            # 第一次 start
            engine.start()
            first_thread = engine._render_thread
            engine._render_running = False

            mock_t.is_alive.return_value = False
            # stop
            engine.stop()

            # 第二次 start
            mock_t2 = MagicMock()
            mock_t2.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t2

            engine.start()

            assert engine._render_thread is mock_t2
            assert engine._render_running is True
            mock_t2.start.assert_called_once()

    def test_start_sets_render_running(self, engine):
        """start() 将 _render_running 置 True。"""
        assert engine._render_running is False
        with patch("threading.Thread"):
            engine.start()
        assert engine._render_running is True


# ══════════════════════════════════════════════════════
# TestRenderEngineFlush
# ══════════════════════════════════════════════════════

class TestRenderEngineFlush:
    """flush 等待队列消费完毕 / render 未运行时清空 / 超时返回。"""

    def test_flush_render_not_started_drains_queue(self, engine):
        """render 线程从未启动（None）→ 直接清空队列。"""
        engine._render_thread = None
        engine._cmd_queue.put((RenderCommand.CONTENT, "a"))
        engine._cmd_queue.put((RenderCommand.CONTENT, "b"))
        assert engine._cmd_queue.qsize() == 2

        engine.flush(timeout=1.0)

        assert engine._cmd_queue.empty()

    def test_flush_render_dead_drains_queue(self, engine):
        """render 线程已死 → 直接清空队列。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = False
        engine._render_thread = mock_t
        engine._cmd_queue.put((RenderCommand.CONTENT, "x"))
        engine._cmd_queue.put((RenderCommand.CONTENT, "y"))

        engine.flush(timeout=1.0)

        assert engine._cmd_queue.empty()
        # task_done 应该被调用了
        assert engine._cmd_queue.qsize() == 0

    def test_flush_empty_queue_returns_quickly(self, engine):
        """空队列时 flush 快速返回。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            mt.return_value = task_done_t

            engine.flush(timeout=1.0)

            mt.assert_called_once_with(target=engine._cmd_queue.join, daemon=True)
            task_done_t.start.assert_called_once()
            task_done_t.join.assert_called_once_with(timeout=1.0)

    def test_flush_with_render_alive_uses_queue_join(self, engine):
        """render 线程存活时通过 queue.join() 等待消费完毕。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            mt.return_value = task_done_t

            engine.flush(timeout=5.0)

            mt.assert_called_once_with(target=engine._cmd_queue.join, daemon=True)
            task_done_t.start.assert_called_once()
            task_done_t.join.assert_called_once_with(timeout=5.0)

    def test_flush_timeout_returns_early(self, engine):
        """flush 超时后返回（不阻塞永久）。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            task_done_t.join.side_effect = lambda timeout=None: None  # 超时
            mt.return_value = task_done_t

            # 不应阻塞
            engine.flush(timeout=0.1)

            task_done_t.join.assert_called_once_with(timeout=0.1)

    def test_flush_sets_cmd_event_to_wake_render(self, engine):
        """flush 先设置 cmd_event 唤醒 render。"""
        engine._cmd_event.clear()
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            mt.return_value = MagicMock()

            engine.flush(timeout=1.0)

        assert engine._cmd_event.is_set()

    def test_flush_infinite_wait(self, engine):
        """flush(timeout=None) 无限等待。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            mt.return_value = task_done_t

            engine.flush(timeout=None)

            task_done_t.join.assert_called_once_with(timeout=None)


# ══════════════════════════════════════════════════════
# TestRenderEngineDrainQueue
# ══════════════════════════════════════════════════════

class TestRenderEngineDrainQueue:
    """_drain_queue 空跳过 / 三阶段流水线 / 容错 / 底部栏触发。"""

    def test_drain_empty_queue_skip(self, engine):
        """空队列 + 无面板 + 状态行不活跃 + 无 resize → 快速跳过。"""
        engine._bb.is_status_active = False
        engine._bb.is_resize_pending = False

        # 确保空队列
        while not engine._cmd_queue.empty():
            engine._cmd_queue.get_nowait()
            engine._cmd_queue.task_done()

        with patch.object(engine, "_check_resize") as m_check:
            # drain_queue 应直接 return，不会进入锁和后续逻辑
            engine._drain_queue()

            m_check.assert_not_called()

    def test_drain_empty_queue_with_status_active_triggers_redraw(
        self, engine,
    ):
        """空队列但 is_status_active == True → 不跳过，执行流水线。"""
        engine._bb.is_status_active = True
        engine._cmd_queue = queue.Queue()
        engine._bb.check_resize.return_value = False

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
            patch.object(engine, "position_cursor") as m_pos,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

            # force_redraw 被调用（is_status_active True）
            engine._bb.force_redraw.assert_called_once()
            # position_cursor 也被调用
            m_pos.assert_called_once()

    def test_drain_commands_renders_in_order(self, engine):
        """有命令时批量出队并按顺序渲染。"""
        engine._bb.is_status_active = False
        engine._bb.is_resize_pending = False
        engine._bb.check_resize.return_value = False

        engine._cmd_queue.put((RenderCommand.CONTENT, "hello"))
        engine._cmd_queue.put((RenderCommand.CONTENT, "world"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 两个命令按顺序渲染
        assert engine._renderer.render.call_count == 2
        engine._renderer.render.assert_has_calls([
            call((RenderCommand.CONTENT, "hello")),
            call((RenderCommand.CONTENT, "world")),
        ])

    def test_drain_renders_without_resize_calls_sync_and_cursor_upper(
        self, engine,
    ):
        """非 resize 路径：先 sync_bottom_lines 再 ensure_cursor_upper。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = False  # 非 resize

        engine._cmd_queue.put((RenderCommand.CONTENT, "test"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 非 resize 路径：sync_bottom_lines + ensure_cursor_upper
        engine._bb.sync_bottom_lines.assert_called_once()
        engine._bb.ensure_cursor_in_upper.assert_called_once()

    def test_drain_renders_with_resize_skips_sync_and_cursor_upper(
        self, engine,
    ):
        """resize 路径：跳过 sync_bottom_lines 和 ensure_cursor_upper。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = True  # resize 发生

        engine._cmd_queue.put((RenderCommand.CONTENT, "test"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # resize 路径：不应调用 sync_bottom_lines 和 ensure_cursor_upper
        engine._bb.sync_bottom_lines.assert_not_called()
        engine._bb.ensure_cursor_in_upper.assert_not_called()

    def test_drain_render_exception_tolerated_and_queues_error(
        self, engine, caplog,
    ):
        """渲染命令异常时被容错（记录日志 + push ERROR 命令）。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = False
        engine._renderer.render.side_effect = RuntimeError("渲染失败")
        caplog.set_level(logging.DEBUG)

        engine._cmd_queue.put((RenderCommand.CONTENT, "坏数据"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 异常被记录，不传播
        assert "渲染命令" in caplog.text
        # ERROR 命令被推回队列
        assert engine._cmd_queue.qsize() == 1
        err_cmd = engine._cmd_queue.get_nowait()
        assert err_cmd[0] == RenderCommand.ERROR

    def test_drain_subagent_panel_refreshed(self, engine):
        """SubAgentPanelControl 需要刷新时阶段2入队 SUBAGENT_REFRESH。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = False
        mock_panel = MagicMock()
        mock_panel.needs_refresh.return_value = True

        engine._cmd_queue.put((RenderCommand.CONTENT, "数据"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", mock_panel),
            patch.object(engine, 'push_cmd') as mock_push,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 验证 SUBAGENT_REFRESH 命令已入队
        mock_push.assert_any_call((RenderCommand.SUBAGENT_REFRESH, False))

    def test_drain_subagent_panel_needs_refresh_false_skip(self, engine):
        """SubAgentPanelControl.needs_refresh() 返回 False 时跳过入队。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = False
        mock_panel = MagicMock()
        mock_panel.needs_refresh.return_value = False

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", mock_panel),
            patch.object(engine, 'push_cmd') as mock_push,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # SUBAGENT_REFRESH 不应入队
        for call_args in mock_push.call_args_list:
            cmd = call_args[0][0]
            assert cmd[0] != RenderCommand.SUBAGENT_REFRESH, (
                "SUBAGENT_REFRESH 不应被推送"
            )

    def test_drain_force_redraw_with_commands(self, engine):
        """有命令时不论 is_status_active 都触发 force_redraw。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = False

        engine._cmd_queue.put((RenderCommand.CONTENT, "数据"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        engine._bb.force_redraw.assert_called_once()

    def test_drain_force_redraw_with_status_active_only(self, engine):
        """无命令但 is_status_active → 触发 force_redraw。"""
        engine._bb.is_status_active = True
        engine._bb.check_resize.return_value = False

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        engine._bb.force_redraw.assert_called_once()

    def test_drain_no_force_redraw_when_no_commands_and_no_status(
        self, engine,
    ):
        """无命令且 is_status_active=False → 不触发 force_redraw。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = True  # 有 resize，进入锁

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        engine._bb.force_redraw.assert_not_called()

    def test_drain_lock_timeout_returns(self, engine):
        """output_lock 超时 → 方法直接返回，不执行渲染。"""
        engine._bb.is_status_active = True  # 否则会跳过

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = False  # 锁未获取

            engine._drain_queue()

        # 锁没拿到，不应执行任何渲染
        engine._renderer.render.assert_not_called()
        engine._bb.force_redraw.assert_not_called()

    def test_drain_check_resize_exception_tolerated(self, engine):
        """check_resize 异常时被容错（resized=False），继续流水线。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.side_effect = RuntimeError("resize 检测异常")

        engine._cmd_queue.put((RenderCommand.CONTENT, "数据"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            # 不应抛出异常
            engine._drain_queue()

        # resized=False 路径：仍走 sync + cursor_upper
        engine._bb.sync_bottom_lines.assert_called_once()

    def test_drain_sync_bottom_lines_exception_tolerated(self, engine):
        """sync_bottom_lines 异常时被容错，继续渲染。"""
        engine._bb.is_status_active = False
        engine._bb.check_resize.return_value = False
        engine._bb.sync_bottom_lines.side_effect = RuntimeError("sync 异常")

        engine._cmd_queue.put((RenderCommand.CONTENT, "数据"))

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 渲染仍成功
        engine._renderer.render.assert_called_once()

    def test_drain_force_redraw_exception_tolerated(self, engine):
        """force_redraw 异常时被容错，继续光标定位。"""
        engine._bb.is_status_active = True
        engine._bb.check_resize.return_value = False
        engine._bb.force_redraw.side_effect = RuntimeError("redraw 异常")

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
            patch.object(engine, "position_cursor") as m_pos,
        ):
            m_lock.return_value.__enter__.return_value = True

            # 不应抛出异常
            engine._drain_queue()

        # position_cursor 仍被调用
        m_pos.assert_called_once()

    def test_drain_position_cursor_exception_tolerated(self, engine):
        """position_cursor 异常时被容错。"""
        engine._bb.is_status_active = True
        engine._bb.check_resize.return_value = False
        engine.position_cursor = MagicMock()
        engine.position_cursor.side_effect = RuntimeError("光标异常")

        with (
            patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
            patch("src.chat_ui._state._active_subagent_panel", None),
        ):
            m_lock.return_value.__enter__.return_value = True

            # 不应抛出异常
            engine._drain_queue()


# ══════════════════════════════════════════════════════
# TestRenderEngineRender
# ══════════════════════════════════════════════════════

class TestRenderEngineRender:
    """_render 循环 / 异常崩溃 / 终止。"""

    def test_render_runs_while_flag_true(self, engine):
        """_render_running=True 时循环持续运行。"""
        # 精确控制循环次数
        engine._render_running = True
        engine._cmd_event.wait = MagicMock(side_effect=[
            None, None, KeyboardInterrupt,  # 第3次抛出 KeyboardInterrupt 跳出
        ])
        engine._drain_queue = MagicMock()

        with pytest.raises(KeyboardInterrupt):
            engine._render()

        # _drain_queue 被调用了 3 次
        assert engine._drain_queue.call_count == 3

    def test_render_clears_event_after_wait(self, engine):
        """每次循环后清除 cmd_event。"""
        engine._render_running = True
        engine._cmd_event.wait = MagicMock(side_effect=[None, KeyboardInterrupt])
        engine._drain_queue = MagicMock()
        engine._cmd_event.clear = MagicMock()

        with pytest.raises(KeyboardInterrupt):
            engine._render()

        # clear 被调用
        engine._cmd_event.clear.assert_called_once()

    def test_render_exception_logs_and_stops(self, engine, caplog):
        """_render 中异常时记录 critical 日志并设置 _render_running=False。"""
        engine._render_running = True
        engine._drain_queue = MagicMock(side_effect=[
            None,  # 第一次正常
            RuntimeError("模拟崩溃"),  # 第二次崩溃
        ])
        engine._cmd_event.wait = MagicMock(return_value=None)
        caplog.set_level(logging.CRITICAL)

        with patch.object(sys, "__stderr__") as mock_stderr:
            engine._render()

        # _render_running 被置 False
        assert engine._render_running is False
        assert "render 线程异常崩溃" in caplog.text
        # 终端也输出告警
        mock_stderr.write.assert_called_once()
        stderr_text = mock_stderr.write.call_args[0][0]
        assert "render 线程异常终止" in stderr_text

    def test_render_stops_when_flag_false(self, engine):
        """_render_running=False 时循环退出。"""
        engine._render_running = False  # 初始就是 False
        engine._drain_queue = MagicMock()
        engine._cmd_event.wait = MagicMock()

        engine._render()

        # 循环不进入 body
        engine._drain_queue.assert_not_called()
        engine._cmd_event.wait.assert_not_called()


# ══════════════════════════════════════════════════════
# TestRenderEnginePositionCursor / EnsureCursorUpper
# ══════════════════════════════════════════════════════

class TestRenderEnginePositionCursor:
    """position_cursor 光标定位逻辑。"""

    def test_position_cursor_writes_ansi_escape(self, engine):
        """position_cursor 向 stdout 写入光标定位序列。

        通过 _BottomBar.compute_cursor_position() 公开 API 计算光标位置。
        """
        engine._bb.get_cursor_info.return_value = ("hello world", 5, 30, 80)
        engine._bb.compute_cursor_position.return_value = (28, 8)

        # Blessed 在非 TTY 环境下格式化序列返回空字符串，
        # 因此 mock get_terminal 返回模拟终端
        mock_term = MagicMock()
        mock_term.move_xy.return_value = "\033[28;8H"
        with patch("src.chat_ui._engine.get_terminal", return_value=mock_term):
            with patch.object(sys, "__stdout__") as mock_stdout:
                engine.position_cursor()

        # 验证调用了 compute_cursor_position
        engine._bb.compute_cursor_position.assert_called_once_with(
            "hello world", 5, 30, 80,
        )
        # 验证写入了光标定位序列
        mock_stdout.write.assert_called_once()
        text = mock_stdout.write.call_args[0][0]
        assert '\033[' in text, f"应包含 ANSI 光标定位序列，实际: {text!r}"
        mock_stdout.flush.assert_called_once()

    def test_position_cursor_uses_compute_cursor_position(self, engine):
        """position_cursor 调用 compute_cursor_position 公开 API。"""
        engine._bb.get_cursor_info.return_value = ("hello", 5, 30, 80)
        engine._bb.compute_cursor_position.return_value = (27, 8)

        with patch.object(sys, "__stdout__"):
            engine.position_cursor()

        engine._bb.compute_cursor_position.assert_called_once_with(
            "hello", 5, 30, 80,
        )

    def test_position_cursor_min_input_rows(self, engine):
        """compute_cursor_position 内部处理总行数最小值。"""
        engine._bb.get_cursor_info.return_value = ("hi", 2, 25, 60)
        engine._bb.compute_cursor_position.return_value = (23, 5)

        with patch.object(sys, "__stdout__") as mock_stdout:
            engine.position_cursor()

        mock_stdout.write.assert_called_once()

    def test_position_cursor_with_popup(self, engine):
        """补全弹窗高度影响光标行偏移。"""
        engine._bb.get_cursor_info.return_value = ("some text", 3, 40, 100)
        engine._bb.compute_cursor_position.return_value = (40, 13)

        mock_term = MagicMock()
        mock_term.move_xy.return_value = "\033[40;13H"
        with patch("src.chat_ui._engine.get_terminal", return_value=mock_term):
            with patch.object(sys, "__stdout__") as mock_stdout:
                engine.position_cursor()

        text = mock_stdout.write.call_args[0][0]
        assert '\033[' in text, f"应包含 ANSI 光标定位序列，实际: {text!r}"


class TestRenderEngineEnsureCursorUpper:
    """ensure_cursor_upper 委托到底部栏。"""

    def test_ensure_cursor_upper_delegates_to_bottom_bar(self, engine):
        """ensure_cursor_upper 调用 _bb.ensure_cursor_in_upper()。"""
        engine.ensure_cursor_upper()
        engine._bb.ensure_cursor_in_upper.assert_called_once()

    def test_ensure_cursor_upper_method_bound(self, engine):
        """验证 ensure_cursor_upper 是绑定的实例方法。"""
        assert hasattr(engine, "ensure_cursor_upper")
        assert callable(engine.ensure_cursor_upper)


# ══════════════════════════════════════════════════════
# TestRenderEngineEdgeCases
# ══════════════════════════════════════════════════════

class TestRenderEngineEdgeCases:
    """其他边界 / 集成验证。"""

    def test_init_sets_defaults(self, mock_renderer, mock_bottom_bar):
        """验证 __init__ 默认值正确。"""
        engine = RenderEngine(mock_renderer, mock_bottom_bar)

        assert engine._renderer is mock_renderer
        assert engine._bb is mock_bottom_bar
        assert engine._cmd_queue.maxsize == 10000
        assert engine._render_thread is None
        assert engine._render_running is False
        assert engine._consecutive_full == 0
        assert isinstance(engine._cmd_queue, queue.Queue)
        assert isinstance(engine._cmd_event, threading.Event)

    def test_flush_drains_multiple_commands_in_order(self, engine):
        """flush 时队列中有多条命令 → 全部清空。"""
        engine._render_thread = None  # 模拟 render 未启动
        engine._cmd_queue.put((RenderCommand.CONTENT, "a"))
        engine._cmd_queue.put((RenderCommand.PHASE_DONE, "思考"))
        engine._cmd_queue.put((RenderCommand.NOTIFICATION, "通知"))

        engine.flush(timeout=1.0)

        assert engine._cmd_queue.empty()

    def test_push_cmd_event_only_set_on_success(self, engine):
        """入队成功时 set event，失败时不 set（事件已在 set 过程中）。"""
        # 满队列
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue
        engine._cmd_event.clear()

        # 满队列时不会 set event
        engine.push_cmd((RenderCommand.CONTENT, "丢弃"))
        # event 在 push 过程中被 set/unset — 但满队列路径没走 set 调用
        # （set 仅在 try 分支中被调用）
        # 所以 event 仍然 clear
        assert not engine._cmd_event.is_set()

    def test_render_uses_render_interval(self, engine):
        """_render 中 cmd_event.wait(timeout=_RENDER_INTERVAL)。"""
        from src.chat_ui._const import _RENDER_INTERVAL

        engine._render_running = True

        # _drain_queue 第1次正常完成，第2次退出循环
        call_count = 0

        def _side_effect_drain():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                engine._render_running = False

        engine._drain_queue = MagicMock(side_effect=_side_effect_drain)
        engine._cmd_event.wait = MagicMock()
        engine._cmd_event.clear = MagicMock()

        engine._render()

        # 验证每次 wait 都带正确的 timeout（循环运行了 2 轮）
        assert engine._cmd_event.wait.call_count >= 1
        engine._cmd_event.wait.assert_called_with(timeout=_RENDER_INTERVAL)

    def test_drain_check_width_exception_safe(self, engine):
        """_check_resize 异常时被静默忽略。"""
        engine._bb.is_status_active = True
        with patch.object(engine, "_check_resize", side_effect=OSError("终端不可用")):
            with (
                patch("src.chat_ui._engine._try_acquire_output_lock") as m_lock,
                patch("src.chat_ui._state._active_subagent_panel", None),
            ):
                m_lock.return_value.__enter__.return_value = True

                # 不抛异常
                engine._drain_queue()

            engine._bb.force_redraw.assert_called_once()

    def test_drain_lock_name_is_drain_queue(self, engine):
        """_try_acquire_output_lock 的 name 参数为 'drain_queue'。"""
        engine._bb.is_status_active = True

        with patch(
            "src.chat_ui._engine._try_acquire_output_lock",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=True),
                __exit__=MagicMock(),
            ),
        ) as m_lock:
            engine._drain_queue()

            m_lock.assert_called_once_with(name="drain_queue", timeout=1.0)


# ══════════════════════════════════════════════════════
# TestRenderEngineCheckResize — _check_resize() 单元测试
# ══════════════════════════════════════════════════════

class TestRenderEngineCheckResize:
    """RenderEngine._check_resize() 测试（迁移自 ContentRenderer._check_and_refresh_width）"""

    @pytest.fixture
    def engine_with_resize_state(self, engine):
        """配置 engine 的 resize 状态字段（已从 ContentRenderer 迁移至 RenderEngine）。"""
        engine._last_width_check = 0.0
        engine._cached_term_size = (0, 0)
        engine._RESIZE_CHECK_INTERVAL = 0.2
        engine._renderer._rs = MagicMock()
        engine._renderer._control_list = MagicMock()
        return engine

    def test_no_check_within_interval(self, engine_with_resize_state):
        """200ms 间隔内不检查终端大小"""
        engine = engine_with_resize_state
        with patch('shutil.get_terminal_size') as mock_gs:
            mock_gs.return_value = MagicMock(columns=100, lines=40)
            engine._check_resize()

        # 立即再次调用 → 200ms 内不应再检查
        with patch('shutil.get_terminal_size') as mock_gs2:
            engine._check_resize()
            mock_gs2.assert_not_called()

    def test_size_change_triggers_refresh(self, engine_with_resize_state):
        """终端大小变化 → 调用 renderer.refresh_width()"""
        engine = engine_with_resize_state

        with patch('shutil.get_terminal_size') as mock_gs:
            mock_gs.return_value = MagicMock(columns=100, lines=40)
            engine._check_resize()
            assert engine._cached_term_size == (100, 40)

        engine._renderer.refresh_width.reset_mock()

        engine._last_width_check = 0
        with patch('shutil.get_terminal_size') as mock_gs2:
            mock_gs2.return_value = MagicMock(columns=80, lines=30)
            engine._check_resize()
            engine._renderer.refresh_width.assert_called_once()
            assert engine._cached_term_size == (80, 30)

    def test_size_unchanged_no_refresh(self, engine_with_resize_state):
        """终端大小未变 → 不调用 renderer.refresh_width()"""
        engine = engine_with_resize_state

        with patch('shutil.get_terminal_size') as mock_gs:
            mock_gs.return_value = MagicMock(columns=100, lines=40)
            engine._check_resize()
            assert engine._cached_term_size == (100, 40)

        engine._renderer.refresh_width.reset_mock()

        engine._last_width_check = 0
        with patch('shutil.get_terminal_size') as mock_gs2:
            mock_gs2.return_value = MagicMock(columns=100, lines=40)
            engine._check_resize()
            engine._renderer.refresh_width.assert_not_called()

    def test_shutil_exception_safe(self, engine_with_resize_state):
        """shutil.get_terminal_size() 异常 → 安全返回，不崩溃"""
        engine = engine_with_resize_state

        with patch('shutil.get_terminal_size', side_effect=OSError("模拟异常")):
            engine._check_resize()

        engine._renderer.refresh_width.assert_not_called()
