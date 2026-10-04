"""会话日志持久化测试 — 会话日志作为持久事实源（保存 / 恢复 / fork）。

覆盖：
- chat_msgs.save_session 写入 session_log 快照、load_session 归一化；
- ChatSession.save 后可从日志恢复消息视图；
- SessionLog.restore + LoggedMessageList.rebind 的回放语义；
- 旧会话（无 session_log）仍按消息列表恢复（向后兼容）。
"""

from __future__ import annotations

import json

import pytest

from src.core.events.agent_types import SessionEventType
from src.core.session_log import LoggedMessageList, SessionLog


def test_save_session_persists_log(tmp_path, monkeypatch):
    import src.chat_msgs as chat_msgs

    monkeypatch.setattr(chat_msgs, "CHAT_MSGS_DIR", tmp_path)
    log = SessionLog(session_id="s1")
    log.append(SessionEventType.SYSTEM_MESSAGE, content="sys")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.ASSISTANT_MESSAGE, content="yo")

    sid = chat_msgs.save_session(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        "deepseek-v4",
        None,
        None,
        log.snapshot(),
    )
    raw = json.loads((tmp_path / f"{sid}.json").read_text(encoding="utf-8"))
    assert raw["session_log"]
    assert [event["type"] for event in raw["session_log"]][:3] == [
        SessionEventType.SYSTEM_MESSAGE,
        SessionEventType.USER_MESSAGE,
        SessionEventType.ASSISTANT_MESSAGE,
    ]


def test_load_session_normalizes_missing_log(tmp_path, monkeypatch):
    import src.chat_msgs as chat_msgs

    monkeypatch.setattr(chat_msgs, "CHAT_MSGS_DIR", tmp_path)
    sid = chat_msgs.save_session([{"role": "user", "content": "hi"}], "m")
    data = chat_msgs.load_session(sid)
    assert data["session_log"] == []


def test_restore_log_then_rebind_view():
    log = SessionLog(session_id="s1")
    log.append(SessionEventType.SYSTEM_MESSAGE, content="sys")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    snapshot = log.snapshot()

    restored = SessionLog.restore(snapshot, session_id="s1")
    view = LoggedMessageList(SessionLog())
    view.rebind(restored)
    assert [m["content"] for m in view] == ["sys", "hi"]
    assert view.verify() is True
    assert view.log.session_id == "s1"


def test_restore_from_legacy_messages_still_works():
    view = LoggedMessageList(initial=[{"role": "system", "content": "sys"}])
    for msg in [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]:
        view.append(msg)
    assert view.verify() is True
    assert [m["role"] for m in view] == ["system", "user", "assistant"]
