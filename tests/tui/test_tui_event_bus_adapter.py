"""DisplayEventAdapter / EventBusDisplayProxy 单元测试。

回归重点：``DisplayEventAdapter.subscribe_to`` 中创建 handler 并调用
``event_bus.subscribe`` 的代码曾被误缩进到 ``continue`` 之后（永不可达）——
适配器从不真正订阅任何 handler（``_handlers`` 恒空、总线 subscriber_count
恒 0），整条「事件 → BaseDisplay」适配路径静默失效。本文件固化「确实订阅」
「事件按签名转发到 display」「继承的 BaseDisplay no-op 不订阅」等行为。
"""

from __future__ import annotations

import pytest

from src.core.events.display_bus import DisplayEventBus
from src.core.events.display_types import (
    AgentAddedEvent,
    AgentStatusChanged,
    ModelPhaseEvent,
    OutputEvent,
    ParseInfoDoneEvent,
    ParseInfoEvent,
    SessionStarted,
    SessionStopped,
    ToolBatchStartedEvent,
    ToolDoneEvent,
    ToolParsingEvent,
    ToolStartedEvent,
    ToolSummaryEvent,
    UsageUpdatedEvent,
)
from src.tui._base_display import BaseDisplay
from src.tui.events.adapters import (
    DisplayEventAdapter,
    EventBusDisplayProxy,
    wire_event_bus,
)


class _RecordingDisplay(BaseDisplay):
    """覆盖全部可选方法的显示实现，记录调用参数。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    # BaseDisplay 抽象方法
    def update_status(self, label: str, status: str) -> None:
        self.calls.append(("update_status", label, status))

    # 可选覆盖（非 BaseDisplay 定义 → 视为真实实现）
    def start(self) -> None:
        self.calls.append(("start",))

    def stop(self, final: bool = False) -> None:
        self.calls.append(("stop", final))

    def tool_start(self, label, tool_name, detail="", metadata=None, tool_id=""):
        self.calls.append(("tool_start", label, tool_name, detail, metadata, tool_id))

    def tool_done(self, label, tool_name="", success=True, metadata=None, tool_id=""):
        self.calls.append(("tool_done", label, tool_name, success, metadata, tool_id))

    def tool_parsing(self, label, tool_name, arguments="", tool_id=""):
        self.calls.append(("tool_parsing", label, tool_name, arguments, tool_id))

    def tool_batch_start(self, label, names):
        self.calls.append(("tool_batch_start", label, tuple(names)))

    def add_agent(self, label, description, status="running"):
        self.calls.append(("add_agent", label, description, status))

    def update_agent_status(self, label, status):
        self.calls.append(("update_agent_status", label, status))

    def update_model_phase(self, label, phase, info=""):
        self.calls.append(("update_model_phase", label, phase, info))

    def update_usage(self, label, usage, replace=False):
        self.calls.append(("update_usage", label, usage, replace))

    def update_parse_info(self, label, tool_name, tokens, elapsed):
        self.calls.append(("update_parse_info", label, tool_name, tokens, elapsed))

    def parse_info_done(self, label):
        self.calls.append(("parse_info_done", label))

    def update_tokens(self, label, tokens):
        self.calls.append(("update_tokens", label, tokens))

    def publish_output(self, text, level="info"):
        self.calls.append(("publish_output", text, level))

    def publish_tool_summary(self, successful_tools, failed_tools):
        self.calls.append(
            ("publish_tool_summary", tuple(successful_tools), tuple(failed_tools))
        )


class _MinimalDisplay(BaseDisplay):
    """只实现抽象方法（其余可选方法继承 BaseDisplay 的具体 no-op）。"""

    def update_status(self, label: str, status: str) -> None:
        pass


@pytest.fixture()
def bus() -> DisplayEventBus:
    instance = DisplayEventBus()
    instance.clear()
    yield instance
    instance.clear()


# ═══════════════════════════════════════════════════════════
# 订阅（回归：不可达的订阅代码）
# ═══════════════════════════════════════════════════════════


def test_subscribe_to_actually_registers_handlers(bus):
    """subscribe_to 必须把 handler 真正注册到总线（而非空转）。"""
    display = _RecordingDisplay()
    adapter = DisplayEventAdapter(display)

    adapter.subscribe_to(bus)

    assert adapter._handlers, "适配器未注册任何 handler（订阅代码不可达回归）"
    assert ToolStartedEvent in adapter._handlers
    assert bus.subscriber_count == len(adapter._handlers)


def test_subscribe_to_is_idempotent(bus):
    """重复 subscribe_to 不产生重复订阅（先退订再订阅）。"""
    display = _RecordingDisplay()
    adapter = DisplayEventAdapter(display)

    adapter.subscribe_to(bus)
    first = len(adapter._handlers)
    adapter.subscribe_to(bus)

    assert len(adapter._handlers) == first
    assert bus.subscriber_count == first


def test_include_types_limits_subscription(bus):
    """include_types 限定订阅范围。"""
    adapter = DisplayEventAdapter(_RecordingDisplay(), include_types={ToolStartedEvent})

    adapter.subscribe_to(bus)

    assert set(adapter._handlers) == {ToolStartedEvent}


def test_inherited_base_noop_not_subscribed(bus):
    """继承自 BaseDisplay 的具体 no-op 方法不被订阅。"""
    adapter = DisplayEventAdapter(_MinimalDisplay())

    adapter.subscribe_to(bus)

    assert AgentStatusChanged not in adapter._handlers
    assert AgentAddedEvent not in adapter._handlers
    assert ParseInfoEvent not in adapter._handlers
    assert ParseInfoDoneEvent not in adapter._handlers
    assert adapter._handlers == {}


def test_unsubscribe_all_stops_delivery(bus):
    """unsubscribe_all 后事件不再转发到 display。"""
    display = _RecordingDisplay()
    adapter = DisplayEventAdapter(display)
    adapter.subscribe_to(bus)

    adapter.unsubscribe_all()
    bus.publish(ToolStartedEvent(label="l", tool_name="bash"))

    assert display.calls == []
    assert bus.subscriber_count == 0


# ═══════════════════════════════════════════════════════════
# 事件 → display 方法转发
# ═══════════════════════════════════════════════════════════


def test_tool_started_event_forwards_all_fields(bus):
    display = _RecordingDisplay()
    adapter = DisplayEventAdapter(display)
    adapter.subscribe_to(bus)

    bus.publish(
        ToolStartedEvent(
            label="t1", tool_name="bash", detail="ls -la",
            metadata={"参数": "3t"}, tool_id="call-1",
        )
    )

    assert display.calls == [
        ("tool_start", "t1", "bash", "ls -la", {"参数": "3t"}, "call-1")
    ]


def test_tool_done_event_forwards_success_and_metadata(bus):
    display = _RecordingDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(
        ToolDoneEvent(
            label="t1", tool_name="bash", success=False,
            metadata={"输出": "1t"}, tool_id="call-1",
        )
    )

    assert display.calls == [("tool_done", "t1", "bash", False, {"输出": "1t"}, "call-1")]


def test_tool_parsing_event_forwards_tool_id(bus):
    display = _RecordingDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(
        ToolParsingEvent(label="t1", tool_name="bash", arguments="{}", tool_id="call-9")
    )

    assert display.calls == [("tool_parsing", "t1", "bash", "{}", "call-9")]


def test_batch_agent_and_phase_events(bus):
    display = _RecordingDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(ToolBatchStartedEvent(label="m", tool_names=("a", "b")))
    bus.publish(AgentAddedEvent(label="s1", description="desc", status="running"))
    bus.publish(ModelPhaseEvent(label="m", phase="thinking", info="…"))
    bus.publish(ParseInfoEvent(label="m", tool_names="bash", tokens=12, elapsed=0.5))
    bus.publish(ParseInfoDoneEvent(label="m"))

    assert display.calls == [
        ("tool_batch_start", "m", ("a", "b")),
        ("add_agent", "s1", "desc", "running"),
        ("update_model_phase", "m", "thinking", "…"),
        ("update_parse_info", "m", "bash", 12, 0.5),
        ("parse_info_done", "m"),
    ]


def test_usage_and_output_events(bus):
    display = _RecordingDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(UsageUpdatedEvent(label="m", usage={"input": 1}, replace=True))
    bus.publish(OutputEvent(text="hello", level="info"))
    bus.publish(ToolSummaryEvent(successful_tools=("a",), failed_tools=(("b", "err"),)))

    assert display.calls == [
        ("update_usage", "m", {"input": 1}, True),
        ("publish_output", "hello", "info"),
        ("publish_tool_summary", ("a",), (("b", "err"),)),
    ]


def test_session_and_agent_status_events(bus):
    display = _RecordingDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(SessionStarted())
    bus.publish(AgentStatusChanged(label="s1", status="done"))
    bus.publish(SessionStopped(final=True))

    assert display.calls == [
        ("start",),
        ("update_agent_status", "s1", "done"),
        ("stop", True),
    ]


def test_handler_exception_does_not_break_other_handlers(bus):
    display = _RecordingDisplay()

    def _boom(event):
        raise RuntimeError("boom")

    adapter = DisplayEventAdapter(display)
    adapter.subscribe_to(bus)
    bus.subscribe(_boom, event_type=ToolStartedEvent)

    bus.publish(ToolStartedEvent(label="t", tool_name="bash"))

    assert display.calls == [("tool_start", "t", "bash", "", None, "")]


# ═══════════════════════════════════════════════════════════
# EventBusDisplayProxy（BaseDisplay 接口 → 事件）
# ═══════════════════════════════════════════════════════════


def test_proxy_publishes_tool_events(bus):
    received: list = []
    bus.subscribe(received.append, event_type=ToolStartedEvent)

    proxy = EventBusDisplayProxy(bus, source="agent")
    proxy.tool_start("t1", "bash", detail="ls", metadata={"k": "v"}, tool_id="call-1")

    assert len(received) == 1
    event = received[0]
    assert event.label == "t1"
    assert event.tool_name == "bash"
    assert event.detail == "ls"
    assert event.metadata == {"k": "v"}
    assert event.tool_id == "call-1"
    assert event.source == "agent"


def test_proxy_tool_done_and_lifecycle(bus):
    received: list = []
    bus.subscribe(received.append)

    proxy = EventBusDisplayProxy(bus, source="agent")
    proxy.start()
    proxy.tool_done("t1", "bash", success=False, tool_id="call-1")
    proxy.stop(final=True)

    kinds = [type(e) for e in received]
    assert kinds == [SessionStarted, ToolDoneEvent, SessionStopped]
    assert received[1].success is False
    assert received[1].tool_id == "call-1"
    assert received[2].final is True


def test_proxy_stores_results_by_label(bus):
    proxy = EventBusDisplayProxy(bus)

    assert proxy.get_result("s1") == {"result_text": "", "error": ""}
    proxy.set_result("s1", result_text="ok", error="")
    assert proxy.get_result("s1") == {"result_text": "ok", "error": ""}


def test_proxy_capture_and_print_calls_function_directly(bus):
    proxy = EventBusDisplayProxy(bus)

    assert proxy.capture_and_print(lambda: "captured") == "captured"
    assert proxy.capture_and_print(None) == ""


@pytest.mark.asyncio
async def test_proxy_await_stop_publishes_final_stop(bus):
    received: list = []
    bus.subscribe(received.append, event_type=SessionStopped)

    await EventBusDisplayProxy(bus).await_stop()

    assert [e.final for e in received] == [True]


# ═══════════════════════════════════════════════════════════
# wire_event_bus 端到端
# ═══════════════════════════════════════════════════════════


def test_wire_event_bus_connects_proxy_and_display(bus):
    display = _RecordingDisplay()

    proxy = wire_event_bus(display, bus, source="agent")
    proxy.tool_start("t1", "bash", detail="ls", tool_id="call-1")
    proxy.update_status("s1", "running")

    assert display.calls == [
        ("tool_start", "t1", "bash", "ls", None, "call-1"),
        # update_status 发布 AgentStatusChanged → 适配器映射到 update_agent_status
        ("update_agent_status", "s1", "running"),
    ]


# ═══════════════════════════════════════════════════════════
# 旧显示实现（未声明 tool_id 形参）兼容
# ═══════════════════════════════════════════════════════════


class _LegacyDisplay(_MinimalDisplay):
    """未声明 ``tool_id`` 形参的显示实现（模拟旧版本/第三方 display）。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def tool_start(self, label, tool_name, detail, metadata=None):
        self.calls.append(("tool_start", label, tool_name, detail, metadata))

    def tool_done(self, label, tool_name="", success=True, metadata=None):
        self.calls.append(("tool_done", label, tool_name, success, metadata))

    def tool_parsing(self, label, tool_name, arguments=""):
        self.calls.append(("tool_parsing", label, tool_name, arguments))


def test_legacy_display_without_tool_id_still_receives_events(bus):
    """display 未声明 tool_id 形参时按签名判定退回调用（不抛 TypeError）。"""
    display = _LegacyDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(ToolParsingEvent(label="l", tool_name="bash", arguments="{}", tool_id="c1"))
    bus.publish(ToolStartedEvent(label="l", tool_name="bash", detail="d", tool_id="c1"))
    bus.publish(ToolDoneEvent(label="l", tool_name="bash", success=True, tool_id="c1"))

    assert display.calls == [
        ("tool_parsing", "l", "bash", "{}"),
        ("tool_start", "l", "bash", "d", None),
        ("tool_done", "l", "bash", True, None),
    ]


def test_kwargs_display_receives_tool_id(bus):
    """显示实现用 ``**kwargs`` 兜底时同样收到 tool_id。"""
    received: list[tuple] = []

    class _KwargsDisplay(_MinimalDisplay):
        def tool_start(self, *args, **kwargs):
            received.append((args, kwargs))

    adapter = DisplayEventAdapter(_KwargsDisplay())
    adapter.subscribe_to(bus)
    bus.publish(ToolStartedEvent(label="l", tool_name="bash", detail="d", tool_id="c1"))

    assert received == [(("l", "bash", "d", None, "c1"), {})]


# ═══════════════════════════════════════════════════════════
# 统一指标事件（MetricsUpdateEvent）按字段分发
# ═══════════════════════════════════════════════════════════


class _MetricsDisplay(_MinimalDisplay):
    """实现全部指标方法的显示（记录每次调用的字段值）。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def update_tokens(self, label, tokens):
        self.calls.append(("update_tokens", label, tokens))

    def update_live_output(self, label, tokens):
        self.calls.append(("update_live_output", label, tokens))

    def update_live_input(self, label, tokens):
        self.calls.append(("update_live_input", label, tokens))

    def update_speed(self, label, speed):
        self.calls.append(("update_speed", label, speed))


def test_metrics_event_forwards_only_non_zero_fields(bus):
    """MetricsUpdateEvent 只转发非默认字段——修复前 live 事件会调用
    update_tokens(label, 0)，把已累计的输出 token 计数清 0。"""
    from src.core.events.display_types import MetricsUpdateEvent

    display = _MetricsDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(MetricsUpdateEvent(
        label="a", live_output_tokens=7, source="t",
    ))

    assert display.calls == [("update_live_output", "a", 7)]


def test_metrics_event_forwards_output_tokens(bus):
    from src.core.events.display_types import MetricsUpdateEvent

    display = _MetricsDisplay()
    DisplayEventAdapter(display).subscribe_to(bus)

    bus.publish(MetricsUpdateEvent(label="a", output_tokens=42, source="t"))

    assert display.calls == [("update_tokens", "a", 42)]


def test_metrics_event_subscribes_when_only_speed_implemented(bus):
    """只实现 update_speed 的显示也必须收到指标事件（修复前映射固定为
    update_tokens → 未实现即不订阅，speed 事件全部丢弃）。"""
    from src.core.events.display_types import MetricsUpdateEvent

    class _SpeedOnly(_MinimalDisplay):
        def __init__(self) -> None:
            super().__init__()
            self.speeds: list[tuple] = []

        def update_speed(self, label, speed):
            self.speeds.append((label, speed))

    display = _SpeedOnly()
    adapter = DisplayEventAdapter(display)
    adapter.subscribe_to(bus)
    assert MetricsUpdateEvent in adapter._handlers

    bus.publish(MetricsUpdateEvent(label="a", speed=12.5, source="t"))

    assert display.speeds == [("a", 12.5)]
