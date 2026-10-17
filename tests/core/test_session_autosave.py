"""会话定时自动保存测试 — 30s 周期、仅内容变化时异步落盘、含会话日志快照。"""

from __future__ import annotations

import asyncio
import json

from src.core.events.agent_types import SessionEventType
from src.core.internal.session import _session_persistence_manager as persistence_mod
from src.core.internal.session._session_persistence_manager import (
    SessionPersistenceManager,
)
from src.core.session_log import LoggedMessageList, SessionLog


class _FakePersistence:
    def __init__(self):
        self.calls: list[dict] = []

    def save_session(self, messages, model, session_id=None,
                     subagents=None, session_log=None):
        self.calls.append({
            "messages": messages,
            "model": model,
            "session_id": session_id,
            "subagents": subagents,
            "session_log": session_log,
        })
        return session_id or "saved-session"

    def load_session(self, session_id):
        return None

    def list_sessions(self):
        return []


class _FakeStateMachine:
    def can(self, action):
        return False


class _FakeObservability:
    def gauge(self, name, value):
        pass


def _make_manager(messages, *, log=None, persistence=None, subagents=None):
    persistence = persistence or _FakePersistence()
    manager = SessionPersistenceManager(
        messages_getter=lambda: messages,
        model_getter=lambda: "test-model",
        model_setter=lambda value: None,
        session_id_getter=lambda: "session-1",
        session_id_setter=lambda value: None,
        persistence_port=persistence,
        checkpoint_port=None,
        state_machine=_FakeStateMachine(),
        emit_fn=lambda *args, **kwargs: None,
        observability_port=_FakeObservability(),
        subagents_getter=lambda: list(subagents or []),
        subagents_setter=lambda value: None,
        log_getter=(lambda: log) if log is not None else None,
        log_setter=lambda value: None,
    )
    return manager, persistence


async def _wait_until(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def test_save_persists_session_log():
    messages = [{"role": "user", "content": "hi"}]
    log = SessionLog(session_id="session-1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    manager, persistence = _make_manager(messages, log=log)

    assert manager.save() == "session-1"

    assert len(persistence.calls) == 1
    snapshot = persistence.calls[0]["session_log"]
    assert snapshot is not None
    assert [event["type"] for event in snapshot] == [SessionEventType.USER_MESSAGE]
    assert snapshot[0]["data"]["content"] == "hi"


def test_chat_session_save_persists_session_log():
    import types

    from src.core.session import ChatSession

    log = SessionLog(session_id="session-1")
    messages = LoggedMessageList(
        log, initial=[{"role": "user", "content": "hi"}],
    )
    agent = types.SimpleNamespace(messages=messages)
    persistence = _FakePersistence()
    session = ChatSession(agent=agent, persistence_port=persistence)

    assert session.save() == "saved-session"
    snapshot = persistence.calls[0]["session_log"]
    assert snapshot is not None
    assert [event["type"] for event in snapshot] == [SessionEventType.USER_MESSAGE]
    assert session.snapshot_session_log() == snapshot


async def test_auto_save_only_when_content_changed():
    messages = [{"role": "user", "content": "hi"}]
    manager, persistence = _make_manager(messages)

    assert await manager.auto_save() == "session-1"
    assert len(persistence.calls) == 1
    assert persistence.calls[0]["session_log"] is None

    assert await manager.auto_save() is None
    assert len(persistence.calls) == 1

    messages.append({"role": "assistant", "content": "hello"})
    assert await manager.auto_save() == "session-1"
    assert len(persistence.calls) == 2


async def test_auto_save_detects_changes_via_session_log():
    log = SessionLog(session_id="session-1")
    messages = LoggedMessageList(log, initial=[{"role": "user", "content": "hi"}])
    manager, persistence = _make_manager(messages, log=log)

    assert await manager.auto_save() == "session-1"
    assert await manager.auto_save() is None

    messages.append({"role": "assistant", "content": "hello"})
    assert await manager.auto_save() == "session-1"
    assert len(persistence.calls) == 2
    assert len(persistence.calls[0]["session_log"]) == 1
    assert [event["type"] for event in persistence.calls[1]["session_log"]] == [
        SessionEventType.USER_MESSAGE,
        SessionEventType.ASSISTANT_MESSAGE,
    ]


async def test_periodic_autosave_every_interval_only_on_change(monkeypatch):
    monkeypatch.setattr(persistence_mod, "_AUTOSAVE_INTERVAL_SECONDS", 0.02)
    messages = [{"role": "user", "content": "hi"}]
    manager, persistence = _make_manager(messages)

    manager.start_autosave()
    task = manager._autosave_task
    manager.start_autosave()
    assert manager._autosave_task is task

    assert await _wait_until(lambda: len(persistence.calls) >= 1)
    first_calls = len(persistence.calls)

    await asyncio.sleep(0.06)
    assert len(persistence.calls) == first_calls

    messages.append({"role": "assistant", "content": "hello"})
    assert await _wait_until(lambda: len(persistence.calls) > first_calls)

    await manager.stop_autosave_async()
    assert manager._autosave_task is None


async def test_autosave_stop_cancels_task(monkeypatch):
    monkeypatch.setattr(persistence_mod, "_AUTOSAVE_INTERVAL_SECONDS", 10.0)
    manager, _persistence = _make_manager([{"role": "user", "content": "hi"}])

    manager.start_autosave()
    task = manager._autosave_task
    assert task is not None and not task.done()

    await manager.stop_autosave_async()

    assert task.cancelled()
    assert manager._autosave_task is None


async def test_periodic_autosave_persists_session_log(monkeypatch):
    """30s 周期任务落盘时携带会话日志快照（内容变化后才保存）。"""
    monkeypatch.setattr(persistence_mod, "_AUTOSAVE_INTERVAL_SECONDS", 0.02)
    log = SessionLog(session_id="session-1")
    messages = LoggedMessageList(
        log, initial=[{"role": "system", "content": "sys"},
                      {"role": "user", "content": "hi"}],
    )
    manager, persistence = _make_manager(messages, log=log)

    manager.start_autosave()
    assert await _wait_until(lambda: len(persistence.calls) >= 1)
    first = persistence.calls[0]["session_log"]
    assert [event["type"] for event in first] == [
        SessionEventType.SYSTEM_MESSAGE,
        SessionEventType.USER_MESSAGE,
    ]

    calls_after_first = len(persistence.calls)
    await asyncio.sleep(0.06)
    assert len(persistence.calls) == calls_after_first

    messages.append({"role": "assistant", "content": "hello"})
    assert await _wait_until(lambda: len(persistence.calls) > calls_after_first)
    assert len(persistence.calls[-1]["session_log"]) == 3

    await manager.stop_autosave_async()


async def test_auto_save_at_round_end_does_not_write_immediately():
    """回合结束只确保定时任务运行，不立即同步写盘（不在每次工具/回答后保存）。"""
    import types

    from src.core.internal.session import _session_lifecycle as lifecycle

    class _Manager:
        def __init__(self):
            self.started = 0

        def start_autosave(self):
            self.started += 1

    manager = _Manager()
    session = types.SimpleNamespace(
        _persistence_mgr=manager, session_id="session-1",
    )

    assert await lifecycle._auto_save(session) == "session-1"
    assert manager.started == 1


async def test_loop_snapshot_passes_session_log(monkeypatch):
    from src.app_loop import _utils

    captured: dict = {}

    def fake_save(messages, model, session_id=None, subagents=None,
                  session_log=None):
        captured.update({
            "messages": list(messages),
            "model": model,
            "session_log": session_log,
        })
        return "loopsession123"

    class _Session:
        messages = [{"role": "user", "content": "hi"}]
        model = "m"

        def snapshot_session_log(self):
            return [{"type": SessionEventType.USER_MESSAGE, "seq": 1,
                     "timestamp": 0.0, "data": {"content": "hi"}}]

    monkeypatch.setattr(_utils, "save_session", fake_save)
    await _utils._save_loop_snapshot(_Session())

    assert captured["model"] == "m"
    assert captured["session_log"] == [
        {"type": SessionEventType.USER_MESSAGE, "seq": 1,
         "timestamp": 0.0, "data": {"content": "hi"}},
    ]


def test_save_session_omits_session_log_field_when_not_provided(tmp_path, monkeypatch):
    import src.chat_msgs as chat_msgs

    monkeypatch.setattr(chat_msgs, "CHAT_MSGS_DIR", tmp_path)
    sid = chat_msgs.save_session([{"role": "user", "content": "hi"}], "m")
    raw = json.loads((tmp_path / f"{sid}.json").read_text(encoding="utf-8"))
    assert "session_log" not in raw

    loaded = chat_msgs.load_session(sid)
    assert loaded["session_log"] == []
