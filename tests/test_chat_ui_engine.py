"""RenderEngine 单元测试 — 命令队列 / render 线程 / 渲染循环。

测试范围：
1. TestRenderEnginePushCmd    — push_cmd 入队/满队列/连续满/ERROR 直写
2. TestRenderEngineStartStop  — start/stop 生命周期管理/幂等/重启
3. TestRenderEngineFlush      — flush 等待消费/render 未运行时清空/超时
4. TestRenderEngineDrainQueue — _drain_queue 空跳过/流水线/容错/底部栏重绘
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

sys.path.insert(0, "/home/DeepSeek-cli")

from src.tui.engine.const import RenderCommand, _MAX_BATCH_SIZE
from src.tui.engine.utils import _cmd_name
from src.tui.engine.engine import TuiEngine as RenderEngine, _ACTIVE_RENDER_INTERVAL


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
      - is_status_active（property）
      - sync_bottom_lines / force_redraw / get_cursor_info
      - compute_cursor_position（公开 API）
      - ensure_cursor_in_upper
    """
    bb = MagicMock()
    bb.is_status_active = False
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
    """push_cmd 入队 / 满队列 / 连续满警告。"""

    def test_push_cmd_enqueues_content_command(self, engine):
        """CONTENT 命令成功入队 → 队列不为空，cmd_event 被 set 以立即唤醒 render 线程。"""
        assert engine._cmd_queue.empty()
        engine._cmd_event.clear()

        engine.push_cmd((RenderCommand.CONTENT, "hello"))

        assert engine._cmd_queue.qsize() == 1
        assert engine._cmd_event.is_set()  # Bug fix: push_cmd 立即唤醒 render 线程
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
        """队列满时丢弃命令并记录 warning。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "已占位"), block=False)
        engine._cmd_queue = tiny_queue
        caplog.set_level(logging.WARNING)

        engine.push_cmd((RenderCommand.CONTENT, "被丢弃"))

        assert engine._consecutive_full >= 1
        assert "渲染命令队列已满" in caplog.text
        assert "CONTENT" in caplog.text

    def test_push_cmd_consecutive_full_warns_log(self, engine, caplog):
        """连续满超过阈值时记录日志错误，不再写终端。"""
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue
        engine._CONSECUTIVE_FULL_THRESHOLD = 3

        caplog.set_level(logging.ERROR)

        with patch.object(sys, "__stdout__") as mock_stdout:
            for _ in range(3):
                engine.push_cmd((RenderCommand.CONTENT, "丢弃"))

        # 不再写终端
        terminal_warning_calls = [
            c for c in mock_stdout.write.call_args_list
            if "渲染输出管线持续拥堵" in c[0][0]
        ] if mock_stdout.write.call_args_list else []
        assert len(terminal_warning_calls) == 0
        # 改为日志记录
        assert "渲染输出管线持续拥堵" in caplog.text

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
        """Bug fix: 成功入队时 set cmd_event 以立即唤醒 render 线程。"""
        engine._cmd_event.clear()
        engine.push_cmd((RenderCommand.NOTIFICATION, "测试"))
        assert engine._cmd_event.is_set()

    def test_request_bottom_redraw_sets_cmd_event(self, engine):
        """修复 B: request_bottom_redraw 同时设置 _cmd_event 以立即唤醒 render 线程。

        验证：
        - _cmd_event.is_set() 为 True
        - _bottom_redraw_requested.is_set() 同时为 True
        - 此行为在 engine 初始化后的默认状态（event 初始为 clear）下正确工作
        """
        engine._cmd_event.clear()
        engine._bottom_redraw_requested.clear()

        engine.request_bottom_redraw()

        assert engine._bottom_redraw_requested.is_set(), (
            "_bottom_redraw_requested 应在 request_bottom_redraw 后被设置"
        )
        assert engine._cmd_event.is_set(), (
            "_cmd_event 应在 request_bottom_redraw 后被设置以立即唤醒 render 线程"
        )

    def test_request_bottom_redraw_cmd_event_idempotent(self, engine):
        """request_bottom_redraw 的 _cmd_event.set() 为幂等操作（threading.Event 天然幂等）。"""
        # 连续两次调用不应抛出异常或产生副作用
        engine.request_bottom_redraw()
        engine.request_bottom_redraw()

        assert engine._bottom_redraw_requested.is_set()
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
        """stop() 设置 _render_running=False，线程靠 10Hz 心跳自唤醒退出。"""
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_t.is_alive.return_value = False
            mock_thread_cls.return_value = mock_t
            engine._render_thread = mock_t
            engine._render_running = True

            engine.stop()

            assert engine._render_running is False
            assert not engine._cmd_event.is_set()  # 不主动唤醒
            mock_t.join.assert_called_once_with(timeout=2.0)

    def test_stop_idempotent(self, engine):
        """stop() 空状态（无线程）→ 安全跳过。"""
        engine._render_thread = None
        engine._render_running = False
        # 不应抛出异常
        engine.stop()

    def test_stop_does_not_set_cmd_event(self, engine):
        """stop() 不主动设置 cmd_event，线程靠 10Hz 心跳自唤醒退出。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = False
        engine._render_thread = mock_t
        engine._render_running = True
        engine._cmd_event.clear()

        engine.stop()

        assert not engine._cmd_event.is_set()

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
        """空队列时 flush 快速返回（非 daemon 线程 join 队列）。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            task_done_t.is_alive.return_value = False  # 线程已完成，不触发 drain
            mt.return_value = task_done_t

            engine.flush(timeout=1.0)

            mt.assert_called_once_with(target=engine._cmd_queue.join, daemon=False)
            task_done_t.start.assert_called_once()
            task_done_t.join.assert_called_once_with(timeout=1.0)

    def test_flush_with_render_alive_uses_queue_join(self, engine):
        """render 线程存活时通过非 daemon 线程 queue.join() 等待消费完毕。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            task_done_t.is_alive.return_value = False  # 线程已完成
            mt.return_value = task_done_t

            engine.flush(timeout=5.0)

            mt.assert_called_once_with(target=engine._cmd_queue.join, daemon=False)
            task_done_t.start.assert_called_once()
            task_done_t.join.assert_called_once_with(timeout=5.0)

    def test_flush_timeout_returns_early(self, engine):
        """flush 超时后 drain 队列并重新 join（非 daemon 线程安全退出）。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            task_done_t.join.side_effect = lambda timeout=None: None  # 超时
            # 第一次 join(0.1) 超时后 is_alive 返回 True → 触发 drain + 第二次 join(1.0)
            task_done_t.is_alive.return_value = True
            mt.return_value = task_done_t

            # 不应阻塞
            engine.flush(timeout=0.1)

            # 第一次 join 带原始 timeout，超时后 drain 再 join(1.0)
            assert task_done_t.join.call_count == 2
            task_done_t.join.assert_any_call(timeout=0.1)
            task_done_t.join.assert_any_call(timeout=1.0)

    def test_flush_does_not_set_cmd_event(self, engine):
        """flush 不主动设置 cmd_event，线程靠 10Hz 心跳自唤醒消费。"""
        engine._cmd_event.clear()
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            mt.return_value = MagicMock()

            engine.flush(timeout=1.0)

        assert not engine._cmd_event.is_set()

    def test_flush_infinite_wait(self, engine):
        """flush(timeout=None) 无限等待（非 daemon 线程，超时后 drain 兜底）。"""
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        engine._render_thread = mock_t

        with patch("threading.Thread") as mt:
            task_done_t = MagicMock()
            # timeout=None 时 join(None) 不超时直接返回（mock 行为），
            # is_alive 为 True 触发 drain 路径 → 第二次 join(1.0)
            task_done_t.is_alive.return_value = True
            mt.return_value = task_done_t

            engine.flush(timeout=None)

            assert task_done_t.join.call_count == 2
            task_done_t.join.assert_any_call(timeout=None)
            task_done_t.join.assert_any_call(timeout=1.0)


# ══════════════════════════════════════════════════════
# TestRenderEngineDrainQueue
# ══════════════════════════════════════════════════════

class TestRenderEngineDrainQueue:
    """_drain_queue 三阶段流水线 / 容错 / 底部栏重绘。"""

    def test_drain_empty_queue_triggers_redraw_by_timer(self, engine):
        """空队列 + 状态不活跃 → 10Hz 定时器触发 force_redraw（首次调用时定时器已过期）。"""
        engine._bb.is_status_active = False

        # 确保空队列
        while not engine._cmd_queue.empty():
            engine._cmd_queue.get_nowait()
            engine._cmd_queue.task_done()

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 队列空时不渲染任何命令
        engine._renderer.render.assert_not_called()
        # 阶段 3：10Hz 定时器初始值为 0.0，首次调用必定触发 force_redraw
        engine._bb.force_redraw.assert_called_once()

    def test_drain_empty_queue_with_status_active_triggers_redraw(
        self, engine,
    ):
        """空队列但 is_status_active == True → 不跳过，执行流水线。"""
        engine._bb.is_status_active = True
        engine._cmd_queue = queue.Queue()

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
            patch.object(engine, "_position_cursor") as m_pos,
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

        engine._cmd_queue.put((RenderCommand.CONTENT, "hello"))
        engine._cmd_queue.put((RenderCommand.CONTENT, "world"))

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 两个命令按顺序渲染
        assert engine._renderer.render.call_count == 2
        engine._renderer.render.assert_has_calls([
            call((RenderCommand.CONTENT, "hello")),
            call((RenderCommand.CONTENT, "world")),
        ])

    def test_drain_calls_sync_and_cursor_upper(
        self, engine,
    ):
        """渲染前先 sync_bottom_lines 再 ensure_cursor_upper。"""
        engine._bb.is_status_active = False

        engine._cmd_queue.put((RenderCommand.CONTENT, "test"))

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # sync_bottom_lines + ensure_cursor_upper
        engine._bb.sync_bottom_lines.assert_called_once()
        engine._bb.ensure_cursor_in_upper.assert_called_once()



    def test_drain_render_exception_tolerated_and_queues_error(
        self, engine, caplog,
    ):
        """渲染命令异常时被容错（记录日志 + push ERROR 命令）。"""
        engine._bb.is_status_active = False
        engine._renderer.render.side_effect = RuntimeError("渲染失败")
        caplog.set_level(logging.DEBUG)

        engine._cmd_queue.put((RenderCommand.CONTENT, "坏数据"))

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 异常被记录，不传播
        assert "渲染命令" in caplog.text
        # ERROR 命令被推回队列
        assert engine._cmd_queue.qsize() == 1
        err_cmd = engine._cmd_queue.get_nowait()
        assert err_cmd[0] == RenderCommand.ERROR

    def test_drain_force_redraw_with_commands(self, engine):
        """有命令时不论 is_status_active 都触发 force_redraw。"""
        engine._bb.is_status_active = False

        engine._cmd_queue.put((RenderCommand.CONTENT, "数据"))

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        engine._bb.force_redraw.assert_called_once()

    def test_drain_force_redraw_with_status_active_only(self, engine):
        """无命令但 is_status_active → 触发 force_redraw。"""
        engine._bb.is_status_active = True

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        engine._bb.force_redraw.assert_called_once()

    def test_drain_no_force_redraw_when_no_commands_and_no_status(
        self, engine,
    ):
        """无命令且 is_status_active=False → 10Hz 定时器仍触发 force_redraw（首次调用）。"""
        engine._bb.is_status_active = False

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # ★ 10Hz 重构后：定时器初始值 0.0，首次调用时定时器已过期，强制触发
        engine._bb.force_redraw.assert_called_once()

    def test_drain_lock_timeout_returns(self, engine):
        """output_lock 超时 → 方法直接返回，不执行渲染。"""
        engine._bb.is_status_active = True  # 否则会跳过

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = False  # 锁未获取

            engine._drain_queue()

        # 锁没拿到，不应执行任何渲染
        engine._renderer.render.assert_not_called()
        engine._bb.force_redraw.assert_not_called()

    def test_drain_lock_timeout_panel_refresh_cb_still_called(self, engine):
        """★ 锁超时时 panel_refresh_cb 仍在锁外执行，SUBAGENT_FRAME 入队不被丢弃。

        验证 _phase_pre_update_panels() 移出锁后：
        - 即使 output_lock 获取失败，panel_refresh_cb 仍被调用
        - panel_refresh_cb 推入的 SUBAGENT_FRAME 命令留在队列中供下次 drain 消费
        """
        engine._bb.is_status_active = False

        # 设置 panel_refresh_cb：将 SUBAGENT_FRAME 命令推入队列
        captured_frames = []

        def panel_cb():
            captured_frames.append(True)
            engine._cmd_queue.put((RenderCommand.SUBAGENT_FRAME, ("line1", "line2")))

        engine.set_panel_refresh_callback(panel_cb)

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = False  # 锁超时

            result = engine._drain_queue()

        # 锁超时返回 False（未进入锁内渲染）
        assert result is False
        # panel_refresh_cb 在锁外执行，已被调用
        assert len(captured_frames) == 1, "锁超时时 panel_refresh_cb 仍应被调用"
        # SUBAGENT_FRAME 命令留在队列中供下次 drain 消费
        assert engine._cmd_queue.qsize() == 1, "SUBAGENT_FRAME 应留在队列中"
        cmd = engine._cmd_queue.get_nowait()
        assert cmd[0] == RenderCommand.SUBAGENT_FRAME
        assert cmd[1] == ("line1", "line2")
        # 锁没拿到，不执行任何渲染
        engine._renderer.render.assert_not_called()
        engine._bb.force_redraw.assert_not_called()

    def test_drain_panel_refresh_cb_called_before_lock(self, engine):
        """★ panel_refresh_cb 在获取锁之前被调用（锁外执行验证）。

        验证调用顺序：panel_refresh_cb → 获取锁 → 渲染命令。
        """
        engine._bb.is_status_active = False

        call_order = []

        def panel_cb():
            call_order.append("panel_cb")

        engine.set_panel_refresh_callback(panel_cb)
        engine._cmd_queue.put((RenderCommand.CONTENT, "hello"))

        # 创建一个记录调用顺序的 mock context manager
        class _CallTracker:
            def __enter__(self):
                call_order.append("lock_enter")
                return True
            def __exit__(self, *args):
                call_order.append("lock_exit")

        with patch("src.tui.widgets.lock._try_acquire_output_lock",
                   return_value=_CallTracker()):
            engine._drain_queue()

        # panel_refresh_cb 在锁获取之前被调用
        panel_idx = call_order.index("panel_cb")
        lock_idx = call_order.index("lock_enter")
        assert panel_idx < lock_idx, (
            f"panel_refresh_cb 应在锁获取之前调用，实际顺序: {call_order}"
        )
        # 渲染正常进行
        engine._renderer.render.assert_called_once()


    def test_drain_sync_bottom_lines_exception_tolerated(self, engine):
        """sync_bottom_lines 异常时被容错，继续渲染。"""
        engine._bb.is_status_active = False
        engine._bb.sync_bottom_lines.side_effect = RuntimeError("sync 异常")

        engine._cmd_queue.put((RenderCommand.CONTENT, "数据"))

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 渲染仍成功
        engine._renderer.render.assert_called_once()

    def test_drain_force_redraw_exception_tolerated(self, engine):
        """force_redraw 异常时被容错，继续光标定位。"""
        engine._bb.is_status_active = True
        engine._bb.force_redraw.side_effect = RuntimeError("redraw 异常")

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
            patch.object(engine, "_position_cursor") as m_pos,
        ):
            m_lock.return_value.__enter__.return_value = True

            # 不应抛出异常
            engine._drain_queue()

        # position_cursor 仍被调用
        m_pos.assert_called_once()

    def test_drain_position_cursor_exception_tolerated(self, engine):
        """position_cursor 异常时被容错。"""
        engine._bb.is_status_active = True
        engine._position_cursor = MagicMock()
        engine._position_cursor.side_effect = RuntimeError("光标异常")

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            # 不应抛出异常
            engine._drain_queue()

    # ── 批量命令处理测试（步骤 4 性能优化） ────────────

    def test_drain_batch_size_limit(self, engine):
        """入队 _MAX_BATCH_SIZE + 10 条命令 → drain_queue 只处理 _MAX_BATCH_SIZE 条。"""
        engine._bb.is_status_active = False

        # 清空队列
        while not engine._cmd_queue.empty():
            engine._cmd_queue.get_nowait()
            engine._cmd_queue.task_done()

        total = _MAX_BATCH_SIZE + 10
        for i in range(total):
            engine._cmd_queue.put((RenderCommand.CONTENT, f"cmd{i}"))

        assert engine._cmd_queue.qsize() == total

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 只处理了 _MAX_BATCH_SIZE 条
        assert engine._renderer.render.call_count == _MAX_BATCH_SIZE
        # 队列中剩余 10 条命令
        assert engine._cmd_queue.qsize() == 10

    def test_drain_batch_size_under_limit(self, engine):
        """入队 _MAX_BATCH_SIZE - 1 条命令 → drain_queue 全部处理。"""
        engine._bb.is_status_active = False

        # 清空队列
        while not engine._cmd_queue.empty():
            engine._cmd_queue.get_nowait()
            engine._cmd_queue.task_done()

        total = _MAX_BATCH_SIZE - 1
        for i in range(total):
            engine._cmd_queue.put((RenderCommand.CONTENT, f"cmd{i}"))

        assert engine._cmd_queue.qsize() == total

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 全部处理完毕
        assert engine._renderer.render.call_count == total
        # 队列为空
        assert engine._cmd_queue.empty()

    def test_drain_batch_size_exact(self, engine):
        """入队正好 _MAX_BATCH_SIZE 条命令 → drain_queue 全部处理。"""
        engine._bb.is_status_active = False

        # 清空队列
        while not engine._cmd_queue.empty():
            engine._cmd_queue.get_nowait()
            engine._cmd_queue.task_done()

        total = _MAX_BATCH_SIZE
        for i in range(total):
            engine._cmd_queue.put((RenderCommand.CONTENT, f"cmd{i}"))

        assert engine._cmd_queue.qsize() == total

        with (
            patch("src.tui.widgets.lock._try_acquire_output_lock") as m_lock,
        ):
            m_lock.return_value.__enter__.return_value = True

            engine._drain_queue()

        # 全部处理完毕（刚好等于钳位值）
        assert engine._renderer.render.call_count == total
        assert engine._cmd_queue.empty()


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
        """空轮询后清除 cmd_event（有内容时不 clear 避免信号丢失）。"""
        engine._render_running = True
        engine._cmd_event.wait = MagicMock(side_effect=[None, KeyboardInterrupt])
        engine._drain_queue = MagicMock(return_value=False)
        engine._cmd_event.clear = MagicMock()

        with pytest.raises(KeyboardInterrupt):
            engine._render()

        # clear 仅在 drain_queue 无内容时调用（避免竞态丢失 push_cmd 的信号）
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
        assert engine._cmd_event.is_set() is True
        assert "render 线程异常崩溃" in caplog.text
        # 终端也输出告警（含异常类型和消息）
        mock_stderr.write.assert_called_once()
        stderr_text = mock_stderr.write.call_args[0][0]
        assert "render 线程异常终止" in stderr_text
        assert "RuntimeError: 模拟崩溃" in stderr_text

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
        with patch("src.tui.terminal.blessed.get_terminal", return_value=mock_term):
            with patch.object(sys, "__stdout__") as mock_stdout:
                engine._position_cursor()

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
            engine._position_cursor()

        engine._bb.compute_cursor_position.assert_called_once_with(
            "hello", 5, 30, 80,
        )

    def test_position_cursor_min_input_rows(self, engine):
        """compute_cursor_position 内部处理总行数最小值。"""
        engine._bb.get_cursor_info.return_value = ("hi", 2, 25, 60)
        engine._bb.compute_cursor_position.return_value = (23, 5)

        with patch.object(sys, "__stdout__") as mock_stdout:
            engine._position_cursor()

        mock_stdout.write.assert_called_once()

    def test_position_cursor_with_popup(self, engine):
        """补全弹窗高度影响光标行偏移。"""
        engine._bb.get_cursor_info.return_value = ("some text", 3, 40, 100)
        engine._bb.compute_cursor_position.return_value = (40, 13)

        mock_term = MagicMock()
        mock_term.move_xy.return_value = "\033[40;13H"
        with patch("src.tui.terminal.blessed.get_terminal", return_value=mock_term):
            with patch.object(sys, "__stdout__") as mock_stdout:
                engine._position_cursor()

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

    def test_ensure_cursor_upper_exception_tolerated(self, engine):
        """ensure_cursor_upper 容错 _bb.ensure_cursor_in_upper 异常。"""
        engine._bb.ensure_cursor_in_upper.side_effect = OSError("终端 I/O 错误")
        # 不应抛出异常
        engine.ensure_cursor_upper()

    def test_phase_render_ensure_cursor_upper_exception_tolerated(self, engine):
        """_phase_render 中 ensure_cursor_upper 异常不影响后续渲染。"""
        engine._bb.sync_bottom_lines = MagicMock()
        engine._bb.ensure_cursor_in_upper.side_effect = RuntimeError("光标定位失败")
        cmd = (RenderCommand.CONTENT, "hello")
        # 不应抛出异常，后续命令应正常渲染
        engine._phase_render([cmd])
        engine._renderer.render.assert_called_once_with(cmd)


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

    def test_push_cmd_event_not_set_on_queue_full(self, engine):
        """队列满时入队失败，cmd_event 保持 clear（不主动 set）。"""
        # 满队列
        tiny_queue = queue.Queue(maxsize=1)
        tiny_queue.put((RenderCommand.CONTENT, "占位"), block=False)
        engine._cmd_queue = tiny_queue
        engine._cmd_event.clear()

        # 满队列时不会 set event
        engine.push_cmd((RenderCommand.CONTENT, "丢弃"))
        # 满队列路径不走 set，所以 event 仍然 clear
        assert not engine._cmd_event.is_set()

    def test_render_uses_exponential_backoff(self, engine):
        """_render 中自适应轮询间隔：空闲时指数退避平滑过渡。"""
        from src.tui.engine.const import _RENDER_INTERVAL
        from src.tui.engine.engine import _ACTIVE_RENDER_INTERVAL

        engine._render_running = True

        # 模拟 7 次 drain → 全部返回 False（空闲）
        call_count = 0
        total_calls = 7

        def _side_effect_drain():
            nonlocal call_count
            call_count += 1
            if call_count >= total_calls:
                engine._render_running = False
            return False  # 空队列，触发退避

        engine._drain_queue = MagicMock(side_effect=_side_effect_drain)
        engine._cmd_event.wait = MagicMock()
        engine._cmd_event.clear = MagicMock()

        engine._render()

        all_calls = engine._cmd_event.wait.call_args_list
        # 指数退避期望值序列（idle_count=0 开始）：
        # idle_count=0: min(0.005 * 2^0, 0.1) = 0.005 → idle_count=1
        # idle_count=1: min(0.005 * 2^1, 0.1) = 0.01  → idle_count=2
        # idle_count=2: min(0.005 * 2^2, 0.1) = 0.02  → idle_count=3
        # idle_count=3: min(0.005 * 2^3, 0.1) = 0.04  → idle_count=4
        # idle_count=4: min(0.005 * 2^4, 0.1) = 0.08  → idle_count=5
        # idle_count=5: min(0.005 * 2^5, 0.1) = 0.1   → idle_count=6
        # idle_count=6: min(0.005 * 2^6, 0.1) = 0.1   → idle_count=7
        expected_timeouts = [
            _ACTIVE_RENDER_INTERVAL,         # 0.005s
            _ACTIVE_RENDER_INTERVAL * 2,      # 0.01s
            _ACTIVE_RENDER_INTERVAL * 4,      # 0.02s
            _ACTIVE_RENDER_INTERVAL * 8,      # 0.04s
            _ACTIVE_RENDER_INTERVAL * 16,     # 0.08s
            _RENDER_INTERVAL,                 # 0.1s
            _RENDER_INTERVAL,                 # 0.1s
        ]
        for idx, expected in enumerate(expected_timeouts):
            assert all_calls[idx] == call(timeout=expected), (
                f"第 {idx+1} 次空闲应使用 timeout={expected}，"
                f"实际={all_calls[idx]}"
            )

    def test_render_resets_backoff_on_content(self, engine):
        """有内容时 idle_count 重置为 0，退避重新从 5ms 开始。"""
        from src.tui.engine.engine import _ACTIVE_RENDER_INTERVAL

        engine._render_running = True

        # drain 返回序列：False×2（退避到10ms）→ True（重置）→ False（重新从5ms开始）
        call_count = 0
        total_calls = 4
        results = [False, False, True, False]

        def _side_effect_drain():
            nonlocal call_count
            idx = call_count
            call_count += 1
            if call_count >= total_calls:
                engine._render_running = False
            return results[idx]

        engine._drain_queue = MagicMock(side_effect=_side_effect_drain)
        engine._cmd_event.wait = MagicMock()
        engine._cmd_event.clear = MagicMock()

        engine._render()

        all_calls = engine._cmd_event.wait.call_args_list
        # 第1次空闲: idle_count=0 → 5ms (退避)
        assert all_calls[0] == call(timeout=_ACTIVE_RENDER_INTERVAL)
        # 第2次空闲: idle_count=1 → 10ms (退避)
        assert all_calls[1] == call(timeout=_ACTIVE_RENDER_INTERVAL * 2)
        # 有内容: idle_count=0 → 5ms (活跃)
        assert all_calls[2] == call(timeout=_ACTIVE_RENDER_INTERVAL)
        # 有内容后再次空闲: idle_count=0 → 5ms (退避重新开始)
        assert all_calls[3] == call(timeout=_ACTIVE_RENDER_INTERVAL)



    def test_drain_lock_name_is_drain_queue(self, engine):
        """_try_acquire_output_lock 的 name 参数为 'drain_queue'。"""
        engine._bb.is_status_active = True

        with patch(
            "src.tui.widgets.lock._try_acquire_output_lock",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=True),
                __exit__=MagicMock(),
            ),
        ) as m_lock:
            engine._drain_queue()

            m_lock.assert_called_once_with(name="drain_queue", timeout=0.1)

    def test_idle_count_does_not_exceed_10(self, engine):
        """长时间空闲时 idle_count 被钳位到 10，不会无限增长。"""
        engine._render_running = True

        # 模拟 20 次空闲 drain（远超正常退避范围）
        call_count = 0
        total_calls = 20

        def _side_effect_drain():
            nonlocal call_count
            call_count += 1
            if call_count >= total_calls:
                engine._render_running = False
            return False  # 始终空闲

        engine._drain_queue = MagicMock(side_effect=_side_effect_drain)
        engine._cmd_event.wait = MagicMock()
        engine._cmd_event.clear = MagicMock()

        engine._render()

        all_calls = engine._cmd_event.wait.call_args_list
        # idle_count ≥5 后恒为 _RENDER_INTERVAL(0.1s)
        from src.tui.engine.const import _RENDER_INTERVAL
        for idx, call_args in enumerate(all_calls):
            if idx >= 5:
                assert call_args == call(timeout=_RENDER_INTERVAL), (
                    f"第 {idx+1} 次空闲应 timeout={_RENDER_INTERVAL}，"
                    f"实际={call_args}"
                )

    def test_idle_count_no_overflow_from_large_exponent(self, engine):
        """idle_count 上限 10 确保 2**idle_count 始终在安全范围内。"""
        # 模拟长时间运行：多次空闲迭代
        engine._render_running = True

        call_count = 0
        total_calls = 30

        def _side_effect_drain():
            nonlocal call_count
            call_count += 1
            if call_count >= total_calls:
                engine._render_running = False
            return False

        engine._drain_queue = MagicMock(side_effect=_side_effect_drain)
        engine._cmd_event.wait = MagicMock()
        engine._cmd_event.clear = MagicMock()

        # 不应抛出任何异常（包括 OverflowError）
        engine._render()

        # idle_count≥10 后 2**idle_count = 2**10 = 1024（封顶），
        # 0.005 * 1024 = 5.12，min(5.12, 0.1) = 0.1，完全安全
        all_timeouts = [
            c[1]["timeout"] for c in engine._cmd_event.wait.call_args_list
        ]
        assert all(t >= 0 for t in all_timeouts), "timeout 不能为负数"
        assert all(t <= 0.1 for t in all_timeouts), "timeout 不能超过 0.1"


# ══════════════════════════════════════════════════════
# TestLockGranularity — 步骤 6：锁细粒度化测试
# ══════════════════════════════════════════════════════

class TestLockGranularity:
    """render_lock 与 io_lock 独立互不阻塞验证。

    验证锁拆分后，渲染管线锁与终端 I/O 锁互不竞争。

    注意：_mock_threading_thread (autouse=True) 会 patch threading.Thread，
    因此使用 _thread.start_new_thread 启动真实线程来持有锁。
    """

    def test_render_lock_independent(self):
        """io_lock 被持有时 render_lock 仍可正常获取。

        验证两锁为独立实例：
        - io_lock 被另一个线程持有
        - render_lock 可立即获取（无等待）
        """
        import _thread as _real_thread
        from src.tui.widgets.lock import render_lock, io_lock

        acquired_io = threading.Event()
        done = threading.Event()

        def hold_io():
            io_lock.acquire()
            acquired_io.set()
            done.wait()  # 保持持有直到测试结束
            io_lock.release()

        # 使用 _thread.start_new_thread 绕过 autouse fixture 对 threading.Thread 的 patch
        _real_thread.start_new_thread(hold_io, ())
        acquired_io.wait(timeout=2)  # 确保 io_lock 已被持有
        assert acquired_io.is_set(), "后台线程未能获取 io_lock"

        # render_lock 应可立即获取（io_lock 独立）
        got = render_lock.acquire(timeout=0.5)
        assert got, "io_lock 被持有时 render_lock 应可获取"
        render_lock.release()

        done.set()
        # 等后台线程释放 io_lock
        import time
        time.sleep(0.1)

    def test_io_lock_independent(self):
        """render_lock 被持有时 io_lock 仍可正常获取。

        验证两锁为独立实例：
        - render_lock 被另一个线程持有
        - io_lock 可立即获取（无等待）
        """
        import _thread as _real_thread
        from src.tui.widgets.lock import render_lock, io_lock

        acquired_render = threading.Event()
        done = threading.Event()

        def hold_render():
            render_lock.acquire()
            acquired_render.set()
            done.wait()  # 保持持有直到测试结束
            render_lock.release()

        # 使用 _thread.start_new_thread 绕过 autouse fixture 对 threading.Thread 的 patch
        _real_thread.start_new_thread(hold_render, ())
        acquired_render.wait(timeout=2)  # 确保 render_lock 已被持有
        assert acquired_render.is_set(), "后台线程未能获取 render_lock"

        # io_lock 应可立即获取（render_lock 独立）
        got = io_lock.acquire(timeout=0.5)
        assert got, "render_lock 被持有时 io_lock 应可获取"
        io_lock.release()

        done.set()
        # 等后台线程释放 render_lock
        import time
        time.sleep(0.1)

