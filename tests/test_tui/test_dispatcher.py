"""测试 EventDispatcher — 事件→命令映射与过滤。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.tui.engine.dispatcher import EventDispatcher, EventHandlerRegistry
from src.tui.engine.const import RenderCommand
from src.tui.events import event_types as _EVENT_TYPES
from src.tui.testing import tui_test_env


# ═══════════════════════════════════════════════════════════
# EventHandlerRegistry
# ═══════════════════════════════════════════════════════════

class TestEventHandlerRegistry:
    """EventHandlerRegistry 注册表测试。"""

    def test_register_and_resolve(self):
        """注册后能解析到对应处理器方法名。"""
        registry = EventHandlerRegistry()
        registry.register(_EVENT_TYPES.ReasoningChunkEvent, "_on_reasoning_chunk")
        assert registry.resolve(_EVENT_TYPES.ReasoningChunkEvent) == "_on_reasoning_chunk"

    def test_resolve_unregistered(self):
        """未注册的事件类型返回 None。"""
        registry = EventHandlerRegistry()
        assert registry.resolve(_EVENT_TYPES.ReasoningChunkEvent) is None

    def test_list_registered_empty(self):
        """新注册表空时返回空字典。"""
        registry = EventHandlerRegistry()
        assert registry.list_registered() == {}

    def test_list_registered_returns_copy(self):
        """list_registered 返回副本，修改不影响原字典。"""
        registry = EventHandlerRegistry()
        registry.register(_EVENT_TYPES.ReasoningChunkEvent, "_on_reasoning_chunk")
        result = registry.list_registered()
        result.clear()
        assert len(registry.list_registered()) == 1

    def test_register_multiple(self):
        """注册多个事件类型。"""
        registry = EventHandlerRegistry()
        registry.register(_EVENT_TYPES.ReasoningChunkEvent, "_on_reasoning_chunk")
        registry.register(_EVENT_TYPES.ContentChunkEvent, "_on_content_chunk")
        assert len(registry.list_registered()) == 2

    def test_register_overwrites(self):
        """重复注册同一事件类型覆盖旧值。"""
        registry = EventHandlerRegistry()
        registry.register(_EVENT_TYPES.ReasoningChunkEvent, "_old")
        registry.register(_EVENT_TYPES.ReasoningChunkEvent, "_new")
        assert registry.resolve(_EVENT_TYPES.ReasoningChunkEvent) == "_new"

    def test_register_defaults(self):
        """register_defaults 注册 12 种默认事件映射。"""
        registry = EventHandlerRegistry()
        registry.register_defaults()
        mapping = registry.list_registered()
        assert len(mapping) == 12
        assert _EVENT_TYPES.ReasoningChunkEvent in mapping
        assert _EVENT_TYPES.ContentChunkEvent in mapping
        assert _EVENT_TYPES.PhaseDoneEvent in mapping
        assert _EVENT_TYPES.ToolParsingEvent in mapping
        assert _EVENT_TYPES.ToolStartedEvent in mapping
        assert _EVENT_TYPES.ToolDoneEvent in mapping
        assert _EVENT_TYPES.ToolOutputChunkEvent in mapping
        assert _EVENT_TYPES.ParseInfoEvent in mapping
        assert _EVENT_TYPES.ParseInfoDoneEvent in mapping
        assert _EVENT_TYPES.OutputEvent in mapping
        assert _EVENT_TYPES.ModelPhaseEvent in mapping
        assert _EVENT_TYPES.ToolSummaryEvent in mapping


# ═══════════════════════════════════════════════════════════
# EventDispatcher — 辅助函数
# ═══════════════════════════════════════════════════════════

def make_dispatcher():
    """创建 EventDispatcher 实例（使用 MagicMock 作为 push_cmd）。"""
    push_cmd = MagicMock()
    dispatcher = EventDispatcher(push_cmd=push_cmd)
    return dispatcher, push_cmd


def _make_event(event_class, **kwargs):
    """创建事件实例。"""
    return event_class(**kwargs)


# ═══════════════════════════════════════════════════════════
# EventDispatcher — 事件映射测试
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherEventMapping:
    """12 种事件类型到 RenderCommand 的映射。"""

    def test_reasoning_chunk_maps_to_reasoning(self):
        """ReasoningChunkEvent → REASONING。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ReasoningChunkEvent,
                                text="thinking...", label="assistant", source="agent")
            dispatcher._on_reasoning_chunk(event)
            push_cmd.assert_called_with((RenderCommand.REASONING, "thinking..."))

    def test_content_chunk_maps_to_content(self):
        """ContentChunkEvent → CONTENT。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ContentChunkEvent,
                                text="Hello!", label="assistant", source="agent")
            dispatcher._on_content_chunk(event)
            push_cmd.assert_called_with((RenderCommand.CONTENT, "Hello!"))

    def test_phase_done_maps_to_phase_done(self):
        """PhaseDoneEvent → PHASE_DONE。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.PhaseDoneEvent,
                                phase="reasoning", label="assistant", source="agent")
            dispatcher._on_phase_done(event)
            push_cmd.assert_called_with((RenderCommand.PHASE_DONE, "reasoning"))

    def test_tool_parsing_maps_to_main_phase(self):
        """ToolParsingEvent → MAIN_PHASE("parsing")。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolParsingEvent,
                                tool_name="bash", source="agent")
            dispatcher._on_tool_parsing(event)
            push_cmd.assert_called_with((RenderCommand.MAIN_PHASE, "parsing"))

    def test_tool_started_maps_to_tool_count_inc(self):
        """ToolStartedEvent → TOOL_COUNT_INC。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolStartedEvent,
                                tool_name="bash", source="agent")
            dispatcher._on_tool_started(event)
            push_cmd.assert_called_with((RenderCommand.TOOL_COUNT_INC,))

    def test_tool_done_success_maps_to_decrement(self):
        """ToolDoneEvent(success=True) → TOOL_COUNT_DEC。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolDoneEvent,
                                tool_name="bash", success=True, source="agent")
            dispatcher._on_tool_done(event)
            push_cmd.assert_called_with((RenderCommand.TOOL_COUNT_DEC,))

    def test_tool_done_fail_maps_to_fail_inc_and_decrement(self):
        """ToolDoneEvent(success=False) → TOOL_FAIL_INC + TOOL_COUNT_DEC。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolDoneEvent,
                                tool_name="bash", success=False, source="agent")
            dispatcher._on_tool_done(event)
            assert push_cmd.call_count == 2
            push_cmd.assert_any_call((RenderCommand.TOOL_FAIL_INC,))
            push_cmd.assert_any_call((RenderCommand.TOOL_COUNT_DEC,))

    def test_tool_output_maps_to_tool_output(self):
        """ToolOutputChunkEvent → TOOL_OUTPUT。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolOutputChunkEvent,
                                text="stdout line\n", source="agent")
            dispatcher._on_tool_output(event)
            push_cmd.assert_called_with((RenderCommand.TOOL_OUTPUT, "stdout line"))

    def test_tool_output_strips_trailing_newline(self):
        """ToolOutputChunkEvent 末尾换行被去除。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolOutputChunkEvent,
                                text="line1\nline2\n\n", source="agent")
            dispatcher._on_tool_output(event)
            # rstrip("\n") 后应为 "line1\nline2"
            args = push_cmd.call_args[0][0]
            assert args[1] == "line1\nline2"

    def test_tool_summary_maps_to_tool_summary(self):
        """ToolSummaryEvent → TOOL_SUMMARY。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolSummaryEvent,
                                successful_tools=("bash",), failed_tools=(), source="agent")
            dispatcher._on_tool_summary(event)
            push_cmd.assert_called_with((RenderCommand.TOOL_SUMMARY, ("bash",), ()))

    def test_tool_summary_empty_skipped(self):
        """ToolSummaryEvent 空结果时跳过。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolSummaryEvent,
                                successful_tools=(), failed_tools=(), source="agent")
            dispatcher._on_tool_summary(event)
            push_cmd.assert_not_called()

    def test_parse_info_maps_to_parse_info(self):
        """ParseInfoEvent → PARSE_INFO。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ParseInfoEvent,
                                tool_names="bash grep", tokens=150, elapsed=2.5, source="agent")
            dispatcher._on_parse_info(event)
            push_cmd.assert_called_with((RenderCommand.PARSE_INFO, "bash grep", 150, 2.5))

    def test_parse_info_done_maps_to_clear(self):
        """ParseInfoDoneEvent → PARSE_INFO("", _CLEAR_PARSE_LINE, 0.0)。"""
        with tui_test_env():
            from src.tui.engine.const import _CLEAR_PARSE_LINE
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ParseInfoDoneEvent, source="agent")
            dispatcher._on_parse_info_done(event)
            push_cmd.assert_called_with((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def test_output_maps_to_write_line(self):
        """OutputEvent → WRITE_LINE。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.OutputEvent, text="hello", level="info")
            dispatcher._on_output(event)
            push_cmd.assert_called_with((RenderCommand.WRITE_LINE, "hello"))

    def test_output_empty_skipped(self):
        """OutputEvent 空文本时跳过。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.OutputEvent, text="")
            dispatcher._on_output(event)
            push_cmd.assert_not_called()

    def test_model_phase_error_maps_to_error(self):
        """ModelPhaseEvent(phase="error") → ERROR。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ModelPhaseEvent,
                                phase="error", info="API error", label="assistant", source="agent")
            dispatcher._on_model_phase(event)
            push_cmd.assert_called()
            cmd = push_cmd.call_args[0][0]
            assert cmd[0] == RenderCommand.ERROR

    def test_model_phase_non_error_maps_to_main_phase(self):
        """ModelPhaseEvent(phase!="error") → MAIN_PHASE。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ModelPhaseEvent,
                                phase="thinking", label="assistant", source="agent")
            dispatcher._on_model_phase(event)
            push_cmd.assert_called_with((RenderCommand.MAIN_PHASE, "thinking"))


class TestEventDispatcherFilter:
    """事件过滤测试。"""

    def test_reasoning_chunk_wrong_label_filtered(self):
        """ReasoningChunkEvent label 不匹配时跳过。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ReasoningChunkEvent,
                                text="thinking", label="subagent-1", source="subagent")
            dispatcher._on_reasoning_chunk(event)
            push_cmd.assert_not_called()

    def test_reasoning_chunk_empty_text_filtered(self):
        """ReasoningChunkEvent 空文本时跳过。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ReasoningChunkEvent,
                                text="", label="assistant", source="agent")
            dispatcher._on_reasoning_chunk(event)
            push_cmd.assert_not_called()

    def test_tool_parsing_wrong_source_filtered(self):
        """ToolParsingEvent source 不匹配且 require_source=True 时跳过。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolParsingEvent,
                                tool_name="bash", source="other")
            dispatcher._on_tool_parsing(event)
            push_cmd.assert_not_called()

    def test_model_phase_no_info_skipped(self):
        """ModelPhaseEvent error 阶段但 info 为空时跳过。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ModelPhaseEvent,
                                phase="error", info="", label="assistant", source="agent")
            dispatcher._on_model_phase(event)
            push_cmd.assert_not_called()

    def test_is_agent_source_agent_prefix(self):
        """source 以 "agent-" 开头时通过检查。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            assert dispatcher._is_agent_source("agent-1") is True
            assert dispatcher._is_agent_source("agent-main") is True

    def test_is_agent_source_exact_match(self):
        """source 精确匹配 main_source 时通过检查。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            from src.tui.consumer.chat_config import ChatConfig
            main_source = ChatConfig.defaults().main_source
            assert dispatcher._is_agent_source(main_source) is True

    def test_is_agent_source_none(self):
        """source=None 时返回 False。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            assert dispatcher._is_agent_source(None) is False

    def test_is_agent_source_unrelated(self):
        """不相关 source 返回 False。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            assert dispatcher._is_agent_source("user") is False
            assert dispatcher._is_agent_source("system") is False


class TestEventDispatcherListHandlers:
    """list_handlers 方法测试。"""

    def test_list_handlers_returns_12_defaults(self):
        """list_handlers 返回 12 个默认处理器。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            handlers = dispatcher.list_handlers()
            assert len(handlers) == 12

    def test_list_handlers_are_callable(self):
        """list_handlers 返回的处理器都是可调用对象。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            handlers = dispatcher.list_handlers()
            for event_type, handler in handlers.items():
                assert callable(handler)

    def test_list_handlers_returns_copy(self):
        """list_handlers 返回新的字典副本。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            handlers1 = dispatcher.list_handlers()
            handlers2 = dispatcher.list_handlers()
            assert handlers1 is not handlers2


class TestEventDispatcherRegisterHandler:
    """register_handler 方法测试。"""

    def test_register_custom_handler(self):
        """注册自定义处理器后 list_handlers 包含它。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            custom = MagicMock()
            dispatcher.register_handler(_EVENT_TYPES.OutputEvent, custom)
            handlers = dispatcher.list_handlers()
            assert _EVENT_TYPES.OutputEvent in handlers
            assert handlers[_EVENT_TYPES.OutputEvent] is custom

    def test_register_handler_overrides_default(self):
        """注册同名事件类型的自定义处理器覆盖默认。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            custom = MagicMock()
            dispatcher.register_handler(_EVENT_TYPES.OutputEvent, custom)
            handlers = dispatcher.list_handlers()
            # 自定义处理器应覆盖默认的 _on_output
            assert handlers[_EVENT_TYPES.OutputEvent] is custom


class TestEventDispatcherEdgeCases:
    """边界条件测试。"""

    def test_push_cmd_not_called_on_filtered_event(self):
        """被过滤的事件不触发 push_cmd。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ReasoningChunkEvent,
                                text="test", label="wrong-label", source="agent")
            dispatcher._on_reasoning_chunk(event)
            push_cmd.assert_not_called()

    def test_none_source_handling(self):
        """source 为 None 的事件安全处理。"""
        with tui_test_env():
            dispatcher, push_cmd = make_dispatcher()
            event = _make_event(_EVENT_TYPES.ToolParsingEvent,
                                tool_name="bash", source=None)
            dispatcher._on_tool_parsing(event)  # 应安全跳过
            push_cmd.assert_not_called()

    def test_config_provided(self):
        """传入 config 时使用 config 的值。"""
        with tui_test_env():
            from src.tui.consumer.chat_config import ChatConfig
            config = ChatConfig(main_label="custom", main_source="custom-source")
            push_cmd = MagicMock()
            dispatcher = EventDispatcher(push_cmd=push_cmd, config=config)
            event = _make_event(_EVENT_TYPES.ReasoningChunkEvent,
                                text="test", label="custom", source="custom-source")
            dispatcher._on_reasoning_chunk(event)
            push_cmd.assert_called()
