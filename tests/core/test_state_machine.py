"""会话状态机测试 — 覆盖 src/core/state_machine.py。

验证 SessionStateMachine 的生命周期转换、非法转换、回调与重置。
"""

import pytest

from src.core.state_machine import (
    InvalidTransitionError,
    SessionState,
    SessionStateMachine,
)


@pytest.fixture
def sm():
    return SessionStateMachine()


def test_initial_state(sm):
    assert sm.state is SessionState.INIT
    assert sm.name == "init"


def test_initialize(sm):
    sm.initialize()
    assert sm.state is SessionState.IDLE


def test_full_lifecycle(sm):
    sm.initialize()
    sm.start_round()
    assert sm.state is SessionState.RUNNING
    sm.complete_round()
    assert sm.state is SessionState.COMPLETED
    sm.save()
    assert sm.state is SessionState.IDLE


def test_interrupt_and_retry(sm):
    sm.initialize()
    sm.start_round()
    sm.interrupt()
    assert sm.state is SessionState.INTERRUPTED
    sm.retry()
    assert sm.state is SessionState.RUNNING


def test_clear_from_idle(sm):
    sm.initialize()
    sm.clear()
    assert sm.state is SessionState.IDLE


def test_invalid_transition_raises(sm):
    # INIT 状态不允许 start_round
    with pytest.raises(InvalidTransitionError):
        sm.start_round()


def test_invalid_transition_error_fields(sm):
    with pytest.raises(InvalidTransitionError) as ei:
        sm.start_round()
    assert ei.value.state is SessionState.INIT
    assert ei.value.action == "run_round"


def test_can(sm):
    assert sm.can("initialize") is True
    assert sm.can("run_round") is False  # INIT 不能直接 run_round


def test_is_(sm):
    sm.initialize()
    assert sm.is_(SessionState.IDLE) is True
    assert sm.is_(SessionState.RUNNING, SessionState.IDLE) is True
    assert sm.is_(SessionState.RUNNING) is False


def test_on_enter_callback(sm):
    calls = []
    sm.on_enter(SessionState.IDLE, lambda old, new, **kw: calls.append((old, new)))
    sm.initialize()
    assert calls == [(SessionState.INIT, SessionState.IDLE)]


def test_reset(sm):
    sm.initialize()
    sm.reset()
    assert sm.state is SessionState.INIT


def test_repr(sm):
    assert "init" in repr(sm)
