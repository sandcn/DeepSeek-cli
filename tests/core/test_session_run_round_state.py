"""会话连续轮次状态回归测试。

覆盖用户可见错误「状态 'completed' 不允许操作 'run_round'」：
回合正常完成后状态停留在 COMPLETED（定时自动保存 30s 后才 save() 回 IDLE），
修复前紧接的下一条用户消息会抛 InvalidTransitionError；中断后的 INTERRUPTED
状态同理。本测试走完整 ChatSession.run_round 路径。
"""

from __future__ import annotations

import pytest

from src.core.internal.session import _session_lifecycle as lifecycle
from src.core.session import ChatSession


class _FakeAgent:
    """最小 Agent 桩：记录消息、可配置本次 run() 是否被中断。"""

    def __init__(self):
        self.messages = [{"role": "system", "content": "sys"}]
        self.model = "test-model"
        self._subagent_records = []
        self.interrupt_next = False

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})

    async def run(self):
        if self.interrupt_next:
            self.interrupt_next = False
            self.messages.append({"role": "assistant", "content": "partial"})
            return True
        self.messages.append({"role": "assistant", "content": "ok"})
        return False


@pytest.fixture
def session(monkeypatch):
    # 技能手势与落盘旁路：本测试只关注状态机生命周期
    monkeypatch.setattr(lifecycle, "inject_skill_gestures", lambda *a, **kw: 0)
    s = ChatSession(model="test-model", agent=_FakeAgent())
    monkeypatch.setattr(s._persistence_mgr, "start_autosave", lambda: None)
    monkeypatch.setattr(s._persistence_mgr, "save_checkpoint", lambda: None)
    return s


async def test_consecutive_run_round_after_completed(session):
    first = await session.run_round("hello")
    assert first["interrupted"] is False
    assert session.state_name == "completed"

    second = await session.run_round("again")
    assert second["interrupted"] is False
    assert session.state_name == "completed"
    assert [m["content"] for m in session.messages[-4:]] == [
        "hello", "ok", "again", "ok",
    ]


async def test_run_round_after_interrupted(session):
    session.agent.interrupt_next = True
    first = await session.run_round("hello")
    assert first["interrupted"] is True
    assert session.state_name == "interrupted"

    second = await session.run_round("continue")
    assert second["interrupted"] is False
    assert session.state_name == "completed"
    assert [m["content"] for m in session.messages[-2:]] == ["continue", "ok"]
