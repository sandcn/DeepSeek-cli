"""ClawBot TUI 扫码等待期间输入保留测试（2026-08-18 输入闪没修复）。

根因：修复前 ``EscapeMonitor.start()`` 在登录完成后才调用，其内部
``input.reset()`` + ``echo("")`` 会把扫码等待期间用户已输入的字符清空——
用户看到「输入一个字符，下一帧闪没了」。修复后 ``monitor.start()`` 提前到
登录**之前**（登录前 buffer 本为空，reset 无副作用），登录完成后不再调用
monitor.start()，扫码期间输入的内容保留到主界面继续编辑。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.clawbot.runner import ClawBotRunner
from src.api.escape_monitor import EscapeMonitor
from src.tui._input import Input


class FakeBottomBar:
    def set_model_name(self, name: str) -> None:
        pass

    def setup_bottom_bar(self) -> None:
        pass

    def ensure_cursor_in_lower(self) -> None:
        pass

    def enable_status(self) -> None:
        pass

    def disable_status(self) -> None:
        pass

    def get_status_elapsed(self) -> float:
        return 0.0


class FakeChatUI:
    """最小 ChatUIConsumer 替身（覆盖 _run_tui 调用面）。"""

    def __init__(self, inputs=None):
        self._inputs = list(inputs or ["exit"])
        self.bottom_bar = FakeBottomBar()
        self._input = Input(fd=0, history_file=Path(tempfile.mktemp()))
        self._components = SimpleNamespace(input=self._input)
        self.started = False
        self.stopped = False
        self.written: list = []
        self.message_source = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    @property
    def input(self):
        return self._input

    def write_line(self, text: str) -> None:
        self.written.append(text)

    def setup_completion(self, input_) -> None:
        pass

    def setup_bottom_bar(self) -> None:
        pass

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        pass

    def flush(self) -> None:
        pass

    def request_bottom_redraw(self) -> None:
        pass

    def set_message_source(self, source) -> None:
        self.message_source = source

    def wait_for_user_input(self, monitor, prefill="", timeout=None, input_=None):
        return self._inputs.pop(0)


class FakeSession:
    def __init__(self, model: str = ""):
        self.model = model
        self.messages: list = []
        self.captured_prefill: str = ""

    def initialize(self) -> None:
        pass

    def on(self, event, cb) -> None:
        pass

    def off(self, event, cb) -> None:
        pass


class FakeClient:
    def __init__(self):
        self.auth = None

    async def get_updates(self, buf):
        await asyncio.sleep(3600)
        return {"msgs": [], "get_updates_buf": buf}

    async def aclose(self) -> None:
        pass

    def set_auth(self, token, base_url) -> None:
        self.auth = (token, base_url)


def _make_tui_env():
    """装配 _run_tui 所需替身，返回 (runner, chat_ui, 观察容器)。"""
    import src.clawbot.runner as runner_mod
    import src.tui.consumer as consumer_mod

    chat_ui = FakeChatUI(inputs=["exit"])
    fake_client = FakeClient()
    holder = {"runner": None, "login_called": False,
              "monitor_alive_at_login": None}

    async def fake_login(client, force=False, print_fn=None, width=None):
        holder["login_called"] = True
        # 登录被调用时 monitor 应已 start（修复核心：提前到登录前）
        runner = holder["runner"]
        if runner is not None and runner._monitor is not None:
            holder["monitor_alive_at_login"] = runner._monitor.is_alive
        # 模拟扫码等待期间用户输入内容（未被 monitor.start 重置清空）
        chat_ui._input.handle_chars("扫码期间输入")
        print_fn("登录成功")
        return "TOKEN", ""

    runner_mod.login = fake_login
    consumer_mod.ChatUIConsumer = lambda: chat_ui

    runner = ClawBotRunner(
        client=fake_client,
        session_factory=lambda m="": FakeSession(m),
        tui=True,
    )
    holder["runner"] = runner
    return runner, chat_ui, holder


async def _run_tui(runner):
    try:
        await runner.run()
    finally:
        try:
            await runner.aclose()
        except Exception:
            pass


def test_monitor_started_before_login():
    """修复核心：monitor.start() 在登录前调用——扫码期间输入不闪没。"""
    runner, chat_ui, holder = _make_tui_env()
    asyncio.run(_run_tui(runner))
    assert holder["login_called"] is True
    # 登录被调用时 monitor 已启动（Input I/O 激活）
    assert holder["monitor_alive_at_login"] is True
    # 扫码等待期间输入的内容在登录完成后保留（未被 monitor.start 的 reset 清空）
    assert chat_ui._input.get_current_text() == "扫码期间输入"
    assert chat_ui.stopped is True


def test_escape_monitor_start_resets_buffer():
    """EscapeMonitor.start() 会重置输入缓冲——根因佐证（登录后调用会清空输入）。"""
    inp = Input(fd=0, history_file=Path(tempfile.mktemp()))
    inp.start_io()
    inp.set_buffer("用户已输入")
    monitor = EscapeMonitor(input_instance=inp)
    monitor.start()
    try:
        assert inp.get_current_text() == ""
    finally:
        monitor.stop()


def test_login_failure_cleanup():
    """登录失败（monitor 已提前启动）时 finally 正常清理，不泄漏/不崩溃。"""
    import src.clawbot.runner as runner_mod
    import src.tui.consumer as consumer_mod

    chat_ui = FakeChatUI(inputs=["exit"])
    fake_client = FakeClient()

    async def failing_login(client, force=False, print_fn=None, width=None):
        raise RuntimeError("二维码已失效")

    runner_mod.login = failing_login
    consumer_mod.ChatUIConsumer = lambda: chat_ui

    runner = ClawBotRunner(
        client=fake_client,
        session_factory=lambda m="": FakeSession(m),
        tui=True,
    )

    async def _run():
        with pytest.raises(RuntimeError, match="二维码已失效"):
            await runner.run()

    asyncio.run(_run())
    # 登录失败后：monitor 已停止、ChatUI 已停止、runner 状态复位
    assert runner._monitor is None
    assert runner._chat_ui is None
    assert chat_ui.stopped is True
    # monitor 停止后 Input I/O 不再运行（终端已恢复）
    assert chat_ui._input.is_io_running is False
