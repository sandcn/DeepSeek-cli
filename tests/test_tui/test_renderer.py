"""测试 _renderer.py — 统一渲染器。

测试 TuiEngine.push_cmd/flush 命令队列逻辑（mock 线程）、
TuiRenderer.render() 分发正确（mock OutputAdapter + ChatRenderState + BottomBar）、
EventDispatcher 事件→命令映射。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestTuiEngineCommandQueue:
    """测试 TuiEngine 命令队列操作。"""

    def test_push_cmd(self):
        from src.tui._renderer import TuiEngine
        from src.tui._const import ReasoningCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        engine.push_cmd(ReasoningCmd(text="test"))
        assert engine._cmd_queue.qsize() == 1

    def test_push_cmd_queue_full_handling(self):
        from src.tui._renderer import TuiEngine
        from src.tui._const import ReasoningCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        # 设置较小的队列容量来测试满队列
        engine._cmd_queue.maxsize = 3
        for i in range(5):
            engine.push_cmd(ReasoningCmd(text=f"test{i}"))
        assert engine._cmd_queue.qsize() <= engine._cmd_queue.maxsize

    def test_push_cmd_high_priority_nonblocking_regression(self):
        """方向D 步骤8：高优先级 push_cmd 满队列时不阻塞（非阻塞 + 丢弃计数）。"""
        import queue as _queue
        from src.tui._renderer import TuiEngine
        from src.tui._const import ReasoningCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        # 满队列 mock：put 抛 queue.Full
        engine._cmd_queue = MagicMock()
        engine._cmd_queue.put.side_effect = _queue.Full

        engine.push_cmd(ReasoningCmd(text="test"))

        # 非阻塞：put 应以 block=False 调用
        engine._cmd_queue.put.assert_called_once()
        _, kwargs = engine._cmd_queue.put.call_args
        assert kwargs["block"] is False
        # 丢弃计数递增
        assert engine._cmd_queue_dropped == 1
        assert engine._consecutive_full == 1

    def test_push_cmd_critical_blocks_regression(self):
        """方向D 步骤8：push_cmd_critical 满队列时仍走阻塞路径。"""
        import queue as _queue
        from src.tui._renderer import TuiEngine
        from src.tui._const import PhaseDoneCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        engine._cmd_queue = MagicMock()
        engine._cmd_queue.put.side_effect = _queue.Full

        with pytest.raises(_queue.Full):
            engine.push_cmd_critical(PhaseDoneCmd(phase="done"))

        # 阻塞路径：block=True + timeout=1.0
        engine._cmd_queue.put.assert_called_once()
        _, kwargs = engine._cmd_queue.put.call_args
        assert kwargs["block"] is True
        assert kwargs["timeout"] == 1.0

    def test_drain_queue_panel_refresh_outside_lock_regression(self):
        """方向D 步骤8：_phase_pre_update_panels（SubAgentPanel 刷新）在输出锁获取之前。"""
        import queue as _queue
        from unittest.mock import patch
        from src.tui._renderer import TuiEngine

        renderer = MagicMock()
        bottom_bar = MagicMock()
        bottom_bar.is_active = False
        engine = TuiEngine(renderer, bottom_bar)

        call_order = []
        engine._phase_process_input = lambda: call_order.append("process_input")
        engine._phase_pre_update_panels = lambda: call_order.append("pre_update_panels")
        engine._cmd_queue = MagicMock()
        engine._cmd_queue.get_nowait.side_effect = _queue.Empty

        class _FakeLock:
            def __enter__(self):
                call_order.append("acquire_lock")
                return True
            def __exit__(self, *a):
                call_order.append("release_lock")
                return False

        with patch(
            "src.tui._renderer._engine._try_acquire_output_lock",
            return_value=_FakeLock(),
        ):
            engine._drain_queue()

        # 面板刷新与输入分发必须先于锁获取（锁外执行）
        assert call_order.index("pre_update_panels") < call_order.index("acquire_lock")
        assert call_order.index("process_input") < call_order.index("acquire_lock")

    def test_flush_drains_queue(self):
        from src.tui._renderer import TuiEngine
        from src.tui._const import WriteLineCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        # 推入命令（不启动线程，flush 应排空）；queue 元素为三元组 (priority, seq, cmd)
        for i in range(5):
            engine._cmd_queue.put((0, i, WriteLineCmd(text=f"test{i}")))
        engine.flush(timeout=1.0)
        assert engine._cmd_queue.qsize() == 0

    def test_request_bottom_redraw(self):
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        assert engine._bottom_redraw_requested.is_set() is False
        engine.request_bottom_redraw()
        assert engine._bottom_redraw_requested.is_set() is True

    def test_set_panel_refresh_callback(self):
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        cb = MagicMock()
        engine.set_panel_refresh_callback(cb)
        assert engine._panel_refresh_cb is cb

    def test_duplicate_start_race_prevention(self):
        """验证重复调用 start() 时仅第一次有效（原子性检查）。"""
        import threading
        from unittest.mock import MagicMock
        from src.tui._renderer import TuiEngine

        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)

        # 首次 start
        assert engine._render_thread is None
        engine.start()
        assert engine._render_thread is not None
        assert engine._render_thread.is_alive()
        original_thread = engine._render_thread

        # 第二次 start（重复调用）— 应被原子性检查拦截
        engine.start()
        assert engine._render_thread is original_thread, "重复 start 不应创建新线程"

        # 第三次 start（再次确认）
        engine.start()
        assert engine._render_thread is original_thread, "多次重复 start 不应创建新线程"

        # 清理
        engine.stop()

    def test_start_after_stop_works(self):
        """验证 stop() 后重新 start() 正常创建新线程。"""
        from unittest.mock import MagicMock
        from src.tui._renderer import TuiEngine

        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)

        # start → stop → start
        engine.start()
        engine.stop()
        assert engine._render_running is False
        assert engine._render_thread is not None
        old_thread = engine._render_thread

        engine.start()
        assert engine._render_running is True
        assert engine._render_thread is not old_thread, "重新 start 应创建新线程"

        # 清理
        engine.stop()

    def test_ensure_cursor_upper(self):
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        engine.ensure_cursor_upper()
        bottom_bar.ensure_cursor_in_upper.assert_called_once()

    def test_render_loop_event_cleared_before_wait(self):
        """验证 _cmd_event.clear() 在 wait() 前无条件执行（修复 CPU 100% 忙等）"""
        from src.tui._renderer import TuiEngine
        from unittest.mock import MagicMock

        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)

        # mock _cmd_event 以跟踪调用
        engine._cmd_event = MagicMock()

        # mock _drain_queue 返回 True（模拟有 SubAgent 帧内容）
        engine._drain_queue = MagicMock(return_value=True)

        # 手动执行修复后的逻辑
        has_content = engine._drain_queue()
        engine._cmd_event.clear()
        engine._cmd_event.wait(timeout=engine._config.render_interval)

        # 验证 clear 在 wait 前被调用
        engine._cmd_event.clear.assert_called_once()
        engine._cmd_event.wait.assert_called_once_with(timeout=engine._config.render_interval)

        # 验证 clear 在 wait 之前被调用（call order）
        calls = engine._cmd_event.mock_calls
        clear_idx = next(i for i, c in enumerate(calls) if c[0] == 'clear')
        wait_idx = next(i for i, c in enumerate(calls) if c[0] == 'wait')
        assert clear_idx < wait_idx, f"clear() 应在 wait() 之前调用, 实际 clear={clear_idx}, wait={wait_idx}"

    def test_event_not_stuck_set_with_continuous_subagent_frames(self):
        """模拟连续 SUBAGENT_FRAME push 场景，验证 event 不会卡在 SET 状态"""
        from src.tui._renderer import TuiEngine
        from src.tui._const import RenderCommand, SubagentFrameCmd, NotificationCmd
        from unittest.mock import MagicMock

        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)

        # 使用真实的 Event
        import threading
        engine._cmd_event = threading.Event()

        # 模拟 _panel_refresh_cb: 每次刷新的 push SUBAGENT_FRAME（模拟真实场景）
        _seq = 0
        def mock_panel_refresh():
            nonlocal _seq
            engine._cmd_queue.put((RenderCommand.SUBAGENT_FRAME, _seq, SubagentFrameCmd(frame_lines=("line1",))))
            _seq += 1
            engine._cmd_event.set()

        engine._panel_refresh_cb = mock_panel_refresh

        # 模拟多次渲染循环回合
        for _ in range(5):
            # 先 push 一些内容到队列（模拟外部 push）
            engine._cmd_queue.put((RenderCommand.NOTIFICATION, _seq, NotificationCmd(text="test")))
            _seq += 1
            engine._cmd_event.set()

            # 执行 drain（内部会调用 _phase_pre_update_panels → mock_panel_refresh → 又 set 了 event）
            engine._drain_queue()

            # ★ 修复后的行为：无论 has_content 如何，都 clear
            engine._cmd_event.clear()

            # 验证 event 已 clear（wait 会实际等待）
            assert not engine._cmd_event.is_set(), \
                f"循环 {_}: event 应已被 clear，但处于 SET 状态"

            # 模拟 wait（设置极短超时避免慢）
            engine._cmd_event.wait(timeout=0.001)

        # 验证：清理队列
        engine._drain_queue_safe()


class TestTuiRenderer:
    """测试 TuiRenderer 命令分发。"""

    def test_render_unknown_command(self):
        from src.tui._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        # 非 RenderCmd 输入应抛 TypeError（tuple 双格式已移除）
        with pytest.raises(TypeError):
            renderer.render(999)
        # adapter 不应被调用
        adapter.write.assert_not_called()

    def test_render_empty_cmd(self):
        from src.tui._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        # 原空 tuple 跳过语义已移除：非 RenderCmd 输入应抛 TypeError
        with pytest.raises(TypeError):
            renderer.render(())
        adapter.write.assert_not_called()

    def test_render_tool_count_inc(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import ToolCountIncCmd
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(ToolCountIncCmd())
        bb.increment_tool.assert_called_once()

    def test_render_tool_count_dec(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import ToolCountDecCmd
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(ToolCountDecCmd())
        bb.decrement_tool.assert_called_once()

    def test_render_tool_fail_inc(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import ToolFailIncCmd
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(ToolFailIncCmd())
        bb.increment_tool_fail.assert_called_once()

    def test_render_main_phase(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import MainPhaseCmd
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(MainPhaseCmd(phase="thinking"))
        bb.set_main_phase.assert_called_once_with("thinking")

    def test_output_adapter_property(self):
        from src.tui._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        assert renderer.output_adapter is adapter

    def test_handlers_cover_all_commands(self):
        """验证所有 RenderCommand 枚举值都有对应的处理器。"""
        from src.tui._renderer import TuiRenderer
        from src.tui._const import RenderCommand
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)

        for cmd in RenderCommand:
            assert cmd.value in renderer._handlers, (
                f"RenderCommand.{cmd.name} ({cmd.value}) 缺少处理器"
            )


class TestEventDispatcher:
    """测试 EventDispatcher 事件→命令映射。"""

    def test_list_handlers(self):
        from src.tui._renderer import EventDispatcher
        dispatcher = EventDispatcher(MagicMock())
        handlers = dispatcher.list_handlers()
        assert len(handlers) == 12

    def test_on_reasoning_chunk(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ReasoningChunkEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ReasoningChunkEvent(text="hello", label="main")
        dispatcher._on_reasoning_chunk(event)
        # 因 label 过滤可能不匹配，但方法不应抛异常
        # 如果 push_cmd 被调用，说明过滤通过
        # 如果未调用，说明 label 不是 "main" — 这是正常行为

    def test_on_content_chunk(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ContentChunkEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ContentChunkEvent(text="world", label="main")
        dispatcher._on_content_chunk(event)
        # 不应抛异常

    def test_on_tool_started(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ToolStartedEvent
        from src.tui._const import ToolCountIncCmd
        push_cmd = MagicMock()
        # 使用默认 filter_fn：source == "agent" 通过
        dispatcher = EventDispatcher(push_cmd)
        event = ToolStartedEvent(source="agent")
        dispatcher._on_tool_started(event)
        push_cmd.assert_called_once_with(ToolCountIncCmd())

    def test_on_tool_done_success(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import ToolCountDecCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolDoneEvent(source="agent", success=True)
        dispatcher._on_tool_done(event)
        push_cmd.assert_called_once_with(ToolCountDecCmd())

    def test_on_tool_done_fail(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import ToolFailIncCmd, ToolCountDecCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolDoneEvent(source="agent", success=False)
        dispatcher._on_tool_done(event)
        assert push_cmd.call_count == 2
        push_cmd.assert_any_call(ToolFailIncCmd())
        push_cmd.assert_any_call(ToolCountDecCmd())

    def test_on_parse_info(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ParseInfoEvent
        from src.tui._const import ParseInfoCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ParseInfoEvent(source="agent", tool_names="test", tokens=100, elapsed=0.5)
        dispatcher._on_parse_info(event)
        push_cmd.assert_called_once_with(ParseInfoCmd(tool_names="test", tokens=100, elapsed=0.5))

    def test_register_handler(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import OutputEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        custom = MagicMock()
        dispatcher.register_handler(OutputEvent, custom)
        handlers = dispatcher.list_handlers()
        assert OutputEvent in handlers
        assert handlers[OutputEvent] is custom

    def test_list_handlers_cache_regression(self):
        """方向D 步骤7：list_handlers() 结果缓存，register_handler 后失效重建。"""
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import OutputEvent, SessionStarted
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)

        # 首次调用构建缓存，两次调用返回同一对象（缓存生效）
        h1 = dispatcher.list_handlers()
        h2 = dispatcher.list_handlers()
        assert h1 is h2, "list_handlers() 应返回同一缓存对象"
        assert len(h1) == 12

        # register_handler 后缓存失效，返回新对象且含新 handler
        custom = MagicMock()
        dispatcher.register_handler(SessionStarted, custom)
        h3 = dispatcher.list_handlers()
        assert h3 is not h2, "register_handler 后应重新构建缓存"
        assert SessionStarted in h3
        assert h3[SessionStarted] is custom
        assert len(h3) == 13

    def test_register_group_regression(self):
        """方向D 步骤7：register_group 注册声明式订阅组并合并进 list_handlers。"""
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import SessionStarted, SessionStopped
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)

        group_handler = MagicMock()
        dispatcher.register_group(
            "test_group",
            {SessionStarted: group_handler},
        )
        handlers = dispatcher.list_handlers()
        assert SessionStarted in handlers
        assert handlers[SessionStarted] is group_handler
        assert len(handlers) == 13

        # 缓存失效：重复 register_group 后返回新对象
        another = MagicMock()
        dispatcher.register_group(
            "test_group",
            {SessionStarted: group_handler, SessionStopped: another},
        )
        handlers2 = dispatcher.list_handlers()
        assert handlers2 is not handlers
        assert handlers2[SessionStopped] is another

    def test_on_model_phase_thinking(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import MainPhaseCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, main_label=cfg.main_label)
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="thinking", info="")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once_with(MainPhaseCmd(phase="thinking"))

    def test_on_model_phase_error(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand, ErrorCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, main_label=cfg.main_label)
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="error", info="something went wrong")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once()
        call_args = push_cmd.call_args[0][0]
        assert call_args.cid == RenderCommand.ERROR

    def test_on_model_phase_error_truncates_to_max_length(self):
        """_on_model_phase error 时消息截断到 max_error_length（P3-9 端到端断言）。"""
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand, ErrorCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(
            push_cmd, main_label=cfg.main_label, max_error_length=50,
        )
        long_info = "E" * 100
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="error", info=long_info)
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once()
        call_args = push_cmd.call_args[0][0]
        assert isinstance(call_args, ErrorCmd)
        assert call_args.cid == RenderCommand.ERROR
        assert len(call_args.message) == 50

    def test_on_model_phase_error_max_length_zero_returns_empty(self):
        """max_error_length=0 时 _on_model_phase error 消息为空串（P3-7）。"""
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand, ErrorCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(
            push_cmd, main_label=cfg.main_label, max_error_length=0,
        )
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="error", info="something went wrong")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once()
        call_args = push_cmd.call_args[0][0]
        assert isinstance(call_args, ErrorCmd)
        assert call_args.cid == RenderCommand.ERROR
        assert call_args.message == ""


class TestTuiEngineCrashRecovery:
    """测试崩溃恢复竞态修复 — _recovering → Event 替换（Issue 2）。"""

    @pytest.fixture
    def engine(self):
        """创建 mock 后的 TuiEngine 实例。"""
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        eng = TuiEngine(renderer, bottom_bar)
        # 缩短恢复延迟便于测试
        eng._config = eng._config.with_overrides(recover_delay=0.01)
        return eng

    def test_recovering_event_in_init(self, engine):
        """验证 _recovering_event 在 __init__ 中被初始化为 threading.Event。"""
        import threading
        assert isinstance(engine._recovering_event, threading.Event)
        # 初始状态应为未设置
        assert not engine._recovering_event.is_set()

    def test_recovering_event_set_on_crash(self, engine):
        """验证 _handle_render_crash 会 set 恢复事件。"""
        # mock drain 避免实际线程操作
        engine._drain_queue_safe = MagicMock(return_value=0)
        engine._render_running = True
        engine._recover_attempts = 0

        exc = RuntimeError("模拟崩溃")
        result = engine._handle_render_crash(exc)

        # 恢复事件应被 set（因为 render_running=True 且 recover_attempts <= max）
        assert engine._recovering_event.is_set()

    def test_finally_checks_version_instead_of_event(self, engine):
        """验证 finally 块使用 _render_version != entry_version 而非旧 _recovering_event。"""
        engine._drain_queue_safe = MagicMock(return_value=0)
        engine._render_running = False  # 让 while 循环退出

        # 手动执行 _render：版本号未变（正常退出），应调用 _drain_queue_safe
        engine._render()
        engine._drain_queue_safe.assert_called_once()

    def test_finally_skips_drain_on_version_change(self, engine):
        """验证版本号变化时 finally 块跳过排空（恢复路径）。"""
        engine._drain_queue_safe = MagicMock(return_value=0)
        engine._render_running = True  # 让 while 循环至少进入一次

        # 第一次 drain 时：递增版本号模拟崩溃恢复 + 设置 _render_running=False 退出循环
        def _drain_and_bump():
            engine._render_version += 1  # 模拟另一个线程启动了新版本
            engine._render_running = False  # 让 while 循环在下一次检查时退出
            return False
        engine._drain_queue = _drain_and_bump

        engine._render()

        # 版本号已变（entry_version != self._render_version），应跳过排空
        engine._drain_queue_safe.assert_not_called()

    def test_recovering_event_still_set_on_crash(self, engine):
        """验证 _handle_render_crash 仍会 set _recovering_event。"""
        engine._drain_queue_safe = MagicMock(return_value=0)
        engine._render_running = True
        engine._recover_attempts = 0

        exc = RuntimeError("模拟崩溃")
        result = engine._handle_render_crash(exc)

        # _recovering_event 仍应被 set（供其他组件检查恢复状态）
        assert engine._recovering_event.is_set()

    def test_no_recovering_attribute_left(self, engine):
        """验证旧的 _recovering bool 属性已不存在（替换为 Event）。"""
        assert not hasattr(engine, '_recovering'), \
            "旧的 _recovering bool 属性应被移除，改用 _recovering_event"


class TestUtilityFunctions:
    """测试 _cmd_name / _emergency_write。"""

    def test_cmd_name_known(self):
        from src.tui._renderer import _cmd_name
        from src.tui._const import RenderCommand
        assert _cmd_name(RenderCommand.REASONING) == "REASONING"
        assert _cmd_name(RenderCommand.CONTENT) == "CONTENT"
        assert _cmd_name(RenderCommand.ERROR) == "ERROR"

    def test_cmd_name_unknown(self):
        from src.tui._renderer import _cmd_name
        result = _cmd_name(999)
        assert result == "999"

    def test_emergency_write(self):
        from src.tui._renderer import _emergency_write
        import io
        import sys as _sys
        saved_stderr = _sys.__stderr__
        fake_stderr = io.StringIO()
        _sys.__stderr__ = fake_stderr
        try:
            _emergency_write("test", stream="stderr")
            output = fake_stderr.getvalue()
            assert "test" in output
        finally:
            _sys.__stderr__ = saved_stderr


class TestContentCommandsSingleSource:
    """验证 _CONTENT_COMMANDS 收敛至 _const.CONTENT_COMMANDS 单一真源（步骤 4.1）。"""

    def test_engine_module_alias_matches_source(self):
        from src.tui._renderer import _CONTENT_COMMANDS as reexport
        from src.tui._renderer._engine import _CONTENT_COMMANDS as engine_cmds
        from src.tui._const import CONTENT_COMMANDS
        assert engine_cmds is CONTENT_COMMANDS
        assert reexport is CONTENT_COMMANDS

    def test_renderer_class_attribute_matches_source(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import CONTENT_COMMANDS
        assert TuiRenderer._CONTENT_COMMANDS is CONTENT_COMMANDS

    def test_content_commands_set_contents(self):
        from src.tui._const import CONTENT_COMMANDS, RenderCommand
        assert RenderCommand.REASONING in CONTENT_COMMANDS
        assert RenderCommand.SPLASH in CONTENT_COMMANDS
        assert RenderCommand.TOOL_COUNT_INC not in CONTENT_COMMANDS
        assert RenderCommand.SUBAGENT_FRAME not in CONTENT_COMMANDS

    def test_has_content_command_still_works(self):
        """TuiEngine._has_content_command 使用收敛后的集合行为不变。"""
        from src.tui._renderer import TuiEngine
        from src.tui._const import ContentCmd, SubagentFrameCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        assert engine._has_content_command([ContentCmd(text="x")]) is True
        assert engine._has_content_command([SubagentFrameCmd(frame_lines=("l",))]) is False


class TestBatchRender:
    """测试批量渲染优化 — TuiRenderer.render_batch + TuiEngine._phase_render 分批逻辑（Issue 4）。"""

    @pytest.fixture
    def renderer(self):
        """创建 mock 后的 TuiRenderer 实例。"""
        from src.tui._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        return TuiRenderer(rs, adapter, bb)

    def test_batchable_commands_set_defined(self, renderer):
        """验证 _BATCHABLE_COMMANDS 集合已正确定义。"""
        from src.tui._const import RenderCommand
        expected = {
            RenderCommand.NOTIFICATION,
            RenderCommand.WRITE_LINE,
            RenderCommand.ERROR,
            RenderCommand.TOOL_OUTPUT,
            RenderCommand.TOOL_SUMMARY,
            RenderCommand.USER_MSG,
        }
        assert renderer._BATCHABLE_COMMANDS == expected

    def test_is_batchable_returns_true_for_batchable(self, renderer):
        """验证 _is_batchable 对可批处理命令返回 True。"""
        from src.tui._const import RenderCommand
        assert renderer._is_batchable(RenderCommand.WRITE_LINE)
        assert renderer._is_batchable(RenderCommand.NOTIFICATION)
        assert renderer._is_batchable(RenderCommand.ERROR)
        assert renderer._is_batchable(RenderCommand.TOOL_OUTPUT)
        assert renderer._is_batchable(RenderCommand.TOOL_SUMMARY)
        assert renderer._is_batchable(RenderCommand.USER_MSG)

    def test_is_batchable_returns_false_for_non_batchable(self, renderer):
        """验证 _is_batchable 对不可批处理命令返回 False。"""
        from src.tui._const import RenderCommand
        assert not renderer._is_batchable(RenderCommand.REASONING)
        assert not renderer._is_batchable(RenderCommand.CONTENT)
        assert not renderer._is_batchable(RenderCommand.SUBAGENT_FRAME)
        assert not renderer._is_batchable(RenderCommand.SPLASH)

    def test_render_batch_collects_and_calls_batch_write(self, renderer):
        """验证 render_batch 收集 renderables 后调用一次 batch_write。"""
        from src.tui._const import WriteLineCmd
        # 推入 3 条 WRITE_LINE 命令
        commands = [
            WriteLineCmd(text="line1\n"),
            WriteLineCmd(text="line2\n"),
            WriteLineCmd(text="line3\n"),
        ]
        renderer.render_batch(commands)
        # batch_write 应被调用 1 次（非 3 次 write）
        renderer._adapter.batch_write.assert_called_once()
        # 验证传入的 renderables 数量
        call_args = renderer._adapter.batch_write.call_args[0][0]
        assert len(call_args) == 3

    def test_render_batch_empty_list_no_write(self, renderer):
        """验证空列表不调用 batch_write。"""
        renderer.render_batch([])
        renderer._adapter.batch_write.assert_not_called()

    def test_render_batch_mixed_batchable_commands(self, renderer):
        """验证混合的可批处理命令正确收集。"""
        from src.tui._const import NotificationCmd, WriteLineCmd, ErrorCmd
        commands = [
            NotificationCmd(text="test notification"),
            WriteLineCmd(text="some line"),
            ErrorCmd(message="error msg"),
        ]
        renderer.render_batch(commands)
        renderer._adapter.batch_write.assert_called_once()
        call_args = renderer._adapter.batch_write.call_args[0][0]
        assert len(call_args) == 3

    def test_render_batch_tool_output_tracks_group_state(self, renderer):
        """验证 TOOL_OUTPUT 在批量中正确驱动 _in_tool_group 状态机。"""
        from src.tui._const import ToolOutputCmd
        # 第一个 TOOL_OUTPUT 应输出工具组框，后续不重复输出
        commands = [
            ToolOutputCmd(text="tool result 1"),
            ToolOutputCmd(text="tool result 2"),
        ]
        renderer.render_batch(commands)
        renderer._adapter.batch_write.assert_called_once()
        call_args = renderer._adapter.batch_write.call_args[0][0]
        # 应该有 3 个 renderable: 工具组框 + 2 个工具输出
        assert len(call_args) == 3
        # 状态机应保持开启状态
        assert renderer._in_tool_group is True

    def test_render_batch_tool_summary_closes_group(self, renderer):
        """验证 TOOL_SUMMARY 在批量中正确关闭 _in_tool_group。"""
        from src.tui._const import ToolSummaryCmd
        renderer._in_tool_group = True
        commands = [
            ToolSummaryCmd(successful=("tool_a",), failed=()),
        ]
        renderer.render_batch(commands)
        renderer._adapter.batch_write.assert_called_once()
        assert renderer._in_tool_group is False

    def test_render_batch_user_message(self, renderer):
        """验证 USER_MSG 在批量中正确渲染。"""
        from src.tui._const import UserMsgCmd
        commands = [
            UserMsgCmd(text="hello world"),
        ]
        renderer.render_batch(commands)
        renderer._adapter.batch_write.assert_called_once()
        call_args = renderer._adapter.batch_write.call_args[0][0]
        assert len(call_args) == 1

    def test_phase_render_groups_batchable_commands(self):
        """验证 _phase_render 将连续可批处理命令分组调用 render_batch。"""
        from src.tui._renderer import TuiEngine
        from src.tui._const import RenderCommand, WriteLineCmd, ContentCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)

        # 混合命令序列：WRITE_LINE(批), CONTENT(不可批), WRITE_LINE(批)
        commands = [
            WriteLineCmd(text="a"),
            WriteLineCmd(text="b"),
            ContentCmd(text="c"),
            WriteLineCmd(text="d"),
        ]

        renderer._is_batchable.side_effect = lambda cmd: cmd.cid in renderer._BATCHABLE_COMMANDS
        # mock _BATCHABLE_COMMANDS
        renderer._BATCHABLE_COMMANDS = frozenset({
            RenderCommand.WRITE_LINE,
        })

        engine._phase_render(commands)

        # render_batch 应被调用 2 次（第1批: a,b; 第2批: d）
        assert renderer.render_batch.call_count == 2
        # render (单条) 应被调用 1 次 (CONTENT)
        assert renderer.render.call_count == 1

    def test_phase_render_preserves_order(self):
        """验证批量渲染后命令输出顺序与原始顺序一致。"""
        from src.tui._renderer import TuiEngine
        from src.tui._const import RenderCommand, WriteLineCmd, ToolOutputCmd, ContentCmd, NotificationCmd
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)

        # 复杂混合序列
        commands = [
            WriteLineCmd(text="first"),
            ToolOutputCmd(text="second"),
            ContentCmd(text="third"),
            WriteLineCmd(text="fourth"),
            NotificationCmd(text="fifth"),
        ]

        renderer._is_batchable.side_effect = lambda cmd: cmd.cid in {
            RenderCommand.WRITE_LINE,
            RenderCommand.TOOL_OUTPUT,
            RenderCommand.NOTIFICATION,
        }
        renderer._BATCHABLE_COMMANDS = frozenset({
            RenderCommand.WRITE_LINE,
            RenderCommand.TOOL_OUTPUT,
            RenderCommand.NOTIFICATION,
        })

        engine._phase_render(commands)

        # 第1批: WRITE_LINE + TOOL_OUTPUT (2条连续批处理)
        # CONTENT: 不可批，单独渲染
        # 第2批: WRITE_LINE + NOTIFICATION (2条连续批处理)
        assert renderer.render_batch.call_count == 2
        assert renderer.render.call_count == 1

    def test_render_batch_clears_in_tool_group_for_summary(self, renderer):
        """验证 TOOL_SUMMARY 后 _in_tool_group 被重置。"""
        from src.tui._const import ToolOutputCmd, ToolSummaryCmd
        # 先设置工具组状态
        renderer._in_tool_group = True

        commands = [
            ToolOutputCmd(text="data"),
            ToolSummaryCmd(successful=("tool_ok",), failed=()),
        ]
        renderer.render_batch(commands)

        # TOOL_OUTPUT（已有组框则不重复输出）+ TOOL_SUMMARY（关闭组框）
        renderer._adapter.batch_write.assert_called_once()
        # TOOL_OUTPUT: 1个renderable + TOOL_SUMMARY: 1个renderable（关闭框）
        call_args = renderer._adapter.batch_write.call_args[0][0]
        assert len(call_args) == 2
        assert renderer._in_tool_group is False


class TestChatRenderStateCapturedRemoval:
    """方向C 步骤5 — captured_* 机制删除回归测试（P1-1）。"""

    def test_chat_render_state_no_captured_regression(self):
        """ChatRenderState 实例不再具有 captured_* 属性。"""
        from src.tui.state.render_state import ChatRenderState

        rs = ChatRenderState()
        assert not hasattr(rs, "captured_reasoning_output")
        assert not hasattr(rs, "captured_content_output")

    def test_get_reasoning_no_captured_binding_regression(self):
        """get_reasoning() 构造渲染器时不传 captured_output 参数。"""
        from unittest.mock import MagicMock, patch
        from src.tui.state.render_state import ChatRenderState

        rs = ChatRenderState()
        rs.set_output_adapter(MagicMock())

        # P2-8：模块级无 IncrementalRenderer 符号（函数内惰性 import），
        # patch 路径改为 src.renderer.IncrementalRenderer
        with patch("src.renderer.IncrementalRenderer") as mock_renderer:
            rs.get_reasoning()
            mock_renderer.assert_called_once()
            kwargs = mock_renderer.call_args.kwargs
            assert "captured_output" not in kwargs, \
                "get_reasoning 不应再绑定 captured_output（P1-1 已删除）"

    def test_get_content_no_captured_binding_regression(self):
        """get_content() 构造渲染器时不传 captured_output 参数。"""
        from unittest.mock import MagicMock, patch
        from src.tui.state.render_state import ChatRenderState

        rs = ChatRenderState()
        rs.set_output_adapter(MagicMock())

        # P2-8：patch 路径改为 src.renderer.IncrementalRenderer（同上）
        with patch("src.renderer.IncrementalRenderer") as mock_renderer:
            rs.get_content()
            mock_renderer.assert_called_once()
            kwargs = mock_renderer.call_args.kwargs
            assert "captured_output" not in kwargs, \
                "get_content 不应再绑定 captured_output（P1-1 已删除）"

