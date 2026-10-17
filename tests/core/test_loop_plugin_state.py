"""LoopPlugin 连续 run_round 状态回归测试。

复现用户可见错误「状态 'completed' 不允许操作 'run_round'」：
``/loop 1 ls`` 每轮连续调用两次 ``session.run_round``（第 2 次使用固定
提词「继续完成所有」）。第一次正常完成后会话状态停留在 COMPLETED
（30s 定时自动保存触发后才经 save() 回到 IDLE），修复前第二次调用直接
抛 InvalidTransitionError，导致 /loop 命令必定失败。

本测试走 LoopPlugin 完整执行路径（含每轮 clear_messages），不依赖网络。
"""

from __future__ import annotations

import pytest

from src.core.commands.plugins.loop_plugin import LoopPlugin
from src.core.internal.commands._command_core import CommandContext
from src.core.internal.session import _session_lifecycle as lifecycle
from src.core.session import ChatSession


class _FakeAgent:
    """最小 Agent 桩：记录消息，run() 正常返回未中断。"""

    def __init__(self):
        self.messages = [{"role": "system", "content": "sys"}]
        self.model = "test-model"
        self._subagent_records = []

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})

    def build_system_prompt(self):
        return ["sys"]

    async def run(self):
        self.messages.append({"role": "assistant", "content": "ok"})
        return False


class _FakeBottomBar:
    def __init__(self):
        self.status_enabled = False
        self.tool_count_reset = 0

    def enable_status(self):
        self.status_enabled = True

    def disable_status(self):
        self.status_enabled = False

    def reset_tool_count(self):
        self.tool_count_reset += 1


class _FakeChatUI:
    def __init__(self):
        self.lines: list[str] = []
        self.bottom_bar = _FakeBottomBar()

    def write_line(self, text):
        self.lines.append(text)


class _FakeLoop:
    def __init__(self):
        self._chat_ui = _FakeChatUI()
        self._loop_state: dict = {}
        self._force_exit = set()

    @property
    def chat_ui(self):
        return self._chat_ui


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(lifecycle, "inject_skill_gestures", lambda *a, **kw: 0)
    s = ChatSession(model="test-model", agent=_FakeAgent())
    monkeypatch.setattr(s._persistence_mgr, "start_autosave", lambda: None)
    monkeypatch.setattr(s._persistence_mgr, "save_checkpoint", lambda: None)
    return s


@pytest.fixture
def loop_plugin(monkeypatch):
    async def _noop_snapshot(*args, **kwargs):
        return None

    monkeypatch.setattr("src.app_loop._save_loop_snapshot", _noop_snapshot)
    monkeypatch.setattr("src.core.interrupt_state.reset_interrupt_async", lambda *a, **kw: None)
    monkeypatch.setattr("src.core.stats.reset_token_speed", lambda *a, **kw: None)

    fake_loop = _FakeLoop()
    plugin = LoopPlugin()
    plugin.bind_loop(fake_loop)
    return plugin, fake_loop


def _make_ctx(session: ChatSession, arg: str) -> CommandContext:
    return CommandContext(
        messages=session.messages,
        state={},
        arg=arg,
        build_system_prompt=session.agent.build_system_prompt,
        get_user_input=lambda prompt="": "",
        context_manager=session.context_manager,
        session=session,
    )


@pytest.mark.parametrize("count", [1, 2])
async def test_loop_command_consecutive_rounds_after_completed(
    session, loop_plugin, count,
):
    """``/loop N ls`` 每轮的两次 run_round 均能连续执行（不再报状态错误）。"""
    plugin, fake_loop = loop_plugin

    handled = await plugin.async_execute(_make_ctx(session, f"{count} ls"))

    assert handled is True
    # 每轮开始时 clear_messages 清空上一轮内容，最终只保留最后一轮：
    # run_round("ls") + run_round("继续完成所有") 各产生一条 user + 一条
    # assistant 消息。count=2 时通过执行日志确认两轮都真实跑过。
    non_system = [m for m in session.messages if m["role"] != "system"]
    assert [m["content"] for m in non_system] == ["ls", "ok", "继续完成所有", "ok"]
    for round_no in range(1, count + 1):
        assert any(f"第 {round_no}/{count} 轮 · 第1次" in line
                   for line in fake_loop._chat_ui.lines)
    assert session.state_name == "completed"
    assert fake_loop._loop_state["_loop_mode"] is False
    assert fake_loop._chat_ui.bottom_bar.status_enabled is False


async def test_loop_command_survives_completed_state_without_clear(
    session, loop_plugin,
):
    """即使上一轮残留 COMPLETED 状态，/loop 首次 run_round 也能正常开始。"""
    plugin, _ = loop_plugin

    # 模拟上一轮正常结束、30s 自动保存尚未触发（状态停留 COMPLETED）。
    first = await session.run_round("上一轮")
    assert first["interrupted"] is False
    assert session.state_name == "completed"

    handled = await plugin.async_execute(_make_ctx(session, "1 ls"))

    assert handled is True
    assert session.state_name == "completed"
    # /loop 首轮开始会 clear_messages，上一轮内容被清空后重新执行两次。
    non_system = [m for m in session.messages if m["role"] != "system"]
    assert [m["content"] for m in non_system] == ["ls", "ok", "继续完成所有", "ok"]
