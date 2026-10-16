"""架构重构回归测试 — 下沉/端口化后的导入契约与 re-export 同一性。

固化 2026-10 架构改进：显示事件基础设施与通用工具下沉核心层、私有模块
转正、工具注册表端口化、轨迹视图大文件拆分后的公共导入路径与对象同一性，
防止后续重构破坏兼容 re-export 或引入重复实现。
"""

from __future__ import annotations

import threading


# ── 1. 显示事件基础设施下沉核心层 ─────────────────────────

def test_display_event_bus_single_class_and_singleton():
    from src.core.events.display_bus import DisplayEventBus as Core
    from src.tui.events.event_bus import DisplayEventBus as Tui
    assert Core is Tui
    assert Core.get_default() is Tui.get_default()


def test_publish_emit_identity():
    from src.core.events.publish import emit as core_emit
    from src.tui.events.publish import emit as tui_emit
    assert core_emit is tui_emit


def test_publish_output_identity():
    from src.core.events.publish import publish_output as core_pub
    from src.tui.events.consumers import publish_output as tui_pub
    assert core_pub is tui_pub


def test_output_event_roundtrip_via_core():
    from src.core.events.display_bus import DisplayEventBus
    from src.core.events.display_types import OutputEvent
    from src.core.events.publish import emit

    bus = DisplayEventBus()
    seen = []
    bus.subscribe(seen.append, event_type=OutputEvent)
    emit(OutputEvent(text="hello", level="info"), bus=bus)
    assert [e.text for e in seen] == ["hello"]


def test_singleton_meta_identity():
    from src.core.singleton import SingletonMeta as Core
    from src.tui.core.singleton import SingletonMeta as Tui
    assert Core is Tui


# ── 2. 通用工具下沉核心层 ─────────────────────────────────

def test_interrupt_state_identity():
    from src.api.interrupt_async import is_kill_background_requested as a
    from src.core.interrupt_state import is_kill_background_requested as b
    assert a is b


def test_tokens_identity():
    from src.api.tokens import estimate_tokens as a
    from src.core.tokens import estimate_tokens as b
    assert a is b


def test_multimodal_identity():
    from src.api.multimodal import is_multimodal_model as a
    from src.core.multimodal import is_multimodal_model as b
    assert a is b


def test_stats_identity():
    from src.api.stats import get_token_stats as a
    from src.core.stats import get_token_stats as b
    assert a is b


def test_tool_display_identity():
    from src.core.tool_display import TOOL_DISPLAY_NAME as a, get_tool_display_name as fa
    from src.tools._constants import TOOL_DISPLAY_NAME as b
    from src.tools.registry import get_tool_display_name as fb
    assert a is b and fa is fb


# ── 3. 私有模块转正 ──────────────────────────────────────

def test_renderer_locks_promoted():
    from src.renderer._locks import render_lock as a
    from src.renderer.locks import render_lock as b
    assert a is b


def test_diff_active_single_source():
    from src.core.diff_state import diff_active as a
    from src.renderer.locks import diff_active as b
    from src.renderer._locks import diff_active as c
    assert a is b is c
    assert isinstance(a, threading.Event)


def test_tool_policy_promoted_and_shared_map():
    from src.core.subagent import _TOOL_EXCLUSION_MAP as a
    from src.tools._tool_policy import _TOOL_EXCLUSION_MAP as b
    from src.tools.tool_policy import TOOL_EXCLUSION_MAP as c
    assert a is b is c


def test_escape_monitor_history_promoted():
    from src.api.escape_monitor._history import _HISTORY_MAX_ENTRIES as a
    from src.api.escape_monitor.history import _HISTORY_MAX_ENTRIES as b
    assert a == b


# ── 4. 工具注册表端口化 ──────────────────────────────────

def test_tool_result_port_shared():
    from src.core.ports.tools import ToolResult as A
    from src.tools.base import ToolResult as B
    assert A is B
    r = A(text="t", blocks=[{"type": "text", "text": "t"}])
    assert r.to_content() == [{"type": "text", "text": "t"}]
    assert A(text="x").to_content() == "x"


def test_tool_registry_implements_port():
    from src.core.ports.tools import ToolRegistryPort
    from src.tools.registry import ToolRegistry
    assert isinstance(ToolRegistry(), ToolRegistryPort)


def test_default_tool_registry_factory():
    from src.core.adapters.tools import get_default_tool_registry
    from src.tools.registry import ToolRegistry
    assert get_default_tool_registry() is ToolRegistry.default()


# ── 5. 轨迹视图拆分（trace_view → trace_types/trace_tree/trace_ledger/styles） ──

def test_trace_record_identity():
    from src.tui.app.trace import TraceRecord as A
    from src.tui.app.trace_types import TraceRecord as B
    assert A is B


def test_trace_tree_reexports():
    from src.tui.app.trace_tree import _value_to_tree as a
    from src.tui.app.trace_view import _value_to_tree as b
    assert a is b
    tree = a({"a": 1})
    assert tree == [{"label": "a: 1", "children": []}]


def test_trace_ledger_reexports():
    from src.tui.app.trace_ledger import _ledger_row_runs as a
    from src.tui.app.trace_view import _ledger_row_runs as b
    assert a is b


def test_trace_styles_shared_object():
    from src.tui.app.trace_styles import _S_TEXT as a
    from src.tui.app.trace_view import _S_TEXT as b
    assert a is b
