"""测试 TuiEngine — 渲染引擎三阶段流水线 + 崩溃恢复。"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock, PropertyMock, patch

from src.tui.engine.engine import TuiEngine
from src.tui.engine.const import RenderCommand
from src.tui.engine.renderer_base import FrameworkRenderer
from src.tui.testing import tui_test_env


def _make_mock_renderer():
    """创建 FrameworkRenderer 的 MagicMock。

    返回模拟对象，render 方法记录调用但不执行实际操作。
    """
    renderer = MagicMock(spec=FrameworkRenderer)
    renderer.render = MagicMock()
    renderer.output_adapter = MagicMock()
    return renderer


def _make_mock_bottom_bar():
    """创建 BottomBarProtocol 的 MagicMock。"""
    bb = MagicMock()
    bb.sync_bottom_lines = MagicMock()
    bb.force_redraw = MagicMock()
    bb.is_active = False
    bb.ensure_cursor_in_upper = MagicMock()
    return bb


class TestTuiEngineInit:
    """TuiEngine 初始化测试。"""

    def test_init_creates_cmd_queue(self):
        """__init__ 创建命令队列。"""
        with tui_test_env():
            renderer = _make_mock_renderer()
            bb = _make_mock_bottom_bar()
            engine = TuiEngine(renderer, bb)
            assert engine._cmd_queue is not None
            assert engine._cmd_queue.maxsize > 0

    def test_init_default_state(self):
        """初始化时渲染线程未启动。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            assert engine._render_thread is None
            assert engine._render_running is False
            assert engine.render_crashed is False
            assert engine.is_recovering is False

    def test_init_with_cursor_tracker(self):
        """可传入 cursor_tracker 参数。"""
        with tui_test_env():
            tracker = MagicMock()
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar(), tracker)
            assert engine._cursor_tracker is tracker


class TestTuiEnginePushCmd:
    """push_cmd 入队测试。"""

    def test_push_cmd_enqueues(self):
        """push_cmd 将命令加入队列。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.push_cmd((RenderCommand.NOTIFICATION, "hello"))
            assert engine._cmd_queue.qsize() == 1

    def test_push_cmd_sets_event(self):
        """push_cmd 设置 cmd_event 信号。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine._cmd_event.clear()
            engine.push_cmd((RenderCommand.NOTIFICATION, "hello"))
            assert engine._cmd_event.is_set()

    def test_push_cmd_full_queue(self):
        """队列满时 push_cmd 不阻塞且记录丢弃计数。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            # 强制设置队列 maxsize 很小来模拟满队列
            engine._cmd_queue = queue.Queue(maxsize=1)
            engine._cmd_queue.put((RenderCommand.NOTIFICATION, "full"), block=False)
            # 第二次 push 应触发队列满
            engine.push_cmd((RenderCommand.NOTIFICATION, "drop_me"))
            assert engine._cmd_queue_dropped == 1
            assert engine._consecutive_full == 1

    def test_push_cmd_resets_consecutive_full(self):
        """连续满后成功入队会重置连续计数。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine._cmd_queue = queue.Queue(maxsize=1)
            engine._cmd_queue.put((RenderCommand.NOTIFICATION, "full"), block=False)
            engine.push_cmd((RenderCommand.NOTIFICATION, "drop1"))
            # 排空队列
            engine._cmd_queue.get_nowait()
            engine._cmd_queue.task_done()
            # 这次应成功
            engine.push_cmd((RenderCommand.NOTIFICATION, "ok"))
            assert engine._consecutive_full == 0


class TestTuiEngineStartStop:
    """TuiEngine start/stop 测试。"""

    def test_start_creates_render_thread(self):
        """start() 创建并启动 render 线程。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            assert engine._render_thread is not None
            assert engine._render_thread.is_alive()
            engine.stop()

    def test_start_idempotent(self):
        """重复调用 start() 安全（幂等）。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            thread_id = id(engine._render_thread)
            # 第二次 start 不应创建新线程（已有线程在运行）
            engine.start()
            assert id(engine._render_thread) == thread_id
            engine.stop()

    def test_stop_joins_render_thread(self):
        """stop() 等待 render 线程结束。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            engine.stop()
            assert engine._render_running is False
            assert engine._render_thread is None or not engine._render_thread.is_alive()

    def test_stop_drains_queue(self):
        """stop() 排空命令队列。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            engine.push_cmd((RenderCommand.NOTIFICATION, "test"))
            engine.stop()
            # 队列应在 stop 后排空
            assert engine._cmd_queue.qsize() == 0 or engine._cmd_queue.empty()

    def test_stop_repeated(self):
        """重复 stop() 安全（幂等）。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            engine.stop()
            engine.stop()  # 再次 stop，不应抛异常


class TestTuiEngineDrainQueue:
    """_drain_queue 三阶段流水线测试。"""

    def test_drain_queue_empty(self):
        """空队列时 drain 返回 False。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine._cmd_event.set()
            has_content = engine._drain_queue()
            assert has_content is False

    def test_drain_queue_with_content(self):
        """有命令时 drain 返回 True 并调用 renderer.render。"""
        with tui_test_env():
            renderer = _make_mock_renderer()
            engine = TuiEngine(renderer, _make_mock_bottom_bar())
            engine.push_cmd((RenderCommand.NOTIFICATION, "test"))
            has_content = engine._drain_queue()
            assert has_content is True
            renderer.render.assert_called()

    def test_drain_queue_batch_clamping(self):
        """批量钳位：max_batch_size 限制单帧最多处理的命令数。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            # 入队超过 max_batch_size 的命令
            for _ in range(engine._config.max_batch_size + 10):
                engine.push_cmd((RenderCommand.NOTIFICATION, "test"))
            has_content = engine._drain_queue()
            assert has_content is True
            # 队列中应有剩余命令
            remaining = engine._cmd_queue.qsize()
            assert remaining > 0

    def test_drain_queue_phases_order(self):
        """三阶段按顺序执行：pre_update → render → redraw_bottom。"""
        with tui_test_env():
            order = []
            renderer = _make_mock_renderer()
            bb = _make_mock_bottom_bar()

            engine = TuiEngine(renderer, bb)
            # 注入调用顺序跟踪
            original_pre = engine._phase_pre_update_panels
            def tracking_pre():
                order.append("pre_update")
                original_pre()
            engine._phase_pre_update_panels = tracking_pre

            original_render = engine._phase_render
            def tracking_render(cmds):
                order.append("render")
                original_render(cmds)
            engine._phase_render = tracking_render

            original_redraw = engine._phase_redraw_bottom
            def tracking_redraw():
                order.append("redraw_bottom")
                original_redraw()
            engine._phase_redraw_bottom = tracking_redraw

            engine.push_cmd((RenderCommand.NOTIFICATION, "test"))
            engine._drain_queue()
            assert order == ["pre_update", "render", "redraw_bottom"]


class TestTuiEnginePhaseRender:
    """_phase_render 渲染阶段测试。"""

    def test_content_commands_trigger_cursor_upper(self):
        """内容命令触发 ensure_cursor_upper。"""
        with tui_test_env():
            bb = _make_mock_bottom_bar()
            engine = TuiEngine(_make_mock_renderer(), bb)
            engine.ensure_cursor_upper = MagicMock()
            commands = [(RenderCommand.CONTENT, "hello")]
            engine._phase_render(commands)
            engine.ensure_cursor_upper.assert_called_once()

    def test_non_content_commands_skip_cursor_upper(self):
        """非内容命令跳过 ensure_cursor_upper。"""
        with tui_test_env():
            bb = _make_mock_bottom_bar()
            engine = TuiEngine(_make_mock_renderer(), bb)
            engine.ensure_cursor_upper = MagicMock()
            commands = [(RenderCommand.TOOL_COUNT_INC,)]
            engine._phase_render(commands)
            engine.ensure_cursor_upper.assert_not_called()

    def test_mixed_commands_trigger_cursor_upper(self):
        """混合命令批次（含内容命令）触发光标定位。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.ensure_cursor_upper = MagicMock()
            commands = [
                (RenderCommand.TOOL_COUNT_INC,),
                (RenderCommand.CONTENT, "hello"),
            ]
            engine._phase_render(commands)
            engine.ensure_cursor_upper.assert_called_once()

    def test_render_error_does_not_crash(self):
        """单条命令渲染失败不中断循环。"""
        with tui_test_env():
            import logging
            logging.disable(logging.CRITICAL)  # 抑制引擎日志中的格式串 bug
            renderer = _make_mock_renderer()
            renderer.render = MagicMock(side_effect=[None, ValueError("render fail"), None])
            engine = TuiEngine(renderer, _make_mock_bottom_bar())
            commands = [
                (RenderCommand.NOTIFICATION, "a"),
                (RenderCommand.ERROR, "b"),
                (RenderCommand.NOTIFICATION, "c"),
            ]
            engine._phase_render(commands)  # 不应抛异常
            logging.disable(logging.NOTSET)


class TestTuiEnginePhaseRedrawBottom:
    """_phase_redraw_bottom 底部栏重绘测试。"""

    def test_force_redraw_called(self):
        """10Hz 定时触发 force_redraw。"""
        with tui_test_env():
            bb = _make_mock_bottom_bar()
            engine = TuiEngine(_make_mock_renderer(), bb)
            engine._last_bottom_redraw = 0.0  # 强制触发
            engine._phase_redraw_bottom()
            bb.force_redraw.assert_called_once()

    def test_force_redraw_skipped_within_interval(self):
        """10Hz 间隔内不重复重绘。"""
        with tui_test_env():
            bb = _make_mock_bottom_bar()
            engine = TuiEngine(_make_mock_renderer(), bb)
            engine._last_bottom_redraw = time.monotonic()  # 刚刚重绘过
            engine._phase_redraw_bottom()
            bb.force_redraw.assert_not_called()

    def test_request_bottom_redraw_clears_interval(self):
        """request_bottom_redraw 强制跳过间隔检查。"""
        with tui_test_env():
            bb = _make_mock_bottom_bar()
            engine = TuiEngine(_make_mock_renderer(), bb)
            engine._last_bottom_redraw = time.monotonic()  # 刚刚重绘过
            engine.request_bottom_redraw()
            engine._phase_redraw_bottom()
            bb.force_redraw.assert_called_once()


class TestTuiEngineFlush:
    """flush 测试。"""

    def test_flush_without_thread(self):
        """无 render 线程时 flush 直接排空队列。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.push_cmd((RenderCommand.NOTIFICATION, "a"))
            engine.push_cmd((RenderCommand.NOTIFICATION, "b"))
            engine.flush(timeout=0.1)
            assert engine._cmd_queue.qsize() == 0

    def test_flush_with_thread(self):
        """有 render 线程时 flush 等待队列排空。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            engine.push_cmd((RenderCommand.NOTIFICATION, "a"))
            engine.flush(timeout=1.0)
            engine.stop()


class TestTuiEngineProperties:
    """属性测试。"""

    def test_render_crashed_initially_false(self):
        """render_crashed 初始为 False。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            assert engine.render_crashed is False

    def test_is_recovering_initially_false(self):
        """is_recovering 初始为 False。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            assert engine.is_recovering is False

    def test_set_panel_refresh_callback(self):
        """set_panel_refresh_callback 注册回调。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            cb = MagicMock()
            engine.set_panel_refresh_callback(cb)
            assert engine._panel_refresh_cb is cb

    def test_panel_refresh_callback_none(self):
        """set_panel_refresh_callback(None) 清除回调。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            cb = MagicMock()
            engine.set_panel_refresh_callback(cb)
            engine.set_panel_refresh_callback(None)
            assert engine._panel_refresh_cb is None


class TestTuiEngineHasContentCommand:
    """_has_content_command 静态方法测试。"""

    def test_content_command_detected(self):
        """内容命令被正确检测。"""
        commands = [(RenderCommand.CONTENT, "hello")]
        assert TuiEngine._has_content_command(commands) is True

    def test_non_content_command(self):
        """非内容命令返回 False。"""
        commands = [(RenderCommand.TOOL_COUNT_INC,)]
        assert TuiEngine._has_content_command(commands) is False

    def test_empty_commands(self):
        """空命令列表返回 False。"""
        assert TuiEngine._has_content_command([]) is False

    def test_none_command(self):
        """含 None 的命令安全处理。"""
        commands = [(RenderCommand.TOOL_COUNT_INC,), None]
        assert TuiEngine._has_content_command(commands) is False

    def test_all_content_types(self):
        """所有 CONTENT_COMMANDS 类型都被检测。"""
        for cmd_id in TuiEngine._CONTENT_COMMANDS:
            assert TuiEngine._has_content_command([(cmd_id, "test")]) is True


class TestTuiEngineEdgeCases:
    """边界条件测试。"""

    def test_push_cmd_after_stop(self):
        """stop 后 push_cmd 仍能入队。"""
        with tui_test_env():
            engine = TuiEngine(_make_mock_renderer(), _make_mock_bottom_bar())
            engine.start()
            engine.stop()
            engine.push_cmd((RenderCommand.NOTIFICATION, "after_stop"))
            assert engine._cmd_queue.qsize() == 1

    def test_ensure_cursor_upper_safe(self):
        """ensure_cursor_upper 在 bb 异常时安全。"""
        with tui_test_env():
            bb = _make_mock_bottom_bar()
            bb.ensure_cursor_in_upper = MagicMock(side_effect=RuntimeError("bb error"))
            engine = TuiEngine(_make_mock_renderer(), bb)
            engine.ensure_cursor_upper()  # 不应抛异常
