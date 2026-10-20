"""退出提示（恢复命令行）测试 — ``--load`` 恢复用命令行的上屏输出。

用户需求：``exit`` 命令执行（及一切退出路径）时必须在屏幕上打印出用于
``--load`` 加载该会话的命令行，便于用户下次直接恢复会话。历史缺陷：
``--load`` 启动因会话日志恢复路径的 ``UnboundLocalError`` 直接崩溃，退出
提示完全不输出；另一缺陷为渲染侧空闲帧复用吞掉该提示命令。

★ 2026-10-20（用户需求：退出时在所有 TUI 内容**下面一行**打印「再见 /
恢复」且**不再清屏**）：提示不再作为内容行写进 TUI（``write_line``——会
随退出清理一起被抹掉），改为经 ``ChatUIConsumer.set_exit_note`` 交给
``InkSession``，在 ``stop()``（渲染线程结束后）输出到界面内容下方一行；
交互式模式同时不再追加 ``Goodbye!``。
"""

from __future__ import annotations

import pytest

from src.app_loop._utils import _save_and_show_recover
from src.chat_msgs import get_recover_cmd


class _FakeUi:
    """最小 ChatUI 桩：分别记录退出提示（set_exit_note）与内容行（write_line）。"""

    def __init__(self):
        self.lines: list[str] = []
        self.exit_notes: list[str] = []

    def write_line(self, text: str) -> None:
        self.lines.append(text)

    def set_exit_note(self, text: str) -> None:
        self.exit_notes.append(text)


class _LegacyUi:
    """旧实现桩（无 ``set_exit_note``）：验证回退 ``write_line`` 兼容路径。"""

    def __init__(self):
        self.lines: list[str] = []

    def write_line(self, text: str) -> None:
        self.lines.append(text)


class _FakeSession:
    def __init__(self, messages, sid="abc123"):
        self.messages = messages
        self._sid = sid
        self.save_calls = 0

    def save(self):
        self.save_calls += 1
        return self._sid


def test_exit_prints_load_command_line():
    """有对话内容：退出提示含 ``python chat.py --load <sid>``。"""
    ui = _FakeUi()
    session = _FakeSession([{"role": "user", "content": "hi"}], sid="abc123")

    _save_and_show_recover(session, ui)

    assert session.save_calls == 1
    joined = "\n".join(ui.exit_notes)
    assert "再见" in joined
    assert get_recover_cmd("abc123") == "python chat.py --load abc123"
    assert "--load abc123" in joined
    # ★ 2026-10-20：不再作为内容行写进 TUI（否则会随界面收尾被覆盖）
    assert ui.lines == []


def test_exit_note_has_no_leading_newline():
    """提示文本不带前缀换行——「TUI 下方一行」由渲染器负责定位。"""
    ui = _FakeUi()
    session = _FakeSession([{"role": "user", "content": "hi"}], sid="abc123")

    _save_and_show_recover(session, ui)

    assert ui.exit_notes
    assert not ui.exit_notes[0].startswith("\n")


def test_exit_without_content_prints_goodbye_only():
    """无对话内容（仅有 system 消息）：不保存、不发恢复命令，仅告别。"""
    ui = _FakeUi()
    session = _FakeSession([{"role": "system", "content": "sys"}])

    _save_and_show_recover(session, ui)

    assert session.save_calls == 0
    assert ui.exit_notes and "再见" in ui.exit_notes[-1]
    assert "--load" not in ui.exit_notes[-1]


def test_exit_save_and_stop_prints_farewell_for_empty_session():
    """``_exit_save_and_stop``：空会话（无对话内容）也要上屏告别信息。"""
    from src.app_loop._utils import _exit_save_and_stop

    ui = _FakeUi()
    session = _FakeSession([{"role": "system", "content": "sys"}])

    _exit_save_and_stop(session, ui)

    assert ui.exit_notes, "无对话内容时退出提示为空（用户看不到任何退出信息）"
    assert "再见" in "\n".join(ui.exit_notes)
    assert session.save_calls == 0


def test_exit_save_and_stop_prints_recover_for_content_session():
    """``_exit_save_and_stop``：有对话内容时上屏恢复命令行。"""
    from src.app_loop._utils import _exit_save_and_stop

    ui = _FakeUi()
    session = _FakeSession([{"role": "user", "content": "hi"}], sid="sid42")

    _exit_save_and_stop(session, ui)

    assert session.save_calls == 1
    assert "--load sid42" in "\n".join(ui.exit_notes)


def test_exit_falls_back_to_write_line_for_legacy_ui():
    """旧 ChatUI 桩无 ``set_exit_note``：回退 ``write_line``（行为等同修复前）。"""
    ui = _LegacyUi()
    session = _FakeSession([{"role": "user", "content": "hi"}], sid="sid7")

    _save_and_show_recover(session, ui)

    assert ui.lines
    assert "--load sid7" in ui.lines[-1]


def test_exit_falls_back_to_publish_output_without_chat_ui():
    """无 ChatUI（无头上下文）：经 publish_output 输出恢复命令行。"""
    import src.app_loop._utils as utils

    captured: list[str] = []
    original = utils.publish_output
    utils.publish_output = lambda text, level="raw": captured.append(text)
    try:
        session = _FakeSession([{"role": "assistant", "content": "yo"}], sid="sid9")
        _save_and_show_recover(session, None)
    finally:
        utils.publish_output = original

    assert captured
    assert "python chat.py --load sid9" in captured[-1]


# ═══════════════════════════════════════════════════════════
# 交互式模式不再追加 Goodbye!（「TUI 下面一行」版式）
# ═══════════════════════════════════════════════════════════


class _Port:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, text, level="raw"):
        self.lines.append(text)


class _SilentMode:
    """交互式模式桩（``shows_goodbye=False``）。"""

    shows_goodbye = False

    async def bootstrap(self):
        return None

    async def run(self):
        return None

    async def shutdown(self):
        return None


class _GoodbyeMode:
    """默认模式桩（``shows_goodbye`` 缺省 True）。"""

    async def bootstrap(self):
        return None

    async def run(self):
        return None

    async def shutdown(self):
        return None


def test_interactive_mode_disables_extra_goodbye():
    """交互式 TUI 自行输出「再见 / 恢复」，再由 Application 追加 Goodbye! 会多一行。"""
    from src.application import AppMode, InteractiveMode
    from src.application import SingleMode

    assert AppMode.shows_goodbye is True
    assert SingleMode.shows_goodbye is True
    assert InteractiveMode.shows_goodbye is False


@pytest.mark.asyncio
async def test_application_run_skips_goodbye_for_tui_mode():
    from src.application import Application, AppContext

    ctx = AppContext(output_port=_Port())
    app = Application(ctx)
    app.set_mode(_SilentMode())
    await app.run()
    assert ctx.output_port.lines == []


@pytest.mark.asyncio
async def test_application_run_keeps_goodbye_for_other_modes():
    from src.application import Application, AppContext

    ctx = AppContext(output_port=_Port())
    app = Application(ctx)
    app.set_mode(_GoodbyeMode())
    await app.run()
    assert ctx.output_port.lines == ["  Goodbye!"]
