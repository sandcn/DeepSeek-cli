"""终端窗口标题还原 —— 设置 / 记忆 / 退出还原（用户需求 2026-10-20）。

覆盖：
  - ``set_terminal_title`` 写 OSC 0 并标记「已接管」；
  - ``restore_terminal_title`` 写回 ``CHAT_TERMINAL_TITLE`` 或清空标题，
    幂等、未接管时不写任何序列；
  - 控制字符剥离（防 OSC 序列注入）；
  - ``tui._screen.set_window_title`` 与 ``ink render(windowTitle=)`` 共用同一
    状态（接管后可还原）；
  - TUI 退出（``InkSession.stop``）与应用退出路径调用还原。
"""

from __future__ import annotations

import io
import sys

import pytest

import src.tui._screen as screen
import src.tui._window_title as wt

_OSC_EMPTY = "\033]0;\007"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例独立：复位模块状态，stdout 换成可断言缓冲，清空环境变量。"""
    wt.reset_state_for_tests()
    buf = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", buf)
    monkeypatch.delenv("CHAT_TERMINAL_TITLE", raising=False)
    yield buf
    wt.reset_state_for_tests()


def _take(buf: io.StringIO) -> str:
    """取出并清空缓冲区内容。"""
    value = buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    return value


def test_set_writes_osc_and_marks_managed(_isolate):
    wt.set_terminal_title("标题A")
    assert _isolate.getvalue() == "\033]0;标题A\007"
    assert wt.title_managed() is True


def test_restore_clears_title_when_no_original(_isolate):
    wt.set_terminal_title("标题A")
    _take(_isolate)
    assert wt.restore_terminal_title() is True
    assert _isolate.getvalue() == _OSC_EMPTY
    assert wt.title_managed() is False


def test_restore_is_idempotent(_isolate):
    wt.set_terminal_title("A")
    assert wt.restore_terminal_title() is True
    _take(_isolate)
    assert wt.restore_terminal_title() is False
    assert _isolate.getvalue() == ""


def test_restore_never_writes_when_title_not_managed(_isolate):
    assert wt.restore_terminal_title() is False
    assert _isolate.getvalue() == ""


def test_restore_uses_env_original(_isolate, monkeypatch):
    monkeypatch.setenv("CHAT_TERMINAL_TITLE", "原始终端标题")
    wt.set_terminal_title("会话标题")
    _take(_isolate)
    assert wt.restore_terminal_title() is True
    assert _isolate.getvalue() == "\033]0;原始终端标题\007"


def test_set_after_restore_can_restore_again(_isolate):
    wt.set_terminal_title("A")
    assert wt.restore_terminal_title() is True
    wt.set_terminal_title("B")
    _take(_isolate)
    assert wt.restore_terminal_title() is True
    assert _isolate.getvalue() == _OSC_EMPTY


def test_sanitizes_control_sequences(_isolate):
    wt.set_terminal_title("a\x07b\x1bc")
    assert _isolate.getvalue() == "\033]0;abc\007"


def test_screen_set_window_title_shares_state(_isolate):
    """``tui._screen.set_window_title`` 走同一状态：接管后退出可还原。"""
    screen.set_window_title("会话标题")
    assert _isolate.getvalue() == "\033]0;会话标题\007"
    assert wt.title_managed() is True
    _take(_isolate)
    assert wt.restore_terminal_title() is True
    assert _isolate.getvalue() == _OSC_EMPTY


def test_custom_stream_is_remembered(monkeypatch):
    """``ink render(windowTitle=)`` 传自定义流：写入与还原都用该流。"""
    wt.reset_state_for_tests()
    buf = io.StringIO()
    wt.set_terminal_title("自定义流标题", stream=buf)
    assert buf.getvalue() == "\033]0;自定义流标题\007"
    buf.seek(0)
    buf.truncate(0)
    assert wt.restore_terminal_title() is True
    assert buf.getvalue() == _OSC_EMPTY


def test_ink_session_stop_restores_title(monkeypatch):
    """TUI 退出（InkSession.stop）还原窗口标题。"""
    from src.tui.ink.session import InkSession

    calls: list[str] = []
    monkeypatch.setattr(wt, "restore_terminal_title", lambda: calls.append("restore"))

    class _Renderer:
        def suspend(self) -> None:
            pass

        def emit_exit_note(self, note: str) -> None:
            pass

    class _Stub:
        stop = InkSession.stop
        exit_terminal_modes = InkSession.exit_terminal_modes
        _join_render_thread = InkSession._join_render_thread

        def __init__(self) -> None:
            self._render_thread = None
            self._render_version = 0
            self._render_running = False
            self._ink_renderer = _Renderer()
            self._exit_note = None
            self._raw_mode = None
            self._system_monitor = None

        def _drain_queue_safe(self, *args, **kwargs):
            return 0

    _Stub().stop()
    assert calls == ["restore"]


def test_render_window_title_restored_on_unmount():
    """真实链路：ink render(windowTitle=) → unmount（stop）→ 写出还原序列。"""
    import time

    from src.tui.ink import TEXT, h, render

    class _TTYBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    buf = _TTYBuffer()
    ctrl = render(
        h(TEXT, {"children": "x"}), stdout=buf, width=20,
        windowTitle="会话标题", interactive=False,
    )
    time.sleep(0.05)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\033]0;会话标题\007" in out
    assert _OSC_EMPTY in out
    assert wt.title_managed() is False
