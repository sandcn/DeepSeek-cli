"""--load 会话恢复（会话日志路径）回归测试。

覆盖 ``SessionPersistenceManager.load`` 的两条恢复路径：

- 会话文件带 ``session_log``（当前保存格式的常规路径）→ 日志重建消息视图，
  修复前 ``messages`` 局部变量仅存在于「非日志恢复」分支，末尾
  ``self._observability.gauge("session.messages", len(messages))`` 引用未绑定
  变量抛 ``UnboundLocalError``——``chat.py --load`` 启动即崩、退出时也打印不出
  恢复命令行。
- 会话文件无 ``session_log``（旧会话）→ 仍按消息列表恢复（向后兼容）。
"""

from __future__ import annotations

from src.core.events.agent_types import SessionEventType
from src.core.internal.session._session_persistence_manager import (
    SessionPersistenceManager,
)
from src.core.session_log import LoggedMessageList, SessionLog


class _FakePersistence:
    def __init__(self, data):
        self._data = data
        self.loaded: list[str] = []

    def load_session(self, session_id):
        self.loaded.append(session_id)
        return self._data


class _FakeObservability:
    def __init__(self):
        self.gauges: list[tuple] = []

    def gauge(self, name, value):
        self.gauges.append((name, value))


class _NoTransitionStateMachine:
    """最小状态机桩：始终不允许 save 转换（load 路径不使用）。"""

    def can(self, action) -> bool:  # pragma: no cover - 防御兜底
        return False


def _make_manager(persistence_data):
    """构造 SessionPersistenceManager（日志源为 LoggedMessageList 视图）。"""
    view = LoggedMessageList(SessionLog())
    bound = {"log": view.log}

    def _rebind_log(log):
        view.rebind(log)
        bound["log"] = log

    obs = _FakeObservability()
    mgr = SessionPersistenceManager(
        messages_getter=lambda: view,
        model_getter=lambda: "m",
        model_setter=lambda v: None,
        session_id_getter=lambda: "",
        session_id_setter=lambda v: None,
        persistence_port=_FakePersistence(persistence_data),
        checkpoint_port=None,
        state_machine=_NoTransitionStateMachine(),
        emit_fn=lambda *a, **k: None,
        observability_port=obs,
        log_getter=lambda: bound["log"],
        log_setter=_rebind_log,
    )
    return mgr, view, obs


def _log_snapshot():
    log = SessionLog(session_id="s1")
    log.append(SessionEventType.SYSTEM_MESSAGE, content="sys")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.ASSISTANT_MESSAGE, content="yo")
    return log.snapshot()


def test_load_with_session_log_restores_view_and_reports_gauge():
    """带 session_log 的会话：日志路径恢复成功且不抛 UnboundLocalError。"""
    data = {
        "id": "s1",
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "subagents": [],
        "session_log": _log_snapshot(),
    }
    mgr, view, obs = _make_manager(data)

    loaded = mgr.load("s1")

    assert loaded is data
    assert [m["role"] for m in view] == ["system", "user", "assistant"]
    assert ("session.messages", 3) in obs.gauges


def test_load_without_session_log_falls_back_to_message_list():
    """无 session_log（旧会话）：仍按消息列表恢复（向后兼容）。"""
    data = {
        "id": "old",
        "model": "m",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        "subagents": [],
        "session_log": [],
    }
    mgr, view, obs = _make_manager(data)

    loaded = mgr.load("old")

    assert loaded is data
    assert [m["role"] for m in view] == ["system", "user"]
    assert ("session.messages", 2) in obs.gauges


def test_load_returns_none_for_empty_session():
    """既无消息也无日志：load 返回 None（不触发 gauge）。"""
    data = {"id": "e", "model": "m", "messages": [], "session_log": []}
    mgr, _, obs = _make_manager(data)

    assert mgr.load("e") is None
    assert obs.gauges == []
