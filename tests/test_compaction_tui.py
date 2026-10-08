"""压缩 TUI 显示单元测试。

覆盖：显示事件 → 命令映射（EventDispatcher）→ 状态更新（apply）→
模式行行首 ``compact · N`` 渲染。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.events.display_types import CompactionChangedEvent
from src.tui._const import CompactionCmd, NotificationCmd
from src.tui._dispatcher import EventDispatcher
from src.tui.app._state_types import StatusState
from src.tui.app.apply import _do_compaction
from src.tui.app.input_area import _build_mode_line


def _text(line) -> str:
    return "".join(run.text for run in line.runs)


# ── 模式行显示 ────────────────────────────────────────────

def test_mode_line_shows_compact_when_active():
    line = _build_mode_line(80, True, compaction_active=1)
    assert "compact \u00b7 1" in _text(line)


def test_mode_line_hides_compact_when_zero():
    line = _build_mode_line(80, True, compaction_active=0)
    assert "compact" not in _text(line)


def test_mode_line_combined_prefix():
    line = _build_mode_line(120, False, 45.0, bash_count=1, subagent_count=2,
                            compaction_active=3)
    text = _text(line)
    assert "main " in text
    assert "bash \u00b7 1" in text
    assert "subagent \u00b7 2" in text
    assert "compact \u00b7 3" in text


# ── apply 状态更新 ────────────────────────────────────────

def test_do_compaction_updates_status():
    model = SimpleNamespace(status=StatusState())
    _do_compaction(model, CompactionCmd(active=2))
    assert model.status.compaction_active == 2
    _do_compaction(model, CompactionCmd(active=0))
    assert model.status.compaction_active == 0


def test_do_compaction_defends_bad_value():
    model = SimpleNamespace(status=StatusState())
    _do_compaction(model, SimpleNamespace(active=None))
    assert model.status.compaction_active == 0


# ── dispatcher 事件映射 ───────────────────────────────────

def _dispatcher():
    cmds: list = []
    dispatcher = EventDispatcher(push_cmd=cmds.append, filter_fn=lambda source: True)
    return dispatcher, cmds


def test_dispatcher_started_aggregates_active_count():
    dispatcher, cmds = _dispatcher()
    dispatcher._on_compaction(CompactionChangedEvent(label="main", phase="started"))
    dispatcher._on_compaction(CompactionChangedEvent(label="agent-1", phase="started"))
    compactions = [c for c in cmds if isinstance(c, CompactionCmd)]
    assert compactions[-1].active == 2


def test_dispatcher_finished_clears_and_notifies():
    dispatcher, cmds = _dispatcher()
    dispatcher._on_compaction(CompactionChangedEvent(label="main", phase="started"))
    cmds.clear()
    dispatcher._on_compaction(CompactionChangedEvent(
        label="main", phase="finished", count=7, saved_tokens=1234,
    ))
    compactions = [c for c in cmds if isinstance(c, CompactionCmd)]
    notes = [c for c in cmds if isinstance(c, NotificationCmd)]
    assert compactions[-1].active == 0
    assert notes and "压缩 7 条消息" in notes[-1].text
    assert "1234t" in notes[-1].text


def test_dispatcher_failed_notifies_with_label_prefix():
    dispatcher, cmds = _dispatcher()
    dispatcher._on_compaction(CompactionChangedEvent(
        label="agent-2", phase="failed", detail="boom",
    ))
    notes = [c for c in cmds if isinstance(c, NotificationCmd)]
    assert notes and "[agent-2]" in notes[-1].text
    assert "压缩失败" in notes[-1].text


def test_dispatcher_registered_handler():
    dispatcher, _cmds = _dispatcher()
    from src.core.events.display_types import CompactionChangedEvent as Ev
    handlers = dispatcher.list_handlers()
    assert Ev in handlers
