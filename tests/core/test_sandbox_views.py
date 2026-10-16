"""文件沙盒界面视图渲染 / F11 快捷键 / 轨迹关联 单元测试。

覆盖 2026-10 新增：
  - ChangesView 增强渲染（文件 / 目录树 / 消息维度模式；差异 / 历史 / 统计 /
    预览右栏；不可见占位）；
  - 新视图渲染（SandboxStatsView / SandboxHistoryView / SandboxRecordsView）；
  - F11 = 文件变更审查器开关（解析 → 分发 → 回调；不经命令队列）；
  - ``_make_changes_toggle_cb`` 打开 / 关闭 / 重开复位；
  - 轨迹 ↔ 文件沙盒关联（``TraceRecord.message_index`` 填充 +
    ``_sandbox_change_rows``）；
  - 快捷键速查表含 F11；模型状态（reset 保留 seq）。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _clean_view_registry():
    import src.tui.app.view_registry as vreg

    vreg.reset()
    yield
    vreg.reset()


def _render(model, component, width=100):
    from src.tui.ink import h, renderToString

    return renderToString(h(component, {"model": model, "width": width}), {"columns": width})


def _make_sm(tmp_path):
    from src.core.sandbox_manager import SandboxManager

    sm = SandboxManager()
    sm.record_file_change(str(tmp_path / "a.py"), "old\n", "new\n", 3, tool_name="write_file")
    sm.record_file_change(str(tmp_path / "sub" / "b.py"), None, "x\n", 5, tool_name="write_file")
    return sm


def _changes_state(sm, **kwargs):
    from src.core.commands._sandbox_cmd import (
        build_change_entries,
        build_message_entries,
        build_sandbox_sections,
    )
    from src.tui.app._state_types import ChangesViewState

    data = dict(
        visible=True, seq=1,
        entries=build_change_entries(sm),
        messages=build_message_entries(sm),
        sections=build_sandbox_sections(sm),
    )
    data.update(kwargs)
    return ChangesViewState(**data)


# ── ChangesView 渲染 ──────────────────────────────────


def test_changes_view_file_mode_renders(tmp_path):
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.changes_view = _changes_state(sm)
    out = _render(model, ChangesView)
    assert "文件变更审查" in out
    assert "a.py" in out


def test_changes_view_tree_mode_renders(tmp_path):
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.changes_view = _changes_state(sm, view_mode="tree")
    out = _render(model, ChangesView)
    assert "目录树" in out


def test_changes_view_message_mode_renders(tmp_path):
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.changes_view = _changes_state(sm, view_mode="message")
    out = _render(model, ChangesView)
    assert "消息" in out


@pytest.mark.parametrize("mode,needle", [
    ("diff", "文件变更审查"),
    ("history", "历史"),
    ("stats", "概览"),
    ("preview", "回滚预览"),
])
def test_changes_view_detail_modes(tmp_path, mode, needle):
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.changes_view = _changes_state(sm, detail_mode=mode, pane="detail")
    out = _render(model, ChangesView)
    assert needle in out


def test_changes_view_invisible_renders_empty(tmp_path):
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    model = AppModel()  # changes_view 默认不可见
    out = _render(model, ChangesView)
    assert "文件变更审查" not in out


def test_changes_view_help_panel_renders(tmp_path):
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.changes_view = _changes_state(sm, help_open=True)
    out = _render(model, ChangesView)
    assert "回滚" in out


# ── 新视图渲染 ────────────────────────────────────────


def test_sandbox_stats_view_renders(tmp_path):
    from src.core.commands._sandbox_cmd import build_sandbox_sections
    from src.tui.app._state_types import SandboxStatsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.sandbox_view = SandboxStatsViewState(
        visible=True, seq=1, sections=build_sandbox_sections(sm),
    )
    out = _render(model, SandboxStatsView)
    assert "文件沙盒" in out
    assert "概览" in out


def test_sandbox_stats_view_invisible(tmp_path):
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    out = _render(AppModel(), SandboxStatsView)
    assert "文件沙盒" not in out


def test_sandbox_stats_view_help(tmp_path):
    from src.tui.app._state_types import SandboxStatsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_stats_view import SandboxStatsView

    model = AppModel()
    model.sandbox_view = SandboxStatsViewState(visible=True, seq=1, help_open=True)
    out = _render(model, SandboxStatsView)
    assert "清空" in out


def test_sandbox_history_view_renders(tmp_path):
    from src.core.commands._sandbox_cmd import build_message_entries
    from src.tui.app._state_types import SandboxHistoryViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_history_view import SandboxHistoryView

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.sandbox_history_view = SandboxHistoryViewState(
        visible=True, seq=1, entries=build_message_entries(sm),
    )
    out = _render(model, SandboxHistoryView)
    assert "沙盒历史" in out
    assert "消息 3" in out


def test_sandbox_records_view_renders(tmp_path):
    from src.core.commands._sandbox_cmd import build_record_history_entries
    from src.tui.app._state_types import SandboxRecordsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_records_view import SandboxRecordsView

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.sandbox_records_view = SandboxRecordsViewState(
        visible=True, seq=1, entries=build_record_history_entries(sm),
    )
    out = _render(model, SandboxRecordsView)
    assert "变更记录流水" in out
    assert "a.py" in out


def test_sandbox_records_view_reverse_and_help(tmp_path):
    from src.core.commands._sandbox_cmd import build_record_history_entries
    from src.tui.app._state_types import SandboxRecordsViewState
    from src.tui.app.model import AppModel
    from src.tui.app.sandbox_records_view import SandboxRecordsView

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.sandbox_records_view = SandboxRecordsViewState(
        visible=True, seq=1, sort_desc=True,
        entries=build_record_history_entries(sm), help_open=True,
    )
    out = _render(model, SandboxRecordsView)
    assert "倒序" in out


# ── F11 快捷键 ────────────────────────────────────────


def _make_dispatcher():
    from src.tui._input_buffer import InputBufferEditor
    from src.tui._input_dispatcher import InputDispatcher
    from src.tui._input_io import InputIO
    from src.tui._input_parser import InputParser

    r, w = os.pipe()
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), w


def test_f11_dispatches_changes_toggle():
    from src.tui._input_parser import KeyEvent

    d, w = _make_dispatcher()
    try:
        hits: list = []
        d.set_changes_toggle_callback(lambda: hits.append(True))
        d._dispatch_key_event(KeyEvent(kind="f11"))
        assert hits == [True]
    finally:
        os.close(w)


def test_f11_through_read_stdin_once_escape_path():
    d, w = _make_dispatcher()
    try:
        hits: list = []
        d.set_changes_toggle_callback(lambda: hits.append(True))
        os.write(w, b"\x1b[23~")
        assert d.read_stdin_once() is True
        assert hits == [True]
    finally:
        os.close(w)


def test_changes_toggle_callback_registered_name():
    d, w = _make_dispatcher()
    try:
        cb = lambda: None  # noqa: E731
        d.set_changes_toggle_callback(cb)
        assert d.get_callback("changes_toggle") is cb
    finally:
        os.close(w)


def test_changes_toggle_without_callback_is_noop():
    d, w = _make_dispatcher()
    try:
        d._handle_changes_toggle()  # 未注入 → no-op（不抛异常）
    finally:
        os.close(w)


def test_make_changes_toggle_cb_toggles_and_builds():
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    forced: list = []
    model.sandbox_refresher = lambda force=False: forced.append(force) or True
    redraws: list = []
    session = SimpleNamespace(request_bottom_redraw=lambda: redraws.append(True))

    cb = _make_changes_toggle_cb(model, session)
    cb()
    assert model.fullscreen == "changes"
    assert forced == [True]
    assert redraws
    cb()
    assert model.fullscreen == ""


def test_make_changes_toggle_cb_resets_state_on_reopen():
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    model.sandbox_refresher = lambda force=False: True
    session = SimpleNamespace(request_bottom_redraw=lambda: None)
    cb = _make_changes_toggle_cb(model, session)

    cb()
    model.changes_view.done = True
    model.changes_view.selected = 5
    model.changes_view.view_mode = "message"
    model.changes_view.search_mode = True
    model.changes_view.revert_all_confirm = True
    cb()          # 关闭
    cb()          # 再次打开
    assert model.fullscreen == "changes"
    assert model.changes_view.done is False
    assert model.changes_view.selected == 0
    assert model.changes_view.view_mode == "message"  # 模式保留（浏览偏好）
    assert model.changes_view.search_mode is False
    assert model.changes_view.revert_all_confirm is False


def test_make_changes_toggle_cb_without_refresher():
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    session = SimpleNamespace(request_bottom_redraw=lambda: None)
    cb = _make_changes_toggle_cb(model, session)
    cb()
    assert model.fullscreen == "changes"


def test_shortcut_rows_include_f11():
    from src.core.internal.commands._command_core import SHORTCUT_ROWS

    flat = {key: desc for row in SHORTCUT_ROWS for key, desc in row}
    assert flat.get("F11") == "变更审查"
    assert flat.get("F12") == "会话日志"


# ── 模型状态 ──────────────────────────────────────────


def test_reset_display_preserves_sandbox_view_seq():
    from src.tui.app.model import AppModel

    model = AppModel()
    model.sandbox_view.seq = 7
    model.sandbox_history_view.seq = 3
    model.sandbox_records_view.seq = 2
    model.reset_display()
    assert model.sandbox_view.seq == 7
    assert model.sandbox_history_view.seq == 3
    assert model.sandbox_records_view.seq == 2
    assert model.sandbox_view.sections == []


def test_consumer_set_sandbox_source():
    from src.tui._consumer import ChatUIConsumer

    consumer = ChatUIConsumer.__new__(ChatUIConsumer)
    consumer._rs = SimpleNamespace(sandbox_source=None)
    sentinel = object()
    consumer.set_sandbox_source(sentinel)
    assert consumer._rs.sandbox_source is sentinel


# ── 轨迹 ↔ 文件沙盒关联 ───────────────────────────────


def test_trace_record_message_index_default():
    from src.tui.app.trace_types import TraceRecord

    assert TraceRecord().message_index == -1


def test_records_from_messages_fills_message_index():
    from src.tui.app.trace import _records_from_messages

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant", "content": "yo",
            "tool_calls": [{"id": "c1", "function": {"name": "write_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    records, _rows = _records_from_messages(messages)
    tool_recs = [r for r in records if r.kind == "tool" and r.tool_name == "write_file"]
    assert tool_recs and tool_recs[0].message_index == 2


def test_sandbox_change_rows_matches(tmp_path):
    from src.tui.app.model import AppModel
    from src.tui.app.trace_types import TraceRecord
    from src.tui.app.trace_view import _sandbox_change_rows

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.sandbox_source = lambda: sm
    rec = TraceRecord(kind="tool", tool_name="write_file", message_index=3)
    rows = _sandbox_change_rows(model, rec, 60)
    assert rows
    assert any("文件变更" in r.text for row in rows for r in row)
    assert any("a.py" in r.text for row in rows for r in row)


def test_sandbox_change_rows_no_match(tmp_path):
    from src.tui.app.model import AppModel
    from src.tui.app.trace_types import TraceRecord
    from src.tui.app.trace_view import _sandbox_change_rows

    sm = _make_sm(tmp_path)
    model = AppModel()
    model.sandbox_source = lambda: sm
    # 非 tool 记录
    assert _sandbox_change_rows(model, TraceRecord(kind="content", message_index=3), 60) == []
    # message_index 未知
    assert _sandbox_change_rows(
        model, TraceRecord(kind="tool", tool_name="write_file", message_index=-1), 60,
    ) == []
    # 未注入沙盒源
    assert _sandbox_change_rows(
        AppModel(), TraceRecord(kind="tool", tool_name="write_file", message_index=3), 60,
    ) == []
    # 工具名不匹配
    assert _sandbox_change_rows(
        model, TraceRecord(kind="tool", tool_name="other", message_index=3), 60,
    ) == []


def test_changes_view_refresher_executes_pending_action(tmp_path):
    """F11 路径（无命令线程）：视图渲染期刷新器消费待处理动作（回滚落地）。

    命令路径（``/changes``）与 F11 直开路径共用 ``consume_sandbox_actions``
    ——本测试模拟 F11 路径：仅注入刷新器（无命令线程轮询），渲染期即执行回滚。
    """
    from src.core.commands._sandbox_cmd import (
        build_change_entries,
        consume_sandbox_actions,
        make_sandbox_refresher,
    )
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)
    old = get_sandbox_manager()
    set_sandbox_manager(sm)
    try:
        model = AppModel()

        def _apply(data, _m=model):
            _m.changes_view.entries = data["files"]
            _m.changes_view.messages = data["messages"]
            _m.changes_view.sections = data["sections"]
            _m.changes_view.visible = True

        model.sandbox_refresher = make_sandbox_refresher(
            None, _apply, action_handler=lambda sb: consume_sandbox_actions(model, sb),
        )
        model.fullscreen = "changes"
        model.changes_view.visible = True
        model.changes_view.entries = build_change_entries(sm)
        a = [p for p in sm.get_file_paths() if p.endswith("a.py")][0]
        model.changes_view.applied = {"action": "revert", "path": a}
        model.changes_view.applied_seq = 1

        _render(model, ChangesView)  # 渲染期 → refresher → 消费动作
        assert model.changes_view.applied_consumed == 1
        assert "已回滚" in model.changes_view.status_message
    finally:
        set_sandbox_manager(old)


def test_f11_open_and_auto_update_on_sandbox_growth(tmp_path):
    """F11 打开变更审查器；沙盒记录增长后渲染期**自动更新**（流式期间可用）。

    模拟用户要求：AI 流式输出期间按 F11 打开文件变更审查器（不经命令队列），
    随后工具写入新文件 → 沙盒签名变化 → 视图渲染期刷新器重建数据。
    """
    from src.core.commands._sandbox_cmd import (
        consume_sandbox_actions,
        make_sandbox_refresher,
    )
    from src.core.sandbox_manager import get_sandbox_manager, set_sandbox_manager
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.changes_view import ChangesView
    from src.tui.app.model import AppModel

    sm = _make_sm(tmp_path)  # 2 条记录 / 2 个文件
    old = get_sandbox_manager()
    set_sandbox_manager(sm)
    try:
        model = AppModel()

        def _apply(data, _m=model):
            _m.changes_view.entries = data["files"]
            _m.changes_view.messages = data["messages"]
            _m.changes_view.sections = data["sections"]
            _m.changes_view.visible = True

        model.sandbox_refresher = make_sandbox_refresher(
            None, _apply, action_handler=lambda sb: consume_sandbox_actions(model, sb),
        )
        session = SimpleNamespace(request_bottom_redraw=lambda: None)
        cb = _make_changes_toggle_cb(model, session)
        cb()
        assert model.fullscreen == "changes"
        assert model.changes_view.visible is True
        assert len(model.changes_view.entries) == 2

        # 流式期间 AI 工具写入新文件 → 视图自动跟进（渲染期刷新，无需命令）
        sm.record_file_change(
            str(tmp_path / "c.py"), None, "y\n", 7, tool_name="write_file",
        )
        _render(model, ChangesView)  # 本帧渲染后（提交期）刷新器重建数据
        out = _render(model, ChangesView)  # 下一帧显示新数据（30Hz 认知即自动更新）
        assert len(model.changes_view.entries) == 3
        assert "3 个文件" in out  # 界面头部计数已自动更新
    finally:
        set_sandbox_manager(old)


def test_f11_open_visible_without_refresher():
    """无刷新器（无会话 / 沙盒缺失）时 F11 打开仍置可见（不显示空占位）。"""
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    session = SimpleNamespace(request_bottom_redraw=lambda: None)
    cb = _make_changes_toggle_cb(model, session)
    cb()
    assert model.fullscreen == "changes"
    assert model.changes_view.visible is True


# ── 关闭语义（Esc / F11 一次关闭） ─────────────────────


def test_is_fullscreen_close_key_extra_kinds():
    from src.tui._input_parser import KeyEvent
    from src.tui.app._modal_view import is_fullscreen_close_key

    assert is_fullscreen_close_key(KeyEvent(kind="escape")) is True
    assert is_fullscreen_close_key(KeyEvent(kind="ctrl_key", char="\x08")) is True
    assert is_fullscreen_close_key(KeyEvent(kind="f11"), ("f11",)) is True
    assert is_fullscreen_close_key(KeyEvent(kind="f11")) is False
    assert is_fullscreen_close_key(KeyEvent(kind="char", char="x")) is False


def test_close_fullscreen_view_clears_and_keeps_other():
    from src.tui.app._modal_view import close_fullscreen_view
    from src.tui.app.model import AppModel

    model = AppModel()
    model.fullscreen = "changes"
    model.changes_view.visible = True
    close_fullscreen_view(model, model.changes_view, "changes")
    assert model.fullscreen == ""
    assert model.changes_view.done is True

    other = AppModel()
    other.fullscreen = "logs"
    close_fullscreen_view(other, other.changes_view, "changes")
    assert other.fullscreen == "logs"  # 不误关其它视图
    assert other.changes_view.done is True


def test_f11_closes_any_sandbox_view():
    """沙盒视图族（changes / sandbox / sandbox_history / sandbox_records）任一
    打开时，F11 一律关闭（回到聊天界面）。"""
    from src.tui._assembly_steps import _make_changes_toggle_cb
    from src.tui.app.model import AppModel

    for vid in ("changes", "sandbox", "sandbox_history", "sandbox_records"):
        model = AppModel()
        model.fullscreen = vid
        cb = _make_changes_toggle_cb(
            model, SimpleNamespace(request_bottom_redraw=lambda: None),
        )
        cb()
        assert model.fullscreen == "", vid


def test_sandbox_change_rows_without_sandbox(tmp_path):
    from src.tui.app.model import AppModel
    from src.tui.app.trace_types import TraceRecord
    from src.tui.app.trace_view import _sandbox_change_rows

    model = AppModel()
    model.sandbox_source = lambda: None
    assert _sandbox_change_rows(
        model, TraceRecord(kind="tool", tool_name="write_file", message_index=3), 60,
    ) == []
