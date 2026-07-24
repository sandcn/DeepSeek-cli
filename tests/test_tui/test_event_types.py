"""测试 UI 显示层事件类型 — event_types.py

覆盖场景：
  - 每种事件类型创建 + 属性验证（21 种全部覆盖）
  - frozen dataclass 不可变性验证
  - 默认值验证（timestamp/source/其他字段默认值）
  - field 类型验证
  - 边界值：空字符串 label、空 tuple tool_names、None metadata、超大数值
  - ALL_EVENT_TYPES 注册表完整性
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from typing import Any, Dict, Optional

import pytest

from src.tui.events.event_types import (
    DisplayEvent,
    SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
    ToolOutputChunkEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
    ContentChunkEvent, ReasoningChunkEvent,
    ParseInfoEvent, ParseInfoDoneEvent, MetricsUpdateEvent,
    OutputEvent, ToolSummaryEvent,
    UserSelectNeededEvent, AgentResultEvent,
    ALL_EVENT_TYPES,
)


# ═══════════════════════════════════════════════════════════
# 基础事件 DisplayEvent
# ═══════════════════════════════════════════════════════════

class TestDisplayEvent:
    """测试 DisplayEvent 基类。"""

    def test_default_values(self):
        """默认 timestamp 不为 0，source 为空字符串。"""
        e = DisplayEvent()
        assert e.timestamp > 0, "timestamp 应有默认值（time.time()）"
        assert e.source == ""

    def test_custom_values(self):
        """自定义参数正确设置。"""
        ts = 12345.0
        e = DisplayEvent(timestamp=ts, source="test")
        assert e.timestamp == ts
        assert e.source == "test"

    def test_frozen_immutable(self):
        """frozen dataclass 不可修改。"""
        e = DisplayEvent()
        with pytest.raises(FrozenInstanceError):
            e.timestamp = 999.0  # type: ignore[misc]

    def test_subclass_typed_as_display_event(self):
        """子类可作为 DisplayEvent 类型。"""
        e: DisplayEvent = SessionStarted(source="test")
        assert isinstance(e, DisplayEvent)


# ═══════════════════════════════════════════════════════════
# 生命周期
# ═══════════════════════════════════════════════════════════

class TestSessionStarted:
    """SessionStarted — 无额外字段。"""

    def test_create(self):
        e = SessionStarted(source="parallel")
        assert isinstance(e, SessionStarted)
        assert e.source == "parallel"

    def test_default_source(self):
        e = SessionStarted()
        assert e.source == ""


class TestSessionStopped:
    """SessionStopped — 含 final 字段。"""

    def test_create_permanent(self):
        e = SessionStopped(source="parallel", final=True)
        assert e.final is True

    def test_create_temporary(self):
        e = SessionStopped(source="parallel", final=False)
        assert e.final is False

    def test_default_final(self):
        e = SessionStopped()
        assert e.final is False


# ═══════════════════════════════════════════════════════════
# 工具调用
# ═══════════════════════════════════════════════════════════

class TestToolParsingEvent:
    """ToolParsingEvent — label/tool_name/arguments/tool_id。"""

    def test_create_with_all_fields(self):
        e = ToolParsingEvent(
            label="call_abc", tool_name="read_file",
            arguments='{"path": "main.py"}', tool_id="call_abc",
            source="agent-1",
        )
        assert e.label == "call_abc"
        assert e.tool_name == "read_file"
        assert e.arguments == '{"path": "main.py"}'
        assert e.tool_id == "call_abc"

    def test_default_empty_strings(self):
        e = ToolParsingEvent()
        assert e.label == ""
        assert e.tool_name == ""
        assert e.arguments == ""
        assert e.tool_id == ""


class TestToolStartedEvent:
    """ToolStartedEvent — label/tool_name/detail/metadata/tool_id。"""

    def test_create_with_all_fields(self):
        e = ToolStartedEvent(
            label="call_abc", tool_name="read_file",
            detail="Reading main.py",
            metadata={"path": "main.py"},
            tool_id="call_abc", source="agent-1",
        )
        assert e.label == "call_abc"
        assert e.tool_name == "read_file"
        assert e.detail == "Reading main.py"
        assert e.metadata == {"path": "main.py"}
        assert e.tool_id == "call_abc"

    def test_default_metadata_none(self):
        e = ToolStartedEvent()
        assert e.metadata is None

    def test_metadata_can_be_dict(self):
        e = ToolStartedEvent(metadata={"param": "value", "count": 42})
        assert e.metadata == {"param": "value", "count": 42}


class TestToolDoneEvent:
    """ToolDoneEvent — label/tool_name/success/metadata/tool_id。"""

    def test_create_success(self):
        e = ToolDoneEvent(
            label="call_abc", tool_name="read_file",
            success=True, tool_id="call_abc",
        )
        assert e.success is True

    def test_create_failure(self):
        e = ToolDoneEvent(
            label="call_abc", tool_name="search",
            success=False, tool_id="call_abc",
        )
        assert e.success is False

    def test_default_success_true(self):
        e = ToolDoneEvent()
        assert e.success is True


class TestToolOutputChunkEvent:
    """ToolOutputChunkEvent — label/text。"""

    def test_create(self):
        e = ToolOutputChunkEvent(label="call_abc", text="stdout line 1\n")
        assert e.label == "call_abc"
        assert e.text == "stdout line 1\n"

    def test_empty_text(self):
        e = ToolOutputChunkEvent(label="call_abc", text="")
        assert e.text == ""


class TestToolBatchStartedEvent:
    """ToolBatchStartedEvent — label/tool_names。"""

    def test_create_with_tool_names(self):
        e = ToolBatchStartedEvent(
            label="agent-1",
            tool_names=("read_file", "search", "write_file"),
        )
        assert e.label == "agent-1"
        assert e.tool_names == ("read_file", "search", "write_file")

    def test_default_empty_tuple(self):
        e = ToolBatchStartedEvent()
        assert e.tool_names == ()

    def test_tool_names_immutable(self):
        """tool_names 是 tuple（不可变）。"""
        e = ToolBatchStartedEvent(tool_names=("a", "b"))
        assert isinstance(e.tool_names, tuple)


# ═══════════════════════════════════════════════════════════
# Agent 状态
# ═══════════════════════════════════════════════════════════

class TestAgentAddedEvent:
    """AgentAddedEvent — label/description/status/dispatch_label。"""

    def test_create_with_all_fields(self):
        e = AgentAddedEvent(
            label="agent-1", description="Main Parser",
            status="running", dispatch_label="dispatch-1",
        )
        assert e.label == "agent-1"
        assert e.description == "Main Parser"
        assert e.status == "running"
        assert e.dispatch_label == "dispatch-1"

    def test_default_status_running(self):
        e = AgentAddedEvent(label="agent-1")
        assert e.status == "running"

    def test_empty_dispatch_label(self):
        e = AgentAddedEvent(label="agent-1")
        assert e.dispatch_label == ""


class TestAgentStatusChanged:
    """AgentStatusChanged — label/status。"""

    def test_create_running(self):
        e = AgentStatusChanged(label="agent-1", status="done")
        assert e.label == "agent-1"
        assert e.status == "done"

    def test_default_empty(self):
        e = AgentStatusChanged()
        assert e.label == ""
        assert e.status == ""


# ═══════════════════════════════════════════════════════════
# 模型阶段
# ═══════════════════════════════════════════════════════════

class TestModelPhaseEvent:
    """ModelPhaseEvent — label/phase/info。"""

    def test_create(self):
        e = ModelPhaseEvent(label="agent-1", phase="thinking", info="reasoning step 1")
        assert e.phase == "thinking"
        assert e.info == "reasoning step 1"

    def test_empty_phase(self):
        e = ModelPhaseEvent(label="agent-1", phase="")
        assert e.phase == ""


class TestPhaseDoneEvent:
    """PhaseDoneEvent — label/phase。"""

    def test_create(self):
        e = PhaseDoneEvent(label="agent-1", phase="reasoning")
        assert e.phase == "reasoning"


class TestUsageUpdatedEvent:
    """UsageUpdatedEvent — label/usage/replace。"""

    def test_create_with_usage(self):
        e = UsageUpdatedEvent(
            label="agent-1",
            usage={"input": 100, "output": 200, "speed": 15.5},
        )
        assert e.usage["input"] == 100
        assert e.usage["output"] == 200
        assert e.usage["speed"] == 15.5

    def test_default_replace_false(self):
        e = UsageUpdatedEvent(label="agent-1")
        assert e.replace is False

    def test_replace_true(self):
        e = UsageUpdatedEvent(label="agent-1", usage={"output": 500}, replace=True)
        assert e.replace is True
        assert e.usage["output"] == 500


# ═══════════════════════════════════════════════════════════
# 流式内容事件
# ═══════════════════════════════════════════════════════════

class TestContentChunkEvent:
    """ContentChunkEvent — text/label。"""

    def test_create(self):
        e = ContentChunkEvent(text="Hello", label="agent-1")
        assert e.text == "Hello"
        assert e.label == "agent-1"

    def test_empty_text(self):
        e = ContentChunkEvent(text="")
        assert e.text == ""


class TestReasoningChunkEvent:
    """ReasoningChunkEvent — text/label。"""

    def test_create(self):
        e = ReasoningChunkEvent(text="reasoning...", label="agent-1")
        assert e.text == "reasoning..."
        assert e.label == "agent-1"


# ═══════════════════════════════════════════════════════════
# 附加状态（并行显示）
# ═══════════════════════════════════════════════════════════

class TestParseInfoEvent:
    """ParseInfoEvent — label/tool_names/tokens/elapsed。"""

    def test_create(self):
        e = ParseInfoEvent(
            label="agent-1",
            tool_names="read_file, search",
            tokens=50, elapsed=0.35,
        )
        assert e.tool_names == "read_file, search"
        assert e.tokens == 50
        assert e.elapsed == 0.35

    def test_default_zero_values(self):
        e = ParseInfoEvent(label="agent-1")
        assert e.tokens == 0
        assert e.elapsed == 0.0


class TestParseInfoDoneEvent:
    """ParseInfoDoneEvent — label。"""

    def test_create(self):
        e = ParseInfoDoneEvent(label="agent-1")
        assert e.label == "agent-1"


class TestMetricsUpdateEvent:
    """MetricsUpdateEvent — label/output_tokens/live_output_tokens/live_input_tokens/speed。"""

    def test_create_full(self):
        e = MetricsUpdateEvent(
            label="agent-1",
            output_tokens=500,
            live_output_tokens=50,
            live_input_tokens=30,
            speed=15.0,
        )
        assert e.output_tokens == 500
        assert e.live_output_tokens == 50
        assert e.live_input_tokens == 30
        assert e.speed == 15.0

    def test_default_zero(self):
        e = MetricsUpdateEvent(label="agent-1")
        assert e.output_tokens == 0
        assert e.live_output_tokens == 0
        assert e.live_input_tokens == 0
        assert e.speed == 0.0


# ═══════════════════════════════════════════════════════════
# 通用输出
# ═══════════════════════════════════════════════════════════

class TestOutputEvent:
    """OutputEvent — text/level。"""

    def test_create_info(self):
        e = OutputEvent(text="Hello", level="info")
        assert e.text == "Hello"
        assert e.level == "info"

    def test_default_level_info(self):
        e = OutputEvent(text="test")
        assert e.level == "info"

    def test_all_levels(self):
        for level in ("info", "success", "warning", "error", "raw"):
            e = OutputEvent(text=level, level=level)
            assert e.level == level


class TestToolSummaryEvent:
    """ToolSummaryEvent — successful_tools/failed_tools。"""

    def test_success_only(self):
        e = ToolSummaryEvent(
            successful_tools=("read_file", "search"),
        )
        assert e.successful_tools == ("read_file", "search")
        assert e.failed_tools == ()

    def test_failed_only(self):
        e = ToolSummaryEvent(
            failed_tools=(("read_file", "File not found"),),
        )
        assert e.failed_tools == (("read_file", "File not found"),)
        assert e.successful_tools == ()

    def test_default_empty(self):
        e = ToolSummaryEvent()
        assert e.successful_tools == ()
        assert e.failed_tools == ()

    def test_immutable_tuples(self):
        """工具列表是 tuple，不可变。"""
        e = ToolSummaryEvent(successful_tools=("a", "b"))
        assert isinstance(e.successful_tools, tuple)


class TestUserSelectNeededEvent:
    """UserSelectNeededEvent — select_id/title/options/multi_select/default_options/timeout。"""

    def test_create_with_all_fields(self):
        e = UserSelectNeededEvent(
            select_id="sel-1",
            title="Choose file",
            options=("a.py", "b.py", "c.py"),
            multi_select=True,
            default_options=("a.py",),
            timeout=60,
        )
        assert e.select_id == "sel-1"
        assert e.title == "Choose file"
        assert e.options == ("a.py", "b.py", "c.py")
        assert e.multi_select is True
        assert e.default_options == ("a.py",)
        assert e.timeout == 60

    def test_default_values(self):
        e = UserSelectNeededEvent(select_id="sel-2")
        assert e.title == ""
        assert e.options == ()
        assert e.multi_select is False
        assert e.default_options == ()
        assert e.timeout == 120

    def test_options_immutable(self):
        e = UserSelectNeededEvent(options=("x", "y"))
        assert isinstance(e.options, tuple)


class TestAgentResultEvent:
    """AgentResultEvent — label/description/result/error。"""

    def test_success_result(self):
        e = AgentResultEvent(
            label="agent-1",
            description="Parse config",
            result="Config parsed successfully",
        )
        assert e.result == "Config parsed successfully"
        assert e.error == ""

    def test_error_result(self):
        e = AgentResultEvent(
            label="agent-1",
            description="Parse config",
            error="Syntax error at line 10",
        )
        assert e.result == ""
        assert e.error == "Syntax error at line 10"

    def test_default_empty(self):
        e = AgentResultEvent(label="agent-1")
        assert e.result == ""
        assert e.error == ""


# ═══════════════════════════════════════════════════════════
# 边界值测试
# ═══════════════════════════════════════════════════════════

class TestEventBoundaryValues:
    """事件类型边界值测试。"""

    def test_empty_label(self):
        """空字符串 label 可创建。"""
        e = ToolStartedEvent(label="")
        assert e.label == ""

    def test_empty_tool_names_tuple(self):
        """空 tuple tool_names 可创建。"""
        e = ToolBatchStartedEvent(tool_names=())
        assert e.tool_names == ()

    def test_none_metadata(self):
        """None metadata 可创建。"""
        e = ToolStartedEvent(metadata=None)
        assert e.metadata is None

    def test_large_positive_values(self):
        """超大正数值不溢出。"""
        e = ParseInfoEvent(tokens=2**31 - 1, elapsed=1e10)
        assert e.tokens == 2147483647
        assert e.elapsed == 1e10

    def test_negative_values(self):
        """负数值（某些字段允许）。"""
        e = MetricsUpdateEvent(output_tokens=-1, speed=-5.0)
        assert e.output_tokens == -1
        assert e.speed == -5.0

    def test_long_label(self):
        """超长 label 可创建。"""
        long_label = "x" * 10000
        e = AgentStatusChanged(label=long_label, status="done")
        assert e.label == long_label

    def test_special_chars_in_text(self):
        """特殊字符在 text 字段中。"""
        e = ContentChunkEvent(text="Hello\nWorld\tTab\r\n")
        assert "\n" in e.text
        assert "\t" in e.text


# ═══════════════════════════════════════════════════════════
# ALL_EVENT_TYPES 完整性
# ═══════════════════════════════════════════════════════════

class TestAllEventTypes:
    """验证 ALL_EVENT_TYPES 注册表包含所有事件类型。"""

    # 所有事件类型（不含 DisplayEvent 基类）
    EXPECTED_TYPES = {
        SessionStarted, SessionStopped,
        ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
        ToolOutputChunkEvent, ToolBatchStartedEvent,
        AgentAddedEvent, AgentStatusChanged,
        ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
        ContentChunkEvent, ReasoningChunkEvent,
        ParseInfoEvent, ParseInfoDoneEvent, MetricsUpdateEvent,
        OutputEvent, ToolSummaryEvent,
        UserSelectNeededEvent, AgentResultEvent,
    }

    def test_all_types_registered(self):
        """ALL_EVENT_TYPES 应包含全部 21 种事件类型。"""
        registered = set(ALL_EVENT_TYPES)
        assert registered == self.EXPECTED_TYPES, (
            f"ALL_EVENT_TYPES 缺少: {self.EXPECTED_TYPES - registered}, "
            f"多出: {registered - self.EXPECTED_TYPES}"
        )

    def test_all_types_count(self):
        """确切 21 种事件类型。"""
        assert len(ALL_EVENT_TYPES) == 21, (
            f"期望 21 种事件类型，实际 {len(ALL_EVENT_TYPES)}"
        )

    def test_display_event_not_in_all(self):
        """DisplayEvent 基类不在 ALL_EVENT_TYPES 中。"""
        assert DisplayEvent not in ALL_EVENT_TYPES

    def test_all_subclass_of_display_event(self):
        """ALL_EVENT_TYPES 中所有类型都是 DisplayEvent 子类。"""
        for et in ALL_EVENT_TYPES:
            assert issubclass(et, DisplayEvent), f"{et.__name__} 不是 DisplayEvent 子类"


# ═══════════════════════════════════════════════════════════
# Frozen 不可变性
# ═══════════════════════════════════════════════════════════

class TestFrozenImmutability:
    """所有事件类型的 frozen 不可变性验证。"""

    @pytest.mark.parametrize("event_cls, kwargs", [
        (SessionStarted, {}),
        (SessionStopped, {"final": True}),
        (ToolParsingEvent, {"label": "x"}),
        (ToolStartedEvent, {"label": "x", "tool_name": "read_file"}),
        (ToolDoneEvent, {"label": "x", "tool_name": "read_file"}),
        (ToolOutputChunkEvent, {"label": "x", "text": "data"}),
        (ToolBatchStartedEvent, {"label": "x", "tool_names": ("a",)}),
        (AgentAddedEvent, {"label": "x"}),
        (AgentStatusChanged, {"label": "x", "status": "done"}),
        (ModelPhaseEvent, {"label": "x", "phase": "thinking"}),
        (PhaseDoneEvent, {"label": "x", "phase": "reasoning"}),
        (UsageUpdatedEvent, {"label": "x", "usage": {"o": 1}}),
        (ContentChunkEvent, {"text": "hello"}),
        (ReasoningChunkEvent, {"text": "thinking"}),
        (ParseInfoEvent, {"label": "x"}),
        (ParseInfoDoneEvent, {"label": "x"}),
        (MetricsUpdateEvent, {"label": "x"}),
        (OutputEvent, {"text": "hello"}),
        (ToolSummaryEvent, {"successful_tools": ("a",)}),
        (UserSelectNeededEvent, {"select_id": "s1"}),
        (AgentResultEvent, {"label": "x", "description": "test"}),
    ])
    def test_frozen_immutable(self, event_cls, kwargs):
        """所有事件类型在创建后不可修改。"""
        e = event_cls(**kwargs)
        # 尝试修改第一个字段
        first_field = list(e.__dataclass_fields__.keys())[0]
        with pytest.raises(FrozenInstanceError):
            setattr(e, first_field, "modified")

    @pytest.mark.parametrize("event_cls, kwargs, field_name, expected_type", [
        (SessionStopped, {"final": True}, "final", bool),
        (ToolParsingEvent, {"label": "x"}, "label", str),
        (ToolStartedEvent, {"label": "x"}, "tool_id", str),
        (ToolDoneEvent, {"label": "x"}, "success", bool),
        (ToolOutputChunkEvent, {"label": "x", "text": ""}, "text", str),
        (ToolBatchStartedEvent, {"label": "x"}, "tool_names", tuple),
        (AgentAddedEvent, {"label": "x"}, "status", str),
        (ModelPhaseEvent, {"label": "x"}, "phase", str),
        (ParseInfoEvent, {"label": "x"}, "tokens", int),
        (ParseInfoEvent, {"label": "x"}, "elapsed", float),
        (MetricsUpdateEvent, {"label": "x"}, "speed", float),
        (OutputEvent, {"text": "hello"}, "level", str),
        (ToolSummaryEvent, {}, "successful_tools", tuple),
        (UserSelectNeededEvent, {"select_id": "s1"}, "timeout", int),
        (AgentResultEvent, {"label": "x"}, "result", str),
    ])
    def test_field_types(self, event_cls, kwargs, field_name, expected_type):
        """验证字段类型正确。"""
        e = event_cls(**kwargs)
        value = getattr(e, field_name)
        assert isinstance(value, expected_type), (
            f"{event_cls.__name__}.{field_name} 期望 {expected_type.__name__}，"
            f"实际 {type(value).__name__}"
        )
