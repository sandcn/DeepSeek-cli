"""``LoopPlugin`` 端到端编排：生成提词跨轮使用、LLM 判定停止、无限轮退出。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.commands.plugins.loop_plugin import LoopPlugin
from src.core.loop_prompt import LOOP_PROMPT_MARKER, LoopPromptService


class _FakeBottomBar:
    def __init__(self):
        self.enabled = 0
        self.disabled = 0
        self.reset = 0

    def enable_status(self):
        self.enabled += 1

    def disable_status(self):
        self.disabled += 1

    def reset_tool_count(self):
        self.reset += 1


class _FakeChatUI:
    def __init__(self):
        self.lines = []
        self.bottom_bar = _FakeBottomBar()
        self.opened = []
        self.statuses = []
        self.closed = 0

    def write_line(self, text):
        self.lines.append(str(text))

    def open_loop_floating_window(self, total, prompt, runs_per_round=1):
        self.opened.append((total, prompt, runs_per_round))

    def set_loop_status(self, **fields):
        self.statuses.append(fields)

    def close_loop_floating_window(self):
        self.closed += 1


class _FakeSession:
    def __init__(self, service, answers):
        self.loop_prompt_service = service
        self.model = "test-model"
        self.messages = []
        self.answers = list(answers)
        self.prompts = []
        self.cleared = 0
        self._service = service

    def clear_messages(self):
        self.cleared += 1
        self.messages = []

    async def run_round(self, prompt):
        self.prompts.append(prompt)
        self.messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": self.answers.pop(0)},
        ]
        # 模拟 Agent.run() 在等待后台任务前调用续跑提词钩子。
        await self._service.on_agent_round_end()
        return {"interrupted": False}


@pytest.mark.asyncio
async def test_loop_uses_generated_prompt_and_stops_on_false(monkeypatch):
    responses = [
        '{"continue": true, "prompt": "补充测试与验证"}',
        '{"continue": false, "prompt": ""}',
    ]

    def call_model(_messages):
        return responses.pop(0), ""

    ui = _FakeChatUI()

    def emit(event, **data):
        if event == "loop_prompt_generating":
            ui.set_loop_status(generating=data.get("generating"))
        elif event == "loop_prompt_generated":
            keep = data.get("should_continue") is not False and not data.get("error")
            ui.set_loop_status(
                generating=False,
                next_prompt=data.get("prompt", "") if keep else "",
            )

    service = LoopPromptService(
        messages_getter=lambda: session.messages,
        model_getter=lambda: "test-model",
        emit=emit,
        call_model=call_model,
        config_getter=lambda: {"retry_once": False},
    )
    session = _FakeSession(service, ["实现完成，还差测试", "测试已补齐"])
    loop = SimpleNamespace(
        _chat_ui=ui,
        _loop_state={},
        _force_exit=SimpleNamespace(clear=lambda: None),
    )

    async def _no_snapshot(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.app_loop._save_loop_snapshot", _no_snapshot,
    )
    monkeypatch.setattr(
        "src.core.interrupt_state.reset_interrupt_async", lambda *a, **k: None,
    )
    monkeypatch.setattr("src.core.stats.reset_token_speed", lambda *a, **k: None)

    plugin = LoopPlugin()
    plugin.bind_loop(loop)
    handled = await plugin.async_execute(
        SimpleNamespace(session=session, arg="5 实现功能"),
    )

    assert handled is True
    assert session.cleared == 1
    assert session.prompts[0] == "实现功能"
    assert session.prompts[1] == f"{LOOP_PROMPT_MARKER}补充测试与验证"
    assert len(session.prompts) == 2  # LLM 判定 false 后不再运行
    assert loop._loop_state["_loop_mode"] is False
    assert ui.opened == [(5, "实现功能", 1)]
    assert ui.closed == 1
    assert ui.bottom_bar.enabled == 1
    assert ui.bottom_bar.disabled == 1
    assert any("LLM 判断无需继续" in line for line in ui.lines)
    assert any("下次提词" in line for line in ui.lines)
    # 生成期间浮窗收到 generating=True；生成结果写入下一轮提词。
    assert any(item.get("generating") is True for item in ui.statuses)
    assert any(item.get("next_prompt") == "补充测试与验证" for item in ui.statuses)


@pytest.mark.asyncio
async def test_loop_infinite_stops_on_pending_user_input(monkeypatch):
    responses = ['{"continue": true, "prompt": "继续"}'] * 5

    def call_model(_messages):
        return responses.pop(0), ""

    ui = _FakeChatUI()

    def emit(event, **data):
        if event == "loop_prompt_generating":
            ui.set_loop_status(generating=data.get("generating"))
        elif event == "loop_prompt_generated":
            keep = data.get("should_continue") is not False and not data.get("error")
            ui.set_loop_status(
                generating=False,
                next_prompt=data.get("prompt", "") if keep else "",
            )

    service = LoopPromptService(
        messages_getter=lambda: session.messages,
        model_getter=lambda: "test-model",
        emit=emit,
        call_model=call_model,
        config_getter=lambda: {"retry_once": False},
    )
    session = _FakeSession(service, ["一", "二", "三", "四", "五"])
    loop_state = {}
    loop = SimpleNamespace(
        _chat_ui=ui,
        _loop_state=loop_state,
        _force_exit=SimpleNamespace(clear=lambda: None),
    )

    async def _no_snapshot(*_args, **_kwargs):
        return None

    monkeypatch.setattr("src.app_loop._save_loop_snapshot", _no_snapshot)
    monkeypatch.setattr(
        "src.core.interrupt_state.reset_interrupt_async", lambda *a, **k: None,
    )
    monkeypatch.setattr("src.core.stats.reset_token_speed", lambda *a, **k: None)

    plugin = LoopPlugin()
    plugin.bind_loop(loop)
    # 第一次运行后模拟用户输入 → 第二轮开头检测到并退出。
    original_run = session.run_round

    async def run_and_queue(prompt):
        result = await original_run(prompt)
        if len(session.prompts) == 1:
            loop_state["queued_input"] = "用户新消息"
        return result

    session.run_round = run_and_queue  # type: ignore[assignment]

    await plugin.async_execute(SimpleNamespace(session=session, arg="实现功能"))

    assert len(session.prompts) == 1
    assert any("检测到用户输入" in line for line in ui.lines)
    assert ui.opened[0][0] == 0  # total=0 表示无限
    assert ui.closed == 1
