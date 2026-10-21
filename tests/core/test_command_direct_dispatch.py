"""命令直连分发（2026-10-22 用户需求）回归测试。

覆盖：
  - ``CommandMeta.queue_only`` / ``direct_during_round`` 声明；
  - ``InteractiveLoop._should_direct_command`` 在空闲 / 生成中的分级判定；
  - ``InputBufferEditor._enter`` 的 Enter 拦截（命令不进入排队输入）；
  - ``_handle_round`` 空闲时命令内联直连、普通消息仍走 MessageQueue；
  - ``_cli_msg_consumer`` 处理期间 ``_round_active`` 置位 / 复位；
  - render 线程直连回调仅在 ``_round_active`` 期间接管只读命令。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app_loop._loop import InteractiveLoop
from src.app_loop._session_setup import SessionState
from src.tui._input_buffer import InputBufferEditor


# ── fakes ──────────────────────────────────────────────


class _FakeHistoryIO:
    def append(self, text):  # noqa: D401 - 测试桩
        return True

    def read(self):
        return "", False

    def compact(self):
        return True


class _FakeBottomBar:
    def __init__(self):
        self.models = []

    def set_model_name(self, name):
        self.models.append(name)

    def ensure_cursor_in_lower(self):
        pass


class _FakeInput:
    def __init__(self):
        self.direct_cb = None

    def set_direct_submit_callback(self, cb):
        self.direct_cb = cb


class _FakeUI:
    def __init__(self, user_input: str = "/help"):
        self.input = _FakeInput()
        self.bottom_bar = _FakeBottomBar()
        self._user_input = user_input
        self.flushed = 0
        self.lines = []
        self.user_messages = []

    def flush(self):
        self.flushed += 1

    def write_line(self, text):
        self.lines.append(str(text))

    def on_user_message(self, text):
        self.user_messages.append(str(text))

    def wait_for_user_input(self, monitor, prefill="", input_=None):
        return self._user_input


class _FakeSession:
    def __init__(self):
        self.captured_prefill = ""
        self.retry_pending = False
        self.messages = []
        self.model = "test-model"
        self.pending_messages = []
        self.agent = SimpleNamespace(build_system_prompt=lambda: "system")

    def pop_pending_messages(self):
        self.pending_messages = []


def _make_loop(user_input: str = "/help") -> InteractiveLoop:
    loop = InteractiveLoop()
    loop._chat_ui = _FakeUI(user_input)
    return loop


# ── CommandMeta 声明 ───────────────────────────────────


def test_command_meta_declares_direct_policy():
    from src.core.commands._config_cmd import CostCommand, ThemeCommand
    from src.core.commands._data_cmd import HelpCommand, LoadCommand
    from src.core.commands._export_cmd import ExportCommand
    from src.core.commands._search_cmd import SearchCommand
    from src.core.commands._session_cmd import ClearCommand, RetryCommand
    from src.core.commands._usage_cmd import UsageCommand
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin
    from src.core.commands.plugins.model_plugin import ModelPlugin

    # 只读展示类：生成中可直连
    for cls in (HelpCommand, CostCommand, UsageCommand):
        assert cls().meta.direct_during_round is True, cls
        assert cls().meta.queue_only is False, cls

    # 队列例外：始终排队
    for cls in (
        ClearCommand, RetryCommand, LoadCommand, EditmsgPlugin, ModelPlugin,
        ThemeCommand, ExportCommand, SearchCommand,
    ):
        assert cls().meta.queue_only is True, cls
        assert cls().meta.direct_during_round is False, cls


# ── 策略判定 ───────────────────────────────────────────


def _install_meta(monkeypatch, loop, metas):
    monkeypatch.setattr(
        loop, "_command_meta",
        lambda name: metas.get(name),
    )


def test_should_direct_command_idle_vs_busy(monkeypatch):
    loop = InteractiveLoop()
    _install_meta(monkeypatch, loop, {
        "help": SimpleNamespace(queue_only=False, direct_during_round=True),
        "clear": SimpleNamespace(queue_only=True, direct_during_round=False),
        "sessions": SimpleNamespace(queue_only=False, direct_during_round=False),
    })

    loop._round_active = False
    assert loop._should_direct_command("/help") is True
    assert loop._should_direct_command("/sessions") is True
    assert loop._should_direct_command("/clear") is False
    assert loop._should_direct_command("/unknown") is True
    assert loop._should_direct_command("hello") is False

    loop._round_active = True
    assert loop._should_direct_command("/help") is True
    assert loop._should_direct_command("/sessions") is False
    assert loop._should_direct_command("/clear") is False
    assert loop._should_direct_command("/unknown") is True


# ── Enter 拦截 ─────────────────────────────────────────


def _make_editor():
    return InputBufferEditor(
        history_file=Path("unused-history"),
        history_io=_FakeHistoryIO(),
    )


def test_enter_intercepts_direct_command():
    editor = _make_editor()
    seen = []
    history = []

    def cb(text):
        seen.append(text)
        return True

    editor.set_direct_submit_callback(cb)
    editor.set_buffer("/help")
    editor._enter(append_history=history.append)

    assert seen == ["/help"]
    assert history == ["/help"]
    assert editor.has_queued_input() is False
    assert editor.get_current_text() == ""
    assert editor.get_queued_input() is None


def test_enter_keeps_queue_for_non_direct_command():
    editor = _make_editor()
    editor.set_direct_submit_callback(lambda text: False)
    editor.set_buffer("/clear")
    editor._enter(append_history=lambda _text: None)

    assert editor.has_queued_input() is True
    assert editor.get_queued_input() == "/clear"


def test_enter_does_not_intercept_plain_message():
    editor = _make_editor()
    called = []
    editor.set_direct_submit_callback(lambda text: called.append(text) or True)
    editor.set_buffer("hello")
    editor._enter(append_history=lambda _text: None)

    assert called == []
    assert editor.has_queued_input() is True
    assert editor.get_queued_input() == "hello"


def test_enter_callback_exception_falls_back_to_queue():
    editor = _make_editor()

    def cb(text):
        raise RuntimeError("boom")

    editor.set_direct_submit_callback(cb)
    editor.set_buffer("/help")
    editor._enter(append_history=lambda _text: None)

    assert editor.has_queued_input() is True
    assert editor.get_queued_input() == "/help"


# ── 主循环路由 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_round_dispatches_idle_command_directly(monkeypatch):
    loop = _make_loop(user_input="/help")
    session = _FakeSession()
    state = SessionState(model="test-model")
    seen = []

    async def fake_handle(content, sess, st, *, reset_interrupt=True):
        seen.append((content, reset_interrupt))

    async def forbidden_put(*args, **kwargs):
        raise AssertionError("直连命令不应进入 MessageQueue")

    monkeypatch.setattr(loop, "_handle_command_msg", fake_handle)
    monkeypatch.setattr(loop, "_should_direct_command", lambda text: True)
    monkeypatch.setattr("src.app_loop._loop._put_and_wait", forbidden_put)
    monkeypatch.setattr("src.app_loop._loop.reset_interrupt_async", lambda **kw: None)

    from src.core.message_queue import MessageQueue

    result = await loop._handle_round(
        session, state, MessageQueue(), asyncio.Event(),
    )

    assert result.should_exit is False
    assert seen == [("/help", False)]


@pytest.mark.asyncio
async def test_handle_round_queues_plain_message(monkeypatch):
    loop = _make_loop(user_input="hello")
    session = _FakeSession()
    state = SessionState(model="test-model")
    calls = []

    async def fake_put(queue, msg, msg_done):
        calls.append(msg)

    monkeypatch.setattr(loop, "_should_direct_command", lambda text: False)
    monkeypatch.setattr("src.app_loop._loop._put_and_wait", fake_put)
    monkeypatch.setattr("src.app_loop._loop.reset_interrupt_async", lambda **kw: None)

    from src.core.message_queue import MessageQueue

    result = await loop._handle_round(
        session, state, MessageQueue(), asyncio.Event(),
    )

    assert result.should_exit is False
    assert calls == ["hello"]


# ── 消费者 / 直连回调 ─────────────────────────────────


@pytest.mark.asyncio
async def test_cli_msg_consumer_marks_round_active(monkeypatch):
    loop = InteractiveLoop()
    session = _FakeSession()
    state = SessionState(model="test-model")
    flags = []

    async def fake_handle(content, sess, st, *, reset_interrupt=True):
        flags.append(loop._round_active)

    loop._handle_command_msg = fake_handle
    msg = SimpleNamespace(content="/help")
    msg_done = asyncio.Event()

    await loop._cli_msg_consumer(msg, session, state, msg_done)

    assert flags == [True]
    assert loop._round_active is False
    assert msg_done.is_set()


@pytest.mark.asyncio
async def test_direct_submit_callback_only_during_round(monkeypatch):
    loop = InteractiveLoop()
    loop._loop = asyncio.get_running_loop()
    scheduled = []
    metas = {
        "help": SimpleNamespace(queue_only=False, direct_during_round=True),
        "clear": SimpleNamespace(queue_only=True, direct_during_round=False),
    }
    _install_meta(monkeypatch, loop, metas)
    monkeypatch.setattr(
        loop, "_schedule_direct_command",
        lambda text, session, state: scheduled.append(text),
    )
    cb = loop._make_direct_submit_callback(_FakeSession(), SessionState())

    # 空闲：不在 render 线程拦截（由 _handle_round 内联直连）
    assert cb("/help") is False
    assert scheduled == []

    # 生成中：只读命令接管；队列例外命令不接管
    loop._round_active = True
    loop._generation_active = True
    assert cb("/help") is True
    assert cb("/clear") is False
    await asyncio.sleep(0)
    assert scheduled == ["/help"]


@pytest.mark.asyncio
async def test_command_active_depth_restored(monkeypatch):
    loop = _make_loop(user_input="/nope")
    session = _FakeSession()
    state = SessionState(model="test-model")
    monkeypatch.setattr("src.app_loop._loop.reset_interrupt_async", lambda **kw: None)

    await loop._handle_command_msg("/nope", session, state)

    assert loop._command_active_depth == 0
    assert any("未知命令" in line for line in loop._ui.lines) is True
    # 命令执行本身不负责回显（回显在 Enter 时由回调完成，避免重复）
    assert loop._ui.user_messages == []


@pytest.mark.asyncio
async def test_direct_submit_callback_echoes_command_once(monkeypatch):
    loop = _make_loop()
    loop._loop = asyncio.get_running_loop()
    scheduled = []
    metas = {
        "help": SimpleNamespace(queue_only=False, direct_during_round=True),
        "clear": SimpleNamespace(queue_only=True, direct_during_round=False),
    }
    _install_meta(monkeypatch, loop, metas)
    monkeypatch.setattr(
        loop, "_schedule_direct_command",
        lambda text, session, state: scheduled.append(text),
    )
    cb = loop._make_direct_submit_callback(_FakeSession(), SessionState())

    # 空闲：回显 + 不拦截（由 _handle_round 内联直连）
    assert cb("/help") is False
    assert loop._ui.user_messages == ["/help"]
    assert scheduled == []

    # 生成中：回显 + 只读命令接管；队列例外命令只回显不接管
    loop._generation_active = True
    assert cb("/help") is True
    assert cb("/clear") is False
    assert loop._ui.user_messages == ["/help", "/help", "/clear"]
    await asyncio.sleep(0)
    assert scheduled == ["/help"]


@pytest.mark.asyncio
async def test_handle_round_echoes_exit(monkeypatch):
    loop = _make_loop(user_input="exit")
    session = _FakeSession()
    state = SessionState(model="test-model")
    monkeypatch.setattr("src.app_loop._loop.reset_interrupt_async", lambda **kw: None)

    from src.core.message_queue import MessageQueue

    result = await loop._handle_round(
        session, state, MessageQueue(), asyncio.Event(),
    )

    assert result.should_exit is True
    assert loop._ui.user_messages == ["exit"]
    assert loop._ui.flushed >= 1


@pytest.mark.asyncio
async def test_handle_round_echoes_queued_exit(monkeypatch):
    loop = _make_loop(user_input="/help")
    session = _FakeSession()
    state = SessionState(model="test-model")
    loop._loop_state["queued_input"] = "exit"
    monkeypatch.setattr("src.app_loop._loop.reset_interrupt_async", lambda **kw: None)

    from src.core.message_queue import MessageQueue

    result = await loop._handle_round(
        session, state, MessageQueue(), asyncio.Event(),
    )

    assert result.should_exit is True
    assert loop._ui.user_messages == ["exit"]
    assert loop._ui.flushed >= 1
