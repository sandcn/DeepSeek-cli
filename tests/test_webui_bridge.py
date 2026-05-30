"""测试 src/webui/bridge.py — WebEventBridge 类"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.ui.events.event_bus import DisplayEventBus
from src.ui.events.event_types import (
    AgentAddedEvent,
    AgentResultEvent,
    AgentStatusChanged,
    ContentChunkEvent,
    DisplayEvent,
    ModelPhaseEvent,
    OutputEvent,
    PhaseDoneEvent,
    ReasoningChunkEvent,
    ToolBatchStartedEvent,
    ToolDoneEvent,
    ToolOutputChunkEvent,
    ToolParsingEvent,
    ToolStartedEvent,
    ToolSummaryEvent,
    UsageUpdatedEvent,
    UserSelectNeededEvent,
)
from src.webui.bridge import WebEventBridge
from src.webui._base_sender import BaseWebSocketSender


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def make_bridge(event_bus=None, select_id_tracker=None) -> tuple[WebEventBridge, MagicMock, MagicMock]:
    """创建 WebEventBridge 实例，返回 (bridge, send_func, event_bus)。"""
    send_func = MagicMock()
    bus = event_bus or MagicMock(spec=DisplayEventBus)
    bridge = WebEventBridge(send_func, event_bus=bus, select_id_tracker=select_id_tracker)
    return bridge, send_func, bus


# ═══════════════════════════════════════════════════════════════
# 构造测试
# ═══════════════════════════════════════════════════════════════

class TestWebEventBridgeInit:
    def test_inherits_base_web_socket_sender(self) -> None:
        bridge, _, _ = make_bridge()
        assert isinstance(bridge, BaseWebSocketSender)

    def test_default_event_bus(self) -> None:
        """未传入 event_bus 时使用 DisplayEventBus.get_default()。"""
        with patch.object(DisplayEventBus, 'get_default', return_value=MagicMock()):
            send_func = MagicMock()
            bridge = WebEventBridge(send_func)
            DisplayEventBus.get_default.assert_called_once()

    def test_custom_event_bus(self) -> None:
        bus = MagicMock(spec=DisplayEventBus)
        bridge, _, _ = make_bridge(event_bus=bus)
        assert bridge._bus is bus

    def test_select_id_tracker_none_by_default(self) -> None:
        bridge, _, _ = make_bridge()
        assert bridge.select_id_tracker is None

    def test_select_id_tracker_custom(self) -> None:
        tracker = set()
        bridge, _, _ = make_bridge(select_id_tracker=tracker)
        assert bridge.select_id_tracker is tracker


# ═══════════════════════════════════════════════════════════════
# subscribe / unsubscribe
# ═══════════════════════════════════════════════════════════════

class TestWebEventBridgeSubscribe:
    def test_subscribe_registers_all_handlers(self) -> None:
        send_func = MagicMock()
        bus = MagicMock(spec=DisplayEventBus)
        bridge = WebEventBridge(send_func, event_bus=bus)
        bridge.subscribe()

        # _EVENT_BINDINGS 中有 16 个绑定项
        expected_count = len(WebEventBridge._EVENT_BINDINGS)
        assert bus.subscribe.call_count == expected_count
        assert len(bridge._handlers) == expected_count

    def test_subscribe_event_types(self) -> None:
        send_func = MagicMock()
        bus = MagicMock(spec=DisplayEventBus)
        bridge = WebEventBridge(send_func, event_bus=bus)
        bridge.subscribe()

        for i, (event_type, method_name) in enumerate(WebEventBridge._EVENT_BINDINGS):
            call_args = bus.subscribe.call_args_list[i]
            handler = call_args[0][0]
            kw_event_type = call_args[1].get("event_type")
            assert handler.__name__ == method_name
            assert kw_event_type is event_type

    def test_unsubscribe_clears_all_handlers(self) -> None:
        send_func = MagicMock()
        bus = MagicMock(spec=DisplayEventBus)
        bridge = WebEventBridge(send_func, event_bus=bus)
        bridge.subscribe()
        bridge.unsubscribe()

        expected_count = len(WebEventBridge._EVENT_BINDINGS)
        assert bus.unsubscribe.call_count == expected_count
        assert len(bridge._handlers) == 0

    def test_unsubscribe_without_subscribe_does_not_crash(self) -> None:
        bridge, _, _ = make_bridge()
        bridge.unsubscribe()  # 空列表，不应报错
        assert len(bridge._handlers) == 0

    def test_unsubscribe_handles_exception_gracefully(self) -> None:
        send_func = MagicMock()
        bus = MagicMock(spec=DisplayEventBus)
        bus.unsubscribe.side_effect = ValueError("test error")
        bridge = WebEventBridge(send_func, event_bus=bus)
        bridge.subscribe()
        bridge.unsubscribe()  # 不应抛出异常
        assert len(bridge._handlers) == 0


# ═══════════════════════════════════════════════════════════════
# select_id_tracker property
# ═══════════════════════════════════════════════════════════════

class TestSelectIdTracker:
    def test_getter(self) -> None:
        tracker = {"sel-1"}
        bridge, _, _ = make_bridge(select_id_tracker=tracker)
        assert bridge.select_id_tracker is tracker

    def test_setter(self) -> None:
        bridge, _, _ = make_bridge()
        assert bridge.select_id_tracker is None
        new_tracker = {"sel-2"}
        bridge.select_id_tracker = new_tracker
        assert bridge.select_id_tracker is new_tracker

    def test_setter_to_none(self) -> None:
        tracker = {"sel-1"}
        bridge, _, _ = make_bridge(select_id_tracker=tracker)
        bridge.select_id_tracker = None
        assert bridge.select_id_tracker is None


# ═══════════════════════════════════════════════════════════════
# _on_content_chunk
# ═══════════════════════════════════════════════════════════════

class TestOnContentChunk:
    def test_sends_when_label_not_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ContentChunkEvent(text="hello", label="main-agent")
        bridge._on_content_chunk(event)
        send_func.assert_called_once_with({
            "type": "content_chunk", "text": "hello", "label": "main-agent",
        })

    def test_skips_when_label_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = ContentChunkEvent(text="hello", label="subagent-1")
        bridge._on_content_chunk(event)
        send_func.assert_not_called()

    def test_skips_empty_text(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ContentChunkEvent(text="", label="main-agent")
        bridge._on_content_chunk(event)
        send_func.assert_not_called()

    def test_skips_non_content_chunk_event(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = DisplayEvent(source="main-agent")
        bridge._on_content_chunk(event)
        send_func.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# _on_reasoning_chunk
# ═══════════════════════════════════════════════════════════════

class TestOnReasoningChunk:
    def test_sends_when_label_not_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ReasoningChunkEvent(text="thinking...", label="main-agent")
        bridge._on_reasoning_chunk(event)
        send_func.assert_called_once_with({
            "type": "reasoning_chunk", "text": "thinking...", "label": "main-agent",
        })

    def test_skips_when_label_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = ReasoningChunkEvent(text="thinking...", label="subagent-1")
        bridge._on_reasoning_chunk(event)
        send_func.assert_not_called()

    def test_skips_empty_text(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ReasoningChunkEvent(text="", label="main-agent")
        bridge._on_reasoning_chunk(event)
        send_func.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# _on_phase_done
# ═══════════════════════════════════════════════════════════════

class TestOnPhaseDone:
    def test_sends_when_label_not_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = PhaseDoneEvent(phase="reasoning", label="main-agent")
        bridge._on_phase_done(event)
        send_func.assert_called_once_with({
            "type": "phase_done", "phase": "reasoning", "label": "main-agent",
        })

    def test_skips_when_label_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = PhaseDoneEvent(phase="reasoning", label="subagent-1")
        bridge._on_phase_done(event)
        send_func.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# _on_tool_output
# ═══════════════════════════════════════════════════════════════

class TestOnToolOutput:
    def test_sends_with_text(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolOutputChunkEvent(label="tool-1", text="output data")
        bridge._on_tool_output(event)
        send_func.assert_called_once_with({
            "type": "tool_output_chunk", "label": "tool-1", "text": "output data",
        })

    def test_skips_empty_text(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolOutputChunkEvent(label="tool-1", text="")
        bridge._on_tool_output(event)
        send_func.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# _on_user_select_needed
# ═══════════════════════════════════════════════════════════════

class TestOnUserSelectNeeded:
    def test_sends_and_adds_to_tracker(self) -> None:
        tracker = set()
        bridge, send_func, _ = make_bridge(select_id_tracker=tracker)
        event = UserSelectNeededEvent(
            select_id="sel-1", title="请选择", options=("a", "b"),
            multi_select=True, default_options=("a",), timeout=120,
        )
        bridge._on_user_select_needed(event)
        assert "sel-1" in tracker
        send_func.assert_called_once_with({
            "type": "user_select_needed", "select_id": "sel-1",
            "title": "请选择", "options": ["a", "b"],
            "multi_select": True, "default_options": ["a"], "timeout": 120,
        })

    def test_works_without_tracker(self) -> None:
        bridge, send_func, _ = make_bridge(select_id_tracker=None)
        event = UserSelectNeededEvent(
            select_id="sel-1", title="选择", options=("a",),
            multi_select=False, default_options=(), timeout=30,
        )
        bridge._on_user_select_needed(event)
        send_func.assert_called_once()
        # tracker 为 None 时不应崩溃


# ═══════════════════════════════════════════════════════════════
# _on_agent_added
# ═══════════════════════════════════════════════════════════════

class TestOnAgentAdded:
    def test_adds_label_and_sends(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = AgentAddedEvent(label="subagent-1", description="分析代码", status="running")
        bridge._on_agent_added(event)
        assert "subagent-1" in bridge._agent_labels
        send_func.assert_called_once_with({
            "type": "agent_added", "label": "subagent-1",
            "description": "分析代码", "status": "running",
        })

    def test_with_source(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = AgentAddedEvent(label="subagent-1", description="分析", status="running")
        # AgentAddedEvent 的 source 字段来自 DisplayEvent
        object.__setattr__(event, 'source', "parent-agent")
        bridge._on_agent_added(event)
        sent = send_func.call_args[0][0]
        assert sent["type"] == "agent_added"


# ═══════════════════════════════════════════════════════════════
# _on_tool_summary
# ═══════════════════════════════════════════════════════════════

class TestOnToolSummary:
    def test_sends_summary(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolSummaryEvent(
            successful_tools=("read_file",),
            failed_tools=(("bad_tool", "error msg"),),
        )
        bridge._on_tool_summary(event)
        send_func.assert_called_once_with({
            "type": "tool_summary",
            "successful_tools": ["read_file"],
            "failed_tools": [{"name": "bad_tool", "error": "error msg"}],
        })

    def test_empty_lists(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolSummaryEvent(successful_tools=(), failed_tools=())
        bridge._on_tool_summary(event)
        sent = send_func.call_args[0][0]
        assert sent["successful_tools"] == []
        assert sent["failed_tools"] == []


# ═══════════════════════════════════════════════════════════════
# _on_agent_status
# ═══════════════════════════════════════════════════════════════

class TestOnAgentStatus:
    def test_sends_status_and_keeps_label_for_running(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = AgentStatusChanged(label="subagent-1", status="running")
        bridge._on_agent_status(event)
        assert "subagent-1" in bridge._agent_labels
        send_func.assert_called_once_with({
            "type": "agent_status", "label": "subagent-1", "status": "running",
        })

    def test_removes_label_on_done(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = AgentStatusChanged(label="subagent-1", status="done")
        bridge._on_agent_status(event)
        assert "subagent-1" not in bridge._agent_labels

    def test_removes_label_on_fail(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = AgentStatusChanged(label="subagent-1", status="fail")
        bridge._on_agent_status(event)
        assert "subagent-1" not in bridge._agent_labels

    def test_removes_label_on_error(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = AgentStatusChanged(label="subagent-1", status="error")
        bridge._on_agent_status(event)
        assert "subagent-1" not in bridge._agent_labels


# ═══════════════════════════════════════════════════════════════
# _on_subagent_tool_event
# ═══════════════════════════════════════════════════════════════

class TestOnSubagentToolEvent:
    def test_skips_if_source_not_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolParsingEvent(source="unknown-agent", tool_name="read_file")
        bridge._on_subagent_tool_event(event)
        send_func.assert_not_called()

    def test_forwards_tool_parsing(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = ToolParsingEvent(source="subagent-1", tool_name="read_file", arguments='{"path": "x.py"}')
        bridge._on_subagent_tool_event(event)
        send_func.assert_called_once_with({
            "type": "agent_tool_parsing",
            "agent_label": "subagent-1",
            "tool_name": "read_file",
            "arguments": '{"path": "x.py"}',
        })

    def test_forwards_tool_started(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = ToolStartedEvent(source="subagent-1", tool_name="read_file", detail="reading")
        bridge._on_subagent_tool_event(event)
        send_func.assert_called_once_with({
            "type": "agent_tool_started",
            "agent_label": "subagent-1",
            "tool_name": "read_file",
            "detail": "reading",
        })

    def test_forwards_tool_done(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = ToolDoneEvent(source="subagent-1", tool_name="read_file", success=True)
        bridge._on_subagent_tool_event(event)
        send_func.assert_called_once_with({
            "type": "agent_tool_done",
            "agent_label": "subagent-1",
            "tool_name": "read_file",
            "success": True,
        })

    def test_unknown_event_type_defaults_to_agent_tool_event(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        # 用一个不在映射中的事件类型 — 需要 tool_name 属性
        from unittest.mock import MagicMock
        event = MagicMock(spec=DisplayEvent)
        event.source = "subagent-1"
        event.tool_name = "unknown_tool"
        bridge._on_subagent_tool_event(event)
        send_func.assert_called_once()
        sent = send_func.call_args[0][0]
        assert sent["type"] == "agent_tool_parsing"  # fallback 现降级为 agent_tool_parsing
        assert sent["tool_name"] == "unknown_tool"
        assert sent.get("arguments") == ""  # 降级时 arguments 默认为空串


# ═══════════════════════════════════════════════════════════════
# _on_subagent_phase_event
# ═══════════════════════════════════════════════════════════════

class TestOnSubagentPhaseEvent:
    def test_skips_if_source_not_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ModelPhaseEvent(source="unknown", phase="thinking")
        bridge._on_subagent_phase_event(event)
        send_func.assert_not_called()

    def test_forwards_phase_event(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = ModelPhaseEvent(source="subagent-1", phase="thinking", info="deep")
        bridge._on_subagent_phase_event(event)
        send_func.assert_called_once_with({
            "type": "agent_phase",
            "agent_label": "subagent-1",
            "phase": "thinking",
            "info": "deep",
        })


# ═══════════════════════════════════════════════════════════════
# _on_subagent_usage_event
# ═══════════════════════════════════════════════════════════════

class TestOnSubagentUsageEvent:
    def test_skips_if_source_not_in_agent_labels(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = UsageUpdatedEvent(source="unknown", usage={"input": 100})
        bridge._on_subagent_usage_event(event)
        send_func.assert_not_called()

    def test_forwards_usage_event(self) -> None:
        bridge, send_func, _ = make_bridge()
        bridge._agent_labels.add("subagent-1")
        event = UsageUpdatedEvent(source="subagent-1", usage={"input": 100, "output": 50})
        bridge._on_subagent_usage_event(event)
        send_func.assert_called_once_with({
            "type": "agent_usage",
            "agent_label": "subagent-1",
            "usage": {"input": 100, "output": 50},
        })


# ═══════════════════════════════════════════════════════════════
# _on_agent_result
# ═══════════════════════════════════════════════════════════════

class TestOnAgentResult:
    def test_forwards_result(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = AgentResultEvent(label="subagent-1", description="分析代码",
                                 result="成功", error="")
        bridge._on_agent_result(event)
        send_func.assert_called_once_with({
            "type": "agent_result",
            "agent_label": "subagent-1",
            "description": "分析代码",
            "result": "成功",
            "error": "",
        })

    def test_forwards_error(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = AgentResultEvent(label="subagent-1", description="分析",
                                 result="", error="超时")
        bridge._on_agent_result(event)
        sent = send_func.call_args[0][0]
        assert sent["error"] == "超时"

    def test_skips_non_agent_result_event(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = DisplayEvent()
        bridge._on_agent_result(event)
        send_func.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# _on_tool_batch_start
# ═══════════════════════════════════════════════════════════════

class TestOnToolBatchStart:
    def test_forwards_batch_start(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolBatchStartedEvent(label="agent-1", tool_names=("read_file", "write_file"))
        bridge._on_tool_batch_start(event)
        send_func.assert_called_once_with({
            "type": "tool_batch_start",
            "label": "agent-1",
            "names": ["read_file", "write_file"],
        })

    def test_empty_tool_names(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = ToolBatchStartedEvent(label="agent-1", tool_names=())
        bridge._on_tool_batch_start(event)
        sent = send_func.call_args[0][0]
        assert sent["names"] == []


# ═══════════════════════════════════════════════════════════════
# _on_output_event
# ═══════════════════════════════════════════════════════════════

class TestOnOutputEvent:
    def test_sends_with_text(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = OutputEvent(text="hello", level="info")
        bridge._on_output_event(event)
        send_func.assert_called_once_with({
            "type": "command_output", "text": "hello", "level": "info",
        })

    def test_skips_empty_text(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = OutputEvent(text="", level="info")
        bridge._on_output_event(event)
        send_func.assert_not_called()

    def test_different_level(self) -> None:
        bridge, send_func, _ = make_bridge()
        event = OutputEvent(text="error!", level="error")
        bridge._on_output_event(event)
        sent = send_func.call_args[0][0]
        assert sent["level"] == "error"
