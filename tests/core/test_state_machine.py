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


def test_run_round_after_completed_is_allowed(sm):
    """回合正常完成后（状态停留 COMPLETED，尚未 save）可直接开始下一轮。

    回归：start_round() 文档声明 IDLE/COMPLETED/INTERRUPTED → RUNNING，
    但转换表只定义了 IDLE → RUNNING，导致下一条用户消息抛
    InvalidTransitionError: 状态 'completed' 不允许操作 'run_round'。
    """
    sm.initialize()
    sm.start_round()
    sm.complete_round()
    assert sm.state is SessionState.COMPLETED
    sm.start_round()
    assert sm.state is SessionState.RUNNING


def test_run_round_after_interrupted_is_allowed(sm):
    """中断后（状态停留 INTERRUPTED）用户直接发送新消息也能开始新一轮。"""
    sm.initialize()
    sm.start_round()
    sm.interrupt()
    assert sm.state is SessionState.INTERRUPTED
    sm.start_round()
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
