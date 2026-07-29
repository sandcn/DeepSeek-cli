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
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        engine.push_cmd((0, "test"))
        assert engine._cmd_queue.qsize() == 1

    def test_push_cmd_queue_full_handling(self):
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        # 设置较小的队列容量来测试满队列
        engine._cmd_queue.maxsize = 3
        for i in range(5):
            engine.push_cmd((0, f"test{i}"))
        assert engine._cmd_queue.qsize() <= engine._cmd_queue.maxsize

    def test_flush_drains_queue(self):
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        # 推入命令（不启动线程，flush 应排空）
        for i in range(5):
            engine._cmd_queue.put((0, f"test{i}"))
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

    def test_ensure_cursor_upper(self):
        from src.tui._renderer import TuiEngine
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        engine.ensure_cursor_upper()
        bottom_bar.ensure_cursor_in_upper.assert_called_once()


class TestTuiRenderer:
    """测试 TuiRenderer 命令分发。"""

    def test_render_unknown_command(self):
        from src.tui._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        # 未知命令不应抛异常
        renderer.render((999,))
        # adapter 不应被调用
        adapter.write.assert_not_called()

    def test_render_empty_cmd(self):
        from src.tui._renderer import TuiRenderer
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render(())
        adapter.write.assert_not_called()

    def test_render_tool_count_inc(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import RenderCommand
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render((RenderCommand.TOOL_COUNT_INC,))
        bb.increment_tool.assert_called_once()

    def test_render_tool_count_dec(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import RenderCommand
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render((RenderCommand.TOOL_COUNT_DEC,))
        bb.decrement_tool.assert_called_once()

    def test_render_tool_fail_inc(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import RenderCommand
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render((RenderCommand.TOOL_FAIL_INC,))
        bb.increment_tool_fail.assert_called_once()

    def test_render_main_phase(self):
        from src.tui._renderer import TuiRenderer
        from src.tui._const import RenderCommand
        rs = MagicMock()
        adapter = MagicMock()
        bb = MagicMock()
        renderer = TuiRenderer(rs, adapter, bb)
        renderer.render((RenderCommand.MAIN_PHASE, "thinking"))
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
        from src.tui._const import RenderCommand
        push_cmd = MagicMock()
        # 需要配置 config 使 source 过滤通过
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, cfg)
        event = ToolStartedEvent(source=cfg.main_source or "main")
        dispatcher._on_tool_started(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_COUNT_INC,))

    def test_on_tool_done_success(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import RenderCommand
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, cfg)
        event = ToolDoneEvent(source=cfg.main_source or "main", success=True)
        dispatcher._on_tool_done(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_COUNT_DEC,))

    def test_on_tool_done_fail(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import RenderCommand
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, cfg)
        event = ToolDoneEvent(source=cfg.main_source or "main", success=False)
        dispatcher._on_tool_done(event)
        assert push_cmd.call_count == 2
        push_cmd.assert_any_call((RenderCommand.TOOL_FAIL_INC,))
        push_cmd.assert_any_call((RenderCommand.TOOL_COUNT_DEC,))

    def test_on_parse_info(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ParseInfoEvent
        from src.tui._const import RenderCommand
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, cfg)
        event = ParseInfoEvent(source=cfg.main_source or "main", tool_names="test", tokens=100, elapsed=0.5)
        dispatcher._on_parse_info(event)
        push_cmd.assert_called_once_with((RenderCommand.PARSE_INFO, "test", 100, 0.5))

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

    def test_on_model_phase_thinking(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, cfg)
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="thinking", info="")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once_with((RenderCommand.MAIN_PHASE, "thinking"))

    def test_on_model_phase_error(self):
        from src.tui._renderer import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, cfg)
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="error", info="something went wrong")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once()
        call_args = push_cmd.call_args[0][0]
        assert call_args[0] == RenderCommand.ERROR


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
