"""事件发布统一门面（emit）测试 — 方向 B（2026-08-16 架构改进）。

覆盖：
  1. ``emit(event)`` 发布到默认总线（订阅者接收，行为与旧
     ``DisplayEventBus.get_default().publish(...)`` 一致）；
  2. ``emit(event, bus=...)`` 支持注入独立总线（测试/多实例场景，为方向 D
     单例解耦铺路）；
  3. ``src.api.events.publish_event``（字符串类型名安全发布）委托 emit 后
     行为不变；
  4. ``events.consumers.publish_output / publish_tool_summary`` 经 emit
     发布（公开便捷函数语义不变）；
  5. 散点收敛守卫：tui 事件发布路径不应再出现非门面直发（静态检查
     src/tools、src/core、src/api 无 ``get_default().publish`` 调用）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tui.events import DisplayEventBus, emit, default_bus
from src.tui.events.event_types import OutputEvent, ToolOutputChunkEvent


@pytest.fixture(autouse=True)
def _isolate_event_bus():
    """每个测试隔离 DisplayEventBus 单例（重置订阅/实例）。"""
    DisplayEventBus.reset_default()
    yield
    DisplayEventBus.reset_default()


# ═══════════════════════════════════════════════════════════
# 1. emit 发布到默认总线
# ═══════════════════════════════════════════════════════════

def test_emit_publishes_to_default_bus():
    """emit(event) 发布到默认单例总线，订阅者按类型接收。"""
    bus = default_bus()
    received: list = []

    def _handler(event):
        received.append(event)

    bus.subscribe(_handler, event_type=OutputEvent)
    emit(OutputEvent(text="hello", level="info", source="test"))
    assert len(received) == 1
    assert received[0].text == "hello"
    assert received[0].source == "test"


def test_emit_default_bus_is_singleton():
    """default_bus() 与 DisplayEventBus.get_default() 同一实例。"""
    assert default_bus() is DisplayEventBus.get_default()


# ═══════════════════════════════════════════════════════════
# 2. emit 支持注入独立总线
# ═══════════════════════════════════════════════════════════

def test_emit_accepts_explicit_bus():
    """emit(event, bus=...) 发布到显式注入的总线（不污染默认总线）。

    ★ 架构改进方向 D（2026-08-16）：``DisplayEventBus()`` 直接构造创建
    独立实例（不再拦截返回单例）——显式注入语义无需 hack 即可验证。
    """
    isolated = DisplayEventBus()  # 独立实例（与默认单例隔离）
    assert isolated is not default_bus()
    received_default: list = []
    received_isolated: list = []
    default_bus().subscribe(
        lambda e: received_default.append(e), event_type=OutputEvent,
    )
    isolated.subscribe(
        lambda e: received_isolated.append(e), event_type=OutputEvent,
    )
    emit(OutputEvent(text="only-isolated"), bus=isolated)
    assert len(received_isolated) == 1
    assert received_isolated[0].text == "only-isolated"
    assert received_default == []  # 默认总线未收到


# ═══════════════════════════════════════════════════════════
# 3. api.events.publish_event 委托 emit 后行为不变
# ═══════════════════════════════════════════════════════════

def test_api_publish_event_still_works():
    """api 层字符串类型名安全发布经 emit 委托，行为不变。"""
    from src.api.events import publish_event
    received: list = []

    def _handler(event):
        received.append(event)

    default_bus().subscribe(_handler, event_type=ToolOutputChunkEvent)
    ok = publish_event(
        "ToolOutputChunkEvent", label="tool-1", tool_id="tool-1", text="x",
        source="agent",
    )
    assert ok is True
    assert len(received) == 1
    assert received[0].tool_id == "tool-1"
    assert received[0].text == "x"


def test_api_publish_event_unknown_type_returns_false():
    """api 层未知事件类型名返回 False（安全降级语义保持）。"""
    from src.api.events import publish_event
    assert publish_event("NonExistentEvent123") is False


# ═══════════════════════════════════════════════════════════
# 4. consumers 便捷函数经 emit 发布
# ═══════════════════════════════════════════════════════════

def test_publish_output_via_emit():
    """events.consumers.publish_output 经 emit 发布（订阅者接收）。"""
    from src.tui.events.consumers import publish_output
    received: list = []

    def _handler(event):
        received.append(event)

    default_bus().subscribe(_handler, event_type=OutputEvent)
    publish_output("msg", level="warning", source="s")
    assert len(received) == 1
    assert received[0].text == "msg"
    assert received[0].level == "warning"


# ═══════════════════════════════════════════════════════════
# 5. 散点收敛静态守卫
# ═══════════════════════════════════════════════════════════

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def test_no_scattered_get_default_publish_in_outer_layers():
    """守卫：tools/core/api 层不得再出现 get_default().publish 散点发布。

    发布路径已统一收敛到 ``tui.events.publish.emit``——外部层若再次引入
    直接 ``DisplayEventBus.get_default().publish(...)`` 即违规（回归检测）。
    订阅/总线获取（``get_default()`` 单用）不受限。
    """
    targets = [
        "src/tools",
        "src/core",
        "src/api",
        "src/webui",
        "src/tui/events/consumers.py",
    ]
    offenders: list[str] = []
    for rel in targets:
        p = _SRC_ROOT / rel
        files = [p] if p.is_file() else list(p.rglob("*.py"))
        for f in files:
            if "__pycache__" in str(f):
                continue
            for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "get_default().publish" in stripped or "get_default().publish" in line:
                    offenders.append(f"{f.relative_to(_SRC_ROOT)}:{lineno}")
    assert not offenders, (
        f"发现散点 get_default().publish 调用（应统一经 tui.events.publish.emit）: "
        f"{offenders}"
    )
