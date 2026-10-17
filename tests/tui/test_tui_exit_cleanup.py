"""TUI 退出清理回归测试。

覆盖 2026-10 review 修复：退出时最后一帧的底部栏（输入区/状态栏）残留，
``Goodbye!`` 覆盖首行后其余行仍留在屏幕上（实测 ``░░░░░░] 1.8%``）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui import _lifecycle


def test_ink_session_prepare_exit_clears_frame():
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    calls: list = []
    session._ink_renderer = SimpleNamespace(full_clear=lambda: calls.append(1))
    session.prepare_exit()
    assert calls == [1]


def test_ink_session_prepare_exit_swallows_error():
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)

    def _boom():
        raise OSError("no tty")

    session._ink_renderer = SimpleNamespace(full_clear=_boom)
    session.prepare_exit()  # 非关键路径：异常不传播


class _Engine:
    def __init__(self, calls: list, *, fail_prepare: bool = False):
        self._calls = calls
        self._line_tracker = None
        self._fail_prepare = fail_prepare

    def flush(self) -> None:
        self._calls.append("flush")

    def prepare_exit(self) -> None:
        self._calls.append("prepare")
        if self._fail_prepare:
            raise OSError("clear failed")

    def stop(self) -> None:
        self._calls.append("stop")


def _stop_lifecycle(engine, monkeypatch):
    monkeypatch.setattr(_lifecycle, "flush_history_disk", lambda timeout: None)
    lc = _lifecycle.TuiLifecycle(engine, object(), object(), object(), object())
    lc._started = True
    lc.stop()


def test_lifecycle_stop_prepares_exit_before_stop(monkeypatch):
    calls: list = []
    _stop_lifecycle(_Engine(calls), monkeypatch)
    assert calls == ["flush", "prepare", "stop"]


def test_lifecycle_stop_continues_when_prepare_exit_fails(monkeypatch):
    calls: list = []
    _stop_lifecycle(_Engine(calls, fail_prepare=True), monkeypatch)
    assert calls == ["flush", "prepare", "stop"]


def test_lifecycle_stop_without_prepare_exit_still_stops(monkeypatch):
    calls: list = []

    class _OldEngine:
        _line_tracker = None

        def flush(self):
            calls.append("flush")

        def stop(self):
            calls.append("stop")

    _stop_lifecycle(_OldEngine(), monkeypatch)
    assert calls == ["flush", "stop"]
