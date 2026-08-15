"""DisplayEventBus 事件 handler 异常日志降级限频测试（L3）。

修复背景（2026-08-15 L3）：``DisplayEventBus.publish`` 对每个异常事件
``_logger.exception``（ERROR + 完整栈）——高频事件（ContentChunkEvent /
ReasoningChunkEvent）handler 持续异常时刷屏污染终端。修复：降级为
warning + 按事件类型 5s 窗口限频（窗口内只记一次 warning 含栈，其余记
debug）；不同事件类型独立限频；handler 异常隔离不变（不影响其他 handler）。

本测试锁定：同类型限频、异类型独立限频、正常路径无日志、异常隔离回归。
"""

from __future__ import annotations

import logging

from src.tui.events import event_bus as eb
from src.tui.events.event_bus import DisplayEventBus
from src.tui.events.event_types import (
    ContentChunkEvent,
    OutputEvent,
    ReasoningChunkEvent,
)


def _boom(ev):
    """抛异常 handler（模拟高频事件 handler 持续异常）。"""
    raise RuntimeError("boom")


def _reset_bus() -> DisplayEventBus:
    """重置单例 + 清空限频字典（测试隔离）。"""
    eb._last_exc_log.clear()
    DisplayEventBus.reset_default()
    bus = DisplayEventBus.get_default()
    bus.clear()
    return bus


# ── L3：同类型限频 ────────────────────────────────────────

def test_publish_exc_rate_limited_warning_regression(caplog):
    """L3：同事件类型 5s 窗口内多次异常只打 1 条 warning（其余 debug）。"""
    bus = _reset_bus()
    bus.subscribe(_boom, ContentChunkEvent)
    with caplog.at_level(logging.DEBUG, logger="src.tui.events.event_bus"):
        for _ in range(3):
            bus.publish(ContentChunkEvent(text="x"))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
    # 第 1 次窗口外 → 1 条 warning（含栈）；第 2/3 次窗口内 → 2 条 debug
    assert len(warnings) == 1
    assert len(debugs) == 2
    # warning 含完整异常栈（exc_info 渲染 traceback）
    assert "Traceback" in caplog.text
    assert "boom" in caplog.text


def test_publish_exc_window_expiry_logs_warning_again_regression(caplog):
    """L3：窗口过期后同事件类型再次异常重新打 warning（时间推进模拟）。"""
    bus = _reset_bus()
    bus.subscribe(_boom, ContentChunkEvent)
    with caplog.at_level(logging.DEBUG, logger="src.tui.events.event_bus"):
        bus.publish(ContentChunkEvent(text="a"))  # 窗口外 → warning
        bus.publish(ContentChunkEvent(text="b"))  # 窗口内 → debug
        # 模拟 5s 窗口过期（直接推进限频字典时间戳）
        eb._last_exc_log["ContentChunkEvent"] -= eb._EXC_LOG_WINDOW
        bus.publish(ContentChunkEvent(text="c"))  # 再次窗口外 → warning
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert len(warnings) == 2
    assert len(debugs) == 1


# ── L3：不同类型独立限频 ──────────────────────────────────

def test_publish_exc_rate_limit_per_event_type_regression(caplog):
    """L3：不同事件类型独立限频——各自 5s 窗口内各打 1 条 warning。"""
    bus = _reset_bus()
    bus.subscribe(_boom, ContentChunkEvent)
    bus.subscribe(_boom, ReasoningChunkEvent)
    with caplog.at_level(logging.DEBUG, logger="src.tui.events.event_bus"):
        bus.publish(ContentChunkEvent(text="a"))
        bus.publish(ReasoningChunkEvent(text="b"))
        bus.publish(ContentChunkEvent(text="c"))  # 与第 1 次同类型窗口内 → debug
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert len(warnings) == 2  # ContentChunkEvent + ReasoningChunkEvent 各 1
    assert len(debugs) == 1
    # warning 文案含事件类型名（分桶依据）
    assert any("ContentChunkEvent" in r.message for r in warnings)
    assert any("ReasoningChunkEvent" in r.message for r in warnings)


# ── L3：正常路径 / 异常隔离 ──────────────────────────────

def test_publish_normal_no_log_regression(caplog):
    """L3 回归：正常 handler（无异常）不产生任何日志（限频不影响分发）。"""
    bus = _reset_bus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev.text), OutputEvent)
    with caplog.at_level(logging.DEBUG, logger="src.tui.events.event_bus"):
        bus.publish(OutputEvent(text="hello"))
    assert seen == ["hello"]
    assert caplog.records == []


def test_publish_exc_isolation_regression():
    """L3 回归：handler 异常不影响其他 handler 调用（异常隔离）。"""
    bus = _reset_bus()
    calls = []
    bus.subscribe(lambda ev: calls.append("ok"), OutputEvent)
    bus.subscribe(_boom, OutputEvent)
    # 抛异常 handler 注册在前、正常 handler 在后——异常隔离保证后续 handler
    # 仍被调用（for 循环 try/except 逐个执行，不中断）。
    bus.publish(OutputEvent(text="x"))
    assert calls == ["ok"]
