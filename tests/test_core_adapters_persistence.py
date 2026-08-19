"""src/core/adapters/persistence — JsonFilePersistence / JsonFileCheckpoint 单元测试。

两个适配器均为延迟导入包装器（方法体内 ``from ...chat_msgs/checkpoint import``），
测试用 monkeypatch 替换底层模块函数，验证透传与返回值。
"""

from __future__ import annotations

import pytest

from src.core.adapters.persistence import JsonFileCheckpoint, JsonFilePersistence


# ── JsonFilePersistence：透传到 chat_msgs ─────────────────

@pytest.fixture
def fake_chat_msgs(monkeypatch):
    """替换 src.chat_msgs 的会话持久化函数，记录调用并返回固定值。"""
    calls = {}

    import src.chat_msgs as chat_msgs_mod

    def _save(messages, model, session_id=None, subagents=None):
        calls["save"] = (messages, model, session_id, subagents)
        return "sid-1"

    def _load(session_id):
        calls["load"] = session_id
        return {"id": session_id, "messages": []}

    def _list():
        calls["list"] = True
        return [{"id": "a"}, {"id": "b"}]

    def _delete(session_id):
        calls["delete"] = session_id
        return True

    def _recover(session_id):
        calls["recover"] = session_id
        return "/resume " + session_id

    def _gen():
        calls["gen"] = True
        return "gen-42"

    monkeypatch.setattr(chat_msgs_mod, "save_session", _save)
    monkeypatch.setattr(chat_msgs_mod, "load_session", _load)
    monkeypatch.setattr(chat_msgs_mod, "list_sessions", _list)
    monkeypatch.setattr(chat_msgs_mod, "delete_session", _delete)
    monkeypatch.setattr(chat_msgs_mod, "get_recover_cmd", _recover)
    monkeypatch.setattr(chat_msgs_mod, "generate_id", _gen)
    return calls


def test_persistence_save_session(fake_chat_msgs):
    p = JsonFilePersistence()
    sid = p.save_session([{"role": "user", "content": "hi"}], "m", "sid-9", [1])
    assert sid == "sid-1"
    msgs, model, session_id, subagents = fake_chat_msgs["save"]
    assert msgs == [{"role": "user", "content": "hi"}]
    assert model == "m"
    assert session_id == "sid-9"
    assert subagents == [1]


def test_persistence_load_session(fake_chat_msgs):
    p = JsonFilePersistence()
    data = p.load_session("sid-2")
    assert data == {"id": "sid-2", "messages": []}
    assert fake_chat_msgs["load"] == "sid-2"


def test_persistence_list_sessions(fake_chat_msgs):
    p = JsonFilePersistence()
    assert p.list_sessions() == [{"id": "a"}, {"id": "b"}]


def test_persistence_delete_session(fake_chat_msgs):
    p = JsonFilePersistence()
    assert p.delete_session("sid-3") is True
    assert fake_chat_msgs["delete"] == "sid-3"


def test_persistence_get_recover_cmd(fake_chat_msgs):
    p = JsonFilePersistence()
    assert p.get_recover_cmd("sid-4") == "/resume sid-4"
    assert fake_chat_msgs["recover"] == "sid-4"


def test_persistence_generate_id(fake_chat_msgs):
    assert JsonFilePersistence.generate_id() == "gen-42"


# ── JsonFileCheckpoint：透传到 checkpoint ─────────────────

@pytest.fixture
def fake_checkpoint(monkeypatch):
    """替换 src.checkpoint 的断点函数，记录调用。"""
    calls = {}

    import src.checkpoint as ckpt_mod

    def _save(messages, model, task_description=""):
        calls["save"] = (messages, model, task_description)

    def _load():
        calls["load"] = True
        return {"messages": []}

    def _clear():
        calls["clear"] = True

    def _has():
        calls["has"] = True
        return True

    def _info():
        calls["info"] = True
        return {"message_count": 1}

    monkeypatch.setattr(ckpt_mod, "save_checkpoint", _save)
    monkeypatch.setattr(ckpt_mod, "load_checkpoint", _load)
    monkeypatch.setattr(ckpt_mod, "clear_checkpoint", _clear)
    monkeypatch.setattr(ckpt_mod, "has_checkpoint", _has)
    monkeypatch.setattr(ckpt_mod, "get_checkpoint_info", _info)
    return calls


def test_checkpoint_save(fake_checkpoint):
    c = JsonFileCheckpoint()
    c.save([{"role": "user", "content": "x"}], "m", "任务")
    msgs, model, desc = fake_checkpoint["save"]
    assert msgs == [{"role": "user", "content": "x"}]
    assert model == "m"
    assert desc == "任务"


def test_checkpoint_load(fake_checkpoint):
    c = JsonFileCheckpoint()
    assert c.load() == {"messages": []}
    assert fake_checkpoint["load"] is True


def test_checkpoint_clear(fake_checkpoint):
    JsonFileCheckpoint().clear()
    assert fake_checkpoint["clear"] is True


def test_checkpoint_exists(fake_checkpoint):
    assert JsonFileCheckpoint().exists() is True


def test_checkpoint_get_info(fake_checkpoint):
    assert JsonFileCheckpoint().get_info() == {"message_count": 1}
    assert fake_checkpoint["info"] is True
