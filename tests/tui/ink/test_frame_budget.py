"""输入/命令单帧时间盒（2026-10-23）回归测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.tui._input_dispatcher import InputDispatcher
from src.tui.ink._session_frame_mixin import _SessionFrameMixin


def test_process_events_respects_budget() -> None:
    dispatcher = object.__new__(InputDispatcher)
    calls: list[int] = []

    def _read() -> bool:
        calls.append(1)
        time.sleep(0.003)
        return len(calls) < 1000

    dispatcher.read_stdin_once = _read  # type: ignore[method-assign]
    dispatcher.process_events(budget=0.02)
    assert 1 <= len(calls) < 1000


def test_process_events_without_budget_drains_all() -> None:
    dispatcher = object.__new__(InputDispatcher)
    calls: list[int] = []

    def _read() -> bool:
        calls.append(1)
        return len(calls) < 50

    dispatcher.read_stdin_once = _read  # type: ignore[method-assign]
    dispatcher.process_events()
    assert len(calls) == 50


def test_apply_commands_respects_budget_and_keeps_remainder() -> None:
    mixin = object.__new__(_SessionFrameMixin)
    applied: list = []
    mixin._apply_fn = lambda model, cmd: (time.sleep(0.003), applied.append(cmd))
    mixin._model = None

    commands = [SimpleNamespace(cid=-1) for _ in range(50)]
    remaining = mixin._apply_commands(commands, budget=0.02)

    assert applied
    assert remaining
    assert len(applied) + len(remaining) == 50
    # 剩余命令保持原顺序（后半段）
    assert remaining[0] is commands[len(applied)]
