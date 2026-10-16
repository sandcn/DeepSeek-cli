"""会话日志测试 — 仅追加事件日志、消息视图与投影（会话事实源）。

覆盖：
- SessionLog 追加 / 投影 / 回放 / fork / restore；
- LoggedMessageList 完整 list 语义（append/pop/切片/删除/清空）；
- 结构变更事件（insert/replace/delete/truncate/reset）在回放中正确生效；
- 「模型可见即已记录」：视图与日志投影始终一致；
- ProjectionRegistry 增量折叠与快照。
"""

from __future__ import annotations

import pytest

from src.core.events.agent_types import SessionEventType
from src.core.session_log import (
    LoggedMessageList,
    ProjectionRegistry,
    SessionLog,
    derive_messages,
)


# ── SessionLog 基础 ─────────────────────────────────────


def test_append_and_derive_messages():
    log = SessionLog(session_id="s1")
    log.append(SessionEventType.SYSTEM_MESSAGE, content="sys")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.ASSISTANT_MESSAGE, content="hello")
    assert log.derive_messages() == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_append_assistant_with_tool_calls():
    log = SessionLog()
    log.append(
        SessionEventType.ASSISTANT_MESSAGE,
        content="",
        tool_calls=[{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}],
    )
    log.append(SessionEventType.TOOL_RESULT, content="file body", tool_call_id="1")
    messages = log.derive_messages()
    assert messages[0]["tool_calls"][0]["id"] == "1"
    assert messages[1] == {"role": "tool", "content": "file body", "tool_call_id": "1"}


def test_assistant_attempt_not_in_model_history():
    log = SessionLog()
    log.append(SessionEventType.ASSISTANT_ATTEMPT, reason="retry", error="boom")
    assert log.derive_messages() == []


def test_append_message_infers_event_type():
    log = SessionLog()
    log.append_message({"role": "user", "content": "u"})
    log.append_message({"role": "system", "content": "s"})
    assert [m["role"] for m in log.derive_messages()] == ["user", "system"]


def test_fork_at_position():
    log = SessionLog()
    log.append(SessionEventType.USER_MESSAGE, content="one")
    log.append(SessionEventType.USER_MESSAGE, content="two")
    forked = log.fork(at=1)
    assert [m["content"] for m in forked.derive_messages()] == ["one"]
    assert [m["content"] for m in log.derive_messages()] == ["one", "two"]


def test_restore_roundtrip():
    log = SessionLog(session_id="s1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.ASSISTANT_MESSAGE, content="yo")
    restored = SessionLog.restore(log.snapshot(), session_id="s1")
    assert restored.session_id == "s1"
    assert restored.derive_messages() == log.derive_messages()
    # seq 继续递增，避免与既有事件冲突
    event = restored.append(SessionEventType.USER_MESSAGE, content="again")
    assert event.seq == 3


def test_verify_detects_divergence():
    log = SessionLog()
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    assert log.verify([{"role": "user", "content": "hi"}]) is True
    assert log.verify([{"role": "user", "content": "other"}]) is False


# ── LoggedMessageList ───────────────────────────────────


def test_view_reuses_log_facts():
    log = SessionLog()
    view = LoggedMessageList(log, initial=[{"role": "system", "content": "sys"}])
    view.append({"role": "user", "content": "hi"})
    assert list(view) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert log.derive_messages() == list(view)
    assert view.verify() is True


def test_view_pop_updates_log():
    view = LoggedMessageList(initial=[{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    popped = view.pop()
    assert popped == {"role": "user", "content": "b"}
    assert view.verify() is True
    assert [m["content"] for m in view.log.derive_messages()] == ["a"]


def test_view_slice_assignment_replaces_range():
    view = LoggedMessageList(initial=[
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "user", "content": "d"},
    ])
    view[1:3] = [{"role": "user", "content": "x"}, {"role": "user", "content": "y"}, {"role": "user", "content": "z"}]
    assert [m["content"] for m in view] == ["a", "x", "y", "z", "d"]
    assert view.verify() is True
    assert [m["content"] for m in view.log.derive_messages()] == ["a", "x", "y", "z", "d"]


def test_view_whole_slice_assignment_keeps_system():
    view = LoggedMessageList(initial=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
    ])
    view[:] = [{"role": "system", "content": "sys"}]
    assert list(view) == [{"role": "system", "content": "sys"}]
    assert view.verify() is True


def test_view_delitem_updates_log():
    view = LoggedMessageList(initial=[
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "user", "content": "c"},
    ])
    del view[1]
    assert [m["content"] for m in view] == ["a", "c"]
    assert view.verify() is True


def test_view_insert_updates_log():
    view = LoggedMessageList(initial=[{"role": "user", "content": "a"}])
    view.insert(0, {"role": "system", "content": "sys"})
    assert [m["content"] for m in view] == ["sys", "a"]
    assert view.verify() is True


def test_view_clear_keeps_system():
    view = LoggedMessageList(initial=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
    ])
    view.clear()
    assert list(view) == [{"role": "system", "content": "sys"}]
    assert view.verify() is True


def test_view_eq_with_list():
    view = LoggedMessageList(initial=[{"role": "user", "content": "a"}])
    assert view == [{"role": "user", "content": "a"}]
    assert view != [{"role": "user", "content": "b"}]


def test_view_rebind_to_fork():
    view = LoggedMessageList(initial=[{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    fork = view.log.fork(at=1)
    view.rebind(fork)
    assert [m["content"] for m in view] == ["a"]
    assert view.verify() is True


def test_view_replace_index():
    view = LoggedMessageList(initial=[{"role": "user", "content": "a"}])
    view[0] = {"role": "user", "content": "z"}
    assert list(view) == [{"role": "user", "content": "z"}]
    assert view.verify() is True


def test_view_index_error_on_bad_delete():
    view = LoggedMessageList(initial=[{"role": "user", "content": "a"}])
    with pytest.raises(IndexError):
        del view[5]


# ── derive_messages 结构事件 ────────────────────────────


def test_derive_truncate_and_reset():
    log = SessionLog()
    log.append(SessionEventType.SYSTEM_MESSAGE, content="sys")
    log.append(SessionEventType.USER_MESSAGE, content="a")
    log.append(SessionEventType.USER_MESSAGE, content="b")
    log.truncate(2)
    assert [m["content"] for m in log.derive_messages()] == ["sys", "a"]
    log.reset()
    assert [m["content"] for m in log.derive_messages()] == ["sys"]


def test_derive_delete_and_replace():
    from src.core.session_log.events import SessionEvent

    events = [
        SessionEvent(SessionEventType.USER_MESSAGE, 1, 0.0, {"content": "a"}),
        SessionEvent(SessionEventType.USER_MESSAGE, 2, 0.0, {"content": "b"}),
        SessionEvent(SessionEventType.REPLACE, 3, 0.0, {"index": 0, "message": {"role": "user", "content": "A"}}),
        SessionEvent(SessionEventType.DELETE, 4, 0.0, {"start": 1, "stop": 2}),
    ]
    assert [m["content"] for m in derive_messages(events)] == ["A"]


def test_derive_with_message_projection():
    log = SessionLog()
    log.append(SessionEventType.USER_MESSAGE, content="secret")
    messages = log.derive_messages(projections=[lambda m: {**m, "content": m["content"].upper()}])
    assert messages[0]["content"] == "SECRET"


# ── ProjectionRegistry ──────────────────────────────────


def test_projection_registry_incremental_fold():
    registry = ProjectionRegistry()
    seen = []

    def folder(state, event):
        seen.append(event.type)
        return (state or 0) + 1

    registry.register("count", folder, initial=lambda: 0)
    log = SessionLog()
    log.append(SessionEventType.USER_MESSAGE, content="a")
    events = log.events()
    assert registry.state_of("count", events) == 1
    log.append(SessionEventType.USER_MESSAGE, content="b")
    events = log.events()
    assert registry.state_of("count", events) == 2
    # 增量：仅折叠新增事件
    assert seen == [SessionEventType.USER_MESSAGE, SessionEventType.USER_MESSAGE]


def test_projection_registry_snapshot():
    registry = ProjectionRegistry()
    registry.register("n", lambda state, event: (state or 0) + 1, initial=lambda: 0)
    log = SessionLog()
    log.append(SessionEventType.USER_MESSAGE, content="a")
    assert registry.snapshot(log.events()) == {"n": 1}


def test_projection_registry_unregister():
    registry = ProjectionRegistry()
    registry.register("n", lambda state, event: state, initial=lambda: 0)
    assert registry.has("n") is True
    assert registry.unregister("n") is True
    assert registry.has("n") is False
    with pytest.raises(KeyError):
        registry.state_of("n", [])


def test_projection_registry_reset_recomputes():
    registry = ProjectionRegistry()
    registry.register("n", lambda state, event: (state or 0) + 1, initial=lambda: 0)
    log = SessionLog()
    log.append(SessionEventType.USER_MESSAGE, content="a")
    assert registry.state_of("n", log.events()) == 1
    registry.reset()
    assert registry.state_of("n", log.events()) == 1


def test_projection_registry_double_register_raises():
    registry = ProjectionRegistry()
    registry.register("n", lambda state, event: state, initial=lambda: 0)
    registry.register("n", lambda state, event: state, initial=lambda: 0)
    assert registry.names() == ["n"]
