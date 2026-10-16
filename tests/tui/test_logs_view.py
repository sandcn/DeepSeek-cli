"""会话日志 / 投影浏览器（/logs）单元测试。

覆盖：视图注册与清单条目、状态类型、命令数据构建（事件条目 / 模型历史投影 /
投影单元状态 / 统计 / 一致性校验）、视图渲染（可见 / 不可见）、文本回退。
不依赖真实 ChatUI 与内核。
"""

from __future__ import annotations

import types

import pytest


@pytest.fixture(autouse=True)
def _clean_view_registry():
    import src.tui.app.view_registry as vreg

    vreg.reset()
    yield
    vreg.reset()


def _make_log():
    from src.core.events.agent_types import SessionEventType
    from src.core.session_log import SessionLog

    log = SessionLog(session_id="test-session")
    log.append_message({"role": "system", "content": "sys prompt"})
    log.append(SessionEventType.TURN_START)
    log.append_message({"role": "user", "content": "hello world"})
    log.append(SessionEventType.STEP_START)
    log.append_message({
        "role": "assistant", "content": "hi",
        "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}],
    })
    log.append_message({"role": "tool", "content": "ok", "tool_call_id": "c1"})
    log.append_message({"role": "assistant", "content": "done"})
    log.append(SessionEventType.TURN_END)
    return log


def _ctx_with(log):
    return types.SimpleNamespace(
        session=types.SimpleNamespace(
            session_log=log, messages=log.derive_messages(),
        ),
        messages=log.derive_messages(),
    )


# ── 视图注册与清单 ────────────────────────────────────


def test_logs_view_registered():
    from src.tui.app.view_registry import active_view_ids, fullscreen_views

    assert "logs" in active_view_ids()
    assert "logs" in fullscreen_views()


def test_manifest_declares_logs_view_and_command():
    from src.plugins.manifest import COMMAND_PLUGIN_ENTRIES, UI_VIEW_ENTRIES

    view_ids = [e["config"]["id"] for e in UI_VIEW_ENTRIES]
    assert "logs" in view_ids
    # 清单 ui_view 顺序须与内置声明顺序一致（test_plugin_ui_layer 契约）
    from src.tui.app import view_registry as vreg

    assert view_ids == vreg.builtin_view_ids()

    cmd_names = [e["config"].get("name") for e in COMMAND_PLUGIN_ENTRIES]
    assert "logs" in cmd_names


def test_logs_view_component_resolvable():
    from src.tui.app.logs_view import LogsView
    from src.tui.app.view_registry import fullscreen_views

    assert fullscreen_views()["logs"] is LogsView


# ── 状态类型 ──────────────────────────────────────────


def test_logs_view_state_defaults_and_final():
    from src.tui.app._state_types import ListViewState, LogsViewState

    state = LogsViewState()
    assert isinstance(state, ListViewState)
    assert state.entries == [] and state.messages == [] and state.projections == []
    assert state.verify_ok is None and state.verify_text == ""
    assert state.stats == [] and state.pane_mode == "event" and state.refresh_seq == 0
    assert state.try_set_final("cancel") is True
    assert state.done is True and state.action == "cancel"
    assert state.try_set_final("timeout") is False


def test_model_has_logs_view_and_reset_preserves_seq():
    from src.tui.app.model import AppModel

    model = AppModel()
    assert model.logs_view is not None
    model.logs_view.seq = 7
    model.logs_view.entries = [{"seq": 1}]
    model.reset_display()
    assert model.logs_view.seq == 7
    assert model.logs_view.entries == []


def test_ui_runtime_logs_state_cls():
    from src.core.adapters.ui_runtime import get_logs_view_state_cls
    from src.tui.app._state_types import LogsViewState

    assert get_logs_view_state_cls() is LogsViewState


# ── 命令数据构建 ──────────────────────────────────────


def test_build_log_entries():
    from src.core.commands._logs_cmd import build_log_entries

    log = _make_log()
    entries = build_log_entries(log)
    assert len(entries) == len(log.events())
    assert [e["seq"] for e in entries] == list(range(1, len(entries) + 1))
    labels = {e["type"]: e["label"] for e in entries}
    assert labels["user/message"] == "用户消息"
    assert labels["turn/start"] == "轮次开始"
    user = next(e for e in entries if e["type"] == "user/message")
    assert "hello world" in user["summary"]
    tool = next(e for e in entries if e["type"] == "tool/result")
    assert "c1" in tool["summary"]
    assert isinstance(user["data"], dict)


def test_build_log_messages():
    from src.core.commands._logs_cmd import build_log_messages

    log = _make_log()
    messages = build_log_messages(log)
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert messages[1]["summary"] == "hello world"
    assert messages[2]["tool_calls"]


def test_build_projection_entries():
    from src.core.commands._logs_cmd import build_projection_entries

    log = _make_log()
    projections = build_projection_entries(log.events())
    names = [p["name"] for p in projections]
    assert "turnBoundary" in names
    turn = next(p for p in projections if p["name"] == "turnBoundary")
    assert turn["state_rows"]
    assert "turn" in turn["state_text"]


def test_build_log_stats():
    from src.core.commands._logs_cmd import build_log_messages, build_log_stats

    log = _make_log()
    rows = build_log_stats(log.events(), build_log_messages(log))
    labels = {label for label, _value in rows}
    assert "事件总数" in labels and "模型历史" in labels
    assert "用户消息" in labels


def test_verify_log():
    from src.core.commands._logs_cmd import verify_log

    log = _make_log()
    ok, text = verify_log(log, log.derive_messages())
    assert ok is True and "一致" in text
    ok2, text2 = verify_log(log, [{"role": "user", "content": "changed"}])
    assert ok2 is False and "不一致" in text2


def test_build_log_snapshot_without_log():
    from src.core.commands._logs_cmd import build_log_snapshot

    ctx = types.SimpleNamespace(session=types.SimpleNamespace(session_log=None))
    assert build_log_snapshot(ctx) is None
    ctx2 = types.SimpleNamespace()
    assert build_log_snapshot(ctx2) is None


def test_build_log_snapshot_fields():
    from src.core.commands._logs_cmd import build_log_snapshot

    log = _make_log()
    data = build_log_snapshot(_ctx_with(log))
    assert set(data) == {
        "entries", "messages", "projections", "verify_ok", "verify_text", "stats",
    }
    assert data["verify_ok"] is True
    assert data["entries"] and data["messages"]


# ── 命令注册与文本回退 ────────────────────────────────


def test_logs_command_declared():
    from src.core.commands._logs_cmd import LogsCommand
    from src.core.commands.base import declared_command_plugin

    assert declared_command_plugin("logs") is not None
    assert LogsCommand().meta.name == "logs"


def test_logs_text_returns_true_without_log(capsys):
    from src.core.commands._logs_cmd import _logs_text

    ctx = types.SimpleNamespace(session=types.SimpleNamespace(session_log=None))
    assert _logs_text(ctx) is True


def test_cmd_logs_falls_back_without_chat_ui():
    from src.core.commands._logs_cmd import _cmd_logs

    log = _make_log()
    assert _cmd_logs(_ctx_with(log)) is True


# ── 视图渲染 ──────────────────────────────────────────


def _render(model, width=100):
    from src.tui.ink import h, renderToString

    from src.tui.app.logs_view import LogsView

    return renderToString(h(LogsView, {"model": model, "width": width}), {"columns": width})


def test_logs_view_renders_entries_and_detail():
    from src.core.commands._logs_cmd import build_log_snapshot
    from src.tui.app._state_types import LogsViewState
    from src.tui.app.model import AppModel

    log = _make_log()
    data = build_log_snapshot(_ctx_with(log))
    model = AppModel()
    # selected=2 → 选中 user/message 事件（右栏详情显示其类型标签与内容）；
    # follow_tail=False 固定选择（默认跟随最新会跳到末条）
    state = LogsViewState(visible=True, seq=1, selected=2, follow_tail=False, **data)
    model.logs_view = state

    out = _render(model)
    assert "会话日志" in out
    assert "用户消息" in out
    assert "hello world" in out


def test_logs_view_pane_modes_render():
    from src.core.commands._logs_cmd import build_log_snapshot
    from src.tui.app._state_types import LogsViewState
    from src.tui.app.model import AppModel

    log = _make_log()
    data = build_log_snapshot(_ctx_with(log))
    for mode, needle in (
        ("messages", "消息投影"),
        ("projections", "投影状态"),
        ("verify", "一致性校验"),
    ):
        model = AppModel()
        model.logs_view = LogsViewState(visible=True, seq=1, pane_mode=mode, **data)
        out = _render(model)
        assert needle in out, mode


def test_logs_view_invisible_renders_empty():
    from src.tui.app.model import AppModel

    model = AppModel()  # logs_view 默认不可见
    out = _render(model)
    assert "会话日志" not in out


# ── 实时刷新（签名检测 + 跟随最新） ────────────────────


def test_log_signature_none_without_log():
    from src.core.commands._logs_cmd import _log_signature

    assert _log_signature(types.SimpleNamespace()) is None
    ctx = types.SimpleNamespace(session=types.SimpleNamespace(session_log=None))
    assert _log_signature(ctx) is None


def test_log_signature_changes_on_growth():
    from src.core.commands._logs_cmd import _log_signature

    log = _make_log()
    ctx = _ctx_with(log)
    before = _log_signature(ctx)
    assert before is not None
    log.append_message({"role": "user", "content": "more"})
    # 模拟 LoggedMessageList 同步（消息数变化也进入签名）
    ctx.session.messages.append({"role": "user", "content": "more"})
    after = _log_signature(ctx)
    assert after != before
    assert after[0] == before[0] + 1


def test_open_logs_ui_tick_rebuilds_on_growth(monkeypatch):
    """会话日志增长时命令线程 tick 自动重建视图数据（无需手动刷新）。"""
    import src.core.commands._view_opener as opener
    from src.core.commands import _logs_cmd
    from src.tui.app._state_types import LogsViewState

    captured: dict = {}

    def fake_open(ctx, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(opener, "open_fullscreen_view", fake_open)

    log = _make_log()
    ctx = _ctx_with(log)
    assert _logs_cmd._open_logs_ui(ctx) is True

    state = LogsViewState(visible=True, seq=1)
    captured["setup"](None, state)
    assert state.entries and state.verify_ok is True
    n0 = len(state.entries)

    log.append_message({"role": "user", "content": "second question"})
    ctx.session.messages.append({"role": "user", "content": "second question"})
    assert captured["on_tick"](state) is False
    assert len(state.entries) == n0 + 1
    assert any("second question" in e["summary"] for e in state.entries)
    assert state.verify_ok is True


def test_open_logs_ui_falls_back_without_log(monkeypatch):
    import src.core.commands._view_opener as opener
    from src.core.commands import _logs_cmd

    called: dict = {}
    monkeypatch.setattr(
        opener, "open_fullscreen_view",
        lambda ctx, **kwargs: called.setdefault("hit", True) or True,
    )
    ctx = types.SimpleNamespace(session=types.SimpleNamespace(session_log=None))
    assert _logs_cmd._open_logs_ui(ctx) is False
    assert "hit" not in called


def test_logs_view_follow_tail_default_and_render():
    from src.core.commands._logs_cmd import build_log_snapshot
    from src.tui.app._state_types import LogsViewState
    from src.tui.app.model import AppModel

    assert LogsViewState().follow_tail is True

    log = _make_log()
    data = build_log_snapshot(_ctx_with(log))
    model = AppModel()
    model.logs_view = LogsViewState(visible=True, seq=1, **data)
    _render(model)
    # 跟随最新：渲染期选中项自动落在末条事件
    assert model.logs_view.selected == len(data["entries"]) - 1


def test_logs_view_follow_tail_off_keeps_selection():
    from src.core.commands._logs_cmd import build_log_snapshot
    from src.tui.app._state_types import LogsViewState
    from src.tui.app.model import AppModel

    log = _make_log()
    data = build_log_snapshot(_ctx_with(log))
    model = AppModel()
    model.logs_view = LogsViewState(
        visible=True, seq=1, selected=1, follow_tail=False, **data,
    )
    out = _render(model)
    assert "会话日志" in out
    assert model.logs_view.selected == 1


# ── 视图纯函数 ────────────────────────────────────────


def test_logs_search_text():
    from src.tui.app.logs_view import _logs_search_text

    text = _logs_search_text({
        "type": "user/message", "label": "用户消息",
        "summary": "hello", "data": {"content": "hello world"},
    })
    assert "user/message" in text and "hello world" in text


def test_event_detail_rows():
    from src.tui.app.logs_view import _event_detail_rows

    rows = _event_detail_rows({
        "seq": 3, "type": "user/message", "label": "用户消息", "time": 0,
        "summary": "hello", "data": {"content": "hello world", "index": 2},
    }, 60)
    flat = ["".join(r.text for r in row) for row in rows]
    assert any("用户消息" in line for line in flat)
    assert any("hello world" in line for line in flat)


def test_data_rows_json_and_scalar():
    from src.tui.app.logs_view import _data_rows

    rows = _data_rows({"tool_calls": [{"function": {"name": "bash"}}], "index": 1}, 80)
    flat = "".join("".join(r.text for r in row) for row in rows)
    assert "tool_calls" in flat and "bash" in flat and "index" in flat
    assert _data_rows({}, 40)[0][0].text == "(无附加数据)"


def test_pane_label():
    from src.tui.app.logs_view import PANE_MODES, pane_label

    assert PANE_MODES == ("event", "messages", "projections", "verify")
    assert pane_label("event") == "事件详情"
    assert pane_label("unknown") == "事件详情"


# ── 实时刷新器（装配注入视图渲染期调用） ──────────────


def test_make_logs_refresher_rebuilds_on_growth():
    from src.core.commands._logs_cmd import make_logs_refresher

    log = _make_log()
    ctx = _ctx_with(log)
    state = {"entries": []}
    refresher = make_logs_refresher(
        ctx.session, lambda data: state.__setitem__("entries", data["entries"]),
    )
    assert refresher() is True                     # 首帧构建
    assert state["entries"]                        # 已写入数据
    assert refresher() is False                    # 签名未变 → 零重建

    log.append_message({"role": "user", "content": "next question"})
    ctx.session.messages.append({"role": "user", "content": "next question"})
    assert refresher() is True                     # 日志增长 → 重建
    assert any("next question" in e["summary"] for e in state["entries"])


def test_make_logs_refresher_force_rebuild():
    from src.core.commands._logs_cmd import make_logs_refresher

    log = _make_log()
    ctx = _ctx_with(log)
    count = {"n": 0}

    def _apply(_data):
        count["n"] += 1

    refresher = make_logs_refresher(ctx.session, _apply)
    refresher()
    assert refresher(True) is True                 # force 强制重建
    assert count["n"] == 2


def test_make_logs_refresher_without_log():
    from src.core.commands._logs_cmd import make_logs_refresher

    refresher = make_logs_refresher(
        types.SimpleNamespace(session_log=None), lambda _d: None,
    )
    assert refresher() is False


def test_logs_view_calls_refresher_when_visible():
    from src.tui.app._state_types import LogsViewState
    from src.tui.app.model import AppModel

    model = AppModel()
    calls: list = []
    model.logs_refresher = lambda force=False: calls.append(force) or True
    model.logs_view = LogsViewState(visible=True, seq=1)
    _render(model)
    assert calls, "可见时渲染期应调用刷新器（实时刷新）"


# ── 关闭语义 ──────────────────────────────────────────


def test_is_logs_close_key():
    from src.tui._input_parser import KeyEvent
    from src.tui.app.logs_view import _is_logs_close_key

    assert _is_logs_close_key(KeyEvent(kind="f12")) is True
    assert _is_logs_close_key(KeyEvent(kind="escape")) is True
    assert _is_logs_close_key(KeyEvent(kind="char", char="x")) is False


def test_close_view_clears_fullscreen():
    from src.tui.app.logs_view import _close_view
    from src.tui.app.model import AppModel

    model = AppModel()
    model.fullscreen = "logs"
    state = model.logs_view
    _close_view(model, state)
    assert model.fullscreen == ""
    assert state.done is True and state.action == "cancel"
    # 其它全屏视图不受影响
    model.fullscreen = "trace"
    _close_view(model, model.logs_view)
    assert model.fullscreen == "trace"
