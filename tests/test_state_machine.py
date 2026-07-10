"""测试会话状态机 SessionStateMachine

覆盖内容：
  1. 初始状态为 INIT
  2. 正常转换路径
  3. 中断路径
  4. 清空路径（从各状态 clear → IDLE）
  5. 非法转换抛 InvalidTransitionError
  6. can() 在合法/非法时返回正确值
  7. is_() 支持多状态检查
  8. reset() 回到 INIT
  9. on_enter 回调正确触发（on_exit/on_transition 已移除 YAGNI）
  10. 回调异常不传播
  11. 便利方法
  12. name 属性
"""

import pytest
from src.core.state_machine import (
    SessionStateMachine,
    SessionState,
    InvalidTransitionError,
)


# ===============================================================
# 1. 初始状态
# ===============================================================

class TestInitialState:
    """初始状态为 INIT"""

    def test_initial_state_is_init(self):
        sm = SessionStateMachine()
        assert sm.state == SessionState.INIT

    def test_initial_name_is_init(self):
        sm = SessionStateMachine()
        assert sm.name == "init"

    def test_initial_is__init_only(self):
        sm = SessionStateMachine()
        assert sm.is_(SessionState.INIT)
        assert not sm.is_(SessionState.IDLE)
        assert not sm.is_(SessionState.RUNNING)


# ===============================================================
# 2. 正常转换路径
# ===============================================================

class TestHappyPath:
    """正常转换路径：INIT → IDLE → RUNNING → COMPLETED → IDLE"""

    def test_full_happy_path(self):
        sm = SessionStateMachine()

        # INIT → IDLE
        sm.initialize()
        assert sm.state == SessionState.IDLE

        # IDLE → RUNNING
        sm.start_round()
        assert sm.state == SessionState.RUNNING

        # RUNNING → COMPLETED
        sm.complete_round()
        assert sm.state == SessionState.COMPLETED

        # COMPLETED → IDLE
        sm.save()
        assert sm.state == SessionState.IDLE

    def test_transition_returns_new_state(self):
        sm = SessionStateMachine()
        new_state = sm.transition("initialize")
        assert new_state == SessionState.IDLE
        assert sm.state == SessionState.IDLE


# ===============================================================
# 3. 中断路径
# ===============================================================

class TestInterruptPath:
    """RUNNING → interrupt → INTERRUPTED → retry → RUNNING"""

    def test_interrupt_and_retry(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()

        # RUNNING → INTERRUPTED
        sm.interrupt()
        assert sm.state == SessionState.INTERRUPTED

        # INTERRUPTED → RUNNING
        sm.retry()
        assert sm.state == SessionState.RUNNING

    def test_interrupt_then_save(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        assert sm.state == SessionState.INTERRUPTED

        # INTERRUPTED → IDLE (save)
        sm.save()
        assert sm.state == SessionState.IDLE


# ===============================================================
# 4. 清空路径（从各状态 clear → IDLE）
# ===============================================================

class TestClearFromStates:
    """从 COMPLETED / INTERRUPTED / RUNNING / IDLE 都可以 clear → IDLE"""

    def test_clear_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.clear()
        assert sm.state == SessionState.IDLE

    def test_clear_from_running(self):
        """RUNNING→clear 已被禁止（P0 修复），应抛出 InvalidTransitionError。"""
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        with pytest.raises(InvalidTransitionError):
            sm.clear()

    def test_clear_from_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        sm.clear()
        assert sm.state == SessionState.IDLE

    def test_clear_from_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        sm.clear()
        assert sm.state == SessionState.IDLE


# ===============================================================
# 5. 非法转换抛 InvalidTransitionError
# ===============================================================

class TestInvalidTransitions:
    """非法转换抛 InvalidTransitionError"""

    def test_initialize_from_non_init_raises(self):
        sm = SessionStateMachine()
        sm.initialize()  # → IDLE
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.initialize()
        assert exc_info.value.state == SessionState.IDLE
        assert exc_info.value.action == "initialize"

    def test_start_round_from_init_raises(self):
        sm = SessionStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.start_round()

    def test_complete_round_from_idle_raises(self):
        sm = SessionStateMachine()
        sm.initialize()
        with pytest.raises(InvalidTransitionError):
            sm.complete_round()

    def test_interrupt_from_idle_raises(self):
        sm = SessionStateMachine()
        sm.initialize()
        with pytest.raises(InvalidTransitionError):
            sm.interrupt()

    def test_retry_from_init_raises(self):
        sm = SessionStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.retry()

    def test_save_from_init_raises(self):
        sm = SessionStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.save()

    def test_clear_from_init_raises(self):
        sm = SessionStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.clear()

    def test_transition_unknown_action_from_any_state(self):
        sm = SessionStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition("nonexistent_action")

    def test_error_message_contains_state_and_action(self):
        sm = SessionStateMachine()
        try:
            sm.start_round()
        except InvalidTransitionError as e:
            msg = str(e)
            assert "init" in msg or "INIT" in msg
            assert "start_round" in msg or "run_round" in msg

    def test_state_unchanged_after_invalid_transition(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.state == SessionState.IDLE
        try:
            sm.complete_round()
        except InvalidTransitionError:
            pass
        assert sm.state == SessionState.IDLE


# ===============================================================
# 6. can() 在合法/非法时返回正确值
# ===============================================================

class TestCanMethod:
    """can() 方法正确判断合法性"""

    def test_can_initialize_from_init(self):
        sm = SessionStateMachine()
        assert sm.can("initialize") is True

    def test_cannot_initialize_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.can("initialize") is False

    def test_can_run_round_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.can("run_round") is True

    def test_cannot_run_round_from_init(self):
        sm = SessionStateMachine()
        assert sm.can("run_round") is False

    def test_can_complete_from_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.can("complete") is True

    def test_can_interrupt_from_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.can("interrupt") is True

    def test_cannot_complete_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.can("complete") is False

    def test_can_save_from_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        assert sm.can("save") is True

    def test_can_retry_from_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        assert sm.can("retry") is True

    def test_can_save_from_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        assert sm.can("save") is True

    def test_can_retry_from_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        assert sm.can("retry") is True

    def test_can_clear_from_all_states(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.can("clear") is True

        sm.start_round()
        # ★ P0 修复: RUNNING 状态下不再允许 clear
        assert sm.can("clear") is False, "RUNNING 状态下不允许 clear（P0 修复）"

        sm.complete_round()
        assert sm.can("clear") is True

    def test_cannot_save_from_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.can("save") is False

    def test_cannot_retry_from_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.can("retry") is False

    def test_cannot_save_from_init(self):
        sm = SessionStateMachine()
        assert sm.can("save") is False

    def test_can_save_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.can("save") is True

    def test_can_retry_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.can("retry") is True

    def test_unknown_action_returns_false(self):
        sm = SessionStateMachine()
        assert sm.can("fly_to_moon") is False

    def test_can_after_transition_updates_correctly(self):
        sm = SessionStateMachine()
        assert sm.can("run_round") is False

        sm.initialize()  # → IDLE
        assert sm.can("run_round") is True

        sm.start_round()  # → RUNNING
        assert sm.can("run_round") is False
        assert sm.can("complete") is True


# ===============================================================
# 7. is_() 支持多状态检查
# ===============================================================

class TestIsMethod:
    """is_() 支持多状态检查"""

    def test_is__single_state(self):
        sm = SessionStateMachine()
        assert sm.is_(SessionState.INIT) is True

    def test_is__multiple_states_match(self):
        sm = SessionStateMachine()
        assert sm.is_(SessionState.INIT, SessionState.IDLE) is True

    def test_is__multiple_states_no_match(self):
        sm = SessionStateMachine()
        assert sm.is_(SessionState.IDLE, SessionState.RUNNING) is False

    def test_is__on_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.is_(SessionState.IDLE, SessionState.INIT) is True
        assert sm.is_(SessionState.RUNNING) is False

    def test_is__on_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.is_(SessionState.RUNNING) is True
        assert sm.is_(SessionState.IDLE, SessionState.COMPLETED) is False

    def test_is__empty_args(self):
        sm = SessionStateMachine()
        assert sm.is_() is False


# ===============================================================
# 8. reset() 回到 INIT
# ===============================================================

class TestReset:
    """reset() 回到 INIT"""

    def test_reset_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.reset()
        assert sm.state == SessionState.INIT

    def test_reset_from_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.reset()
        assert sm.state == SessionState.INIT

    def test_reset_from_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        sm.reset()
        assert sm.state == SessionState.INIT

    def test_reset_from_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        sm.reset()
        assert sm.state == SessionState.INIT

    def test_name_after_reset(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.reset()
        assert sm.name == "init"

    def test_is__after_reset(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.reset()
        assert sm.is_(SessionState.INIT)
        assert not sm.is_(SessionState.RUNNING)

    def test_can_initialize_after_reset(self):
        """reset 之后可重新 initialize"""
        sm = SessionStateMachine()
        sm.initialize()
        sm.reset()
        assert sm.can("initialize") is True
        sm.initialize()
        assert sm.state == SessionState.IDLE


# ===============================================================
# 9. on_enter 回调正确触发（on_exit/on_transition 已移除 YAGNI）
# ===============================================================

class TestCallbacks:
    """on_enter 回调正确触发"""

    def test_on_enter_called(self):
        sm = SessionStateMachine()
        calls = []
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: calls.append(("enter", new)))
        sm.initialize()
        assert len(calls) == 1
        assert calls[0] == ("enter", SessionState.IDLE)

    def test_on_enter_not_called_for_unrelated_state(self):
        sm = SessionStateMachine()
        calls = []
        sm.on_enter(SessionState.RUNNING, lambda old, new, **kw: calls.append(("enter", new)))
        sm.initialize()
        assert len(calls) == 0

    def test_on_enter_receives_context(self):
        sm = SessionStateMachine()
        calls = []
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: calls.append(kw))
        sm.transition("initialize", user="test_user")
        assert len(calls) == 1
        assert calls[0].get("user") == "test_user"
        assert calls[0].get("action") == "initialize"

    def test_multiple_callbacks_for_same_event(self):
        sm = SessionStateMachine()
        call_order = []
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: call_order.append(1))
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: call_order.append(2))
        sm.initialize()
        assert call_order == [1, 2]


# ===============================================================
# 10. 回调异常不传播
# ===============================================================

class TestCallbackExceptions:
    """回调异常被吞掉，不传播到调用方"""

    def test_on_enter_exception_swallowed(self):
        sm = SessionStateMachine()
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: 1 / 0)
        # 不应抛 ZeroDivisionError
        sm.initialize()
        assert sm.state == SessionState.IDLE

    def test_subsequent_callbacks_still_fire_after_exception(self):
        """即使某个回调抛异常，后续同类型回调仍执行"""
        sm = SessionStateMachine()
        calls = []

        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: 1 / 0)  # 异常
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: calls.append("ok"))  # 正常

        sm.initialize()
        assert calls == ["ok"]

    def test_state_still_transitions_despite_exception(self):
        sm = SessionStateMachine()
        sm.on_enter(SessionState.IDLE, lambda old, new, **kw: 1 / 0)
        sm.initialize()
        assert sm.state == SessionState.IDLE


# ===============================================================
# 11. 便利方法
# ===============================================================

class TestConvenienceMethods:
    """便利方法正确触发对应转换"""

    def test_initialize(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.state == SessionState.IDLE

    def test_start_round_from_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.state == SessionState.RUNNING

    def test_complete_round(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        assert sm.state == SessionState.COMPLETED

    def test_interrupt(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        assert sm.state == SessionState.INTERRUPTED

    def test_save_from_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        sm.save()
        assert sm.state == SessionState.IDLE

    def test_save_from_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        sm.save()
        assert sm.state == SessionState.IDLE

    def test_retry_from_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        sm.retry()
        assert sm.state == SessionState.RUNNING

    def test_retry_from_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        sm.retry()
        assert sm.state == SessionState.RUNNING

    def test_clear(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.clear()
        assert sm.state == SessionState.IDLE

    def test_save_from_idle(self):
        """IDLE 状态下 save 保持在 IDLE"""
        sm = SessionStateMachine()
        sm.initialize()
        sm.save()
        assert sm.state == SessionState.IDLE

    def test_retry_from_idle(self):
        """IDLE 状态下 retry 进入 RUNNING"""
        sm = SessionStateMachine()
        sm.initialize()
        sm.retry()
        assert sm.state == SessionState.RUNNING

    def test_chained_convenience_methods(self):
        """链式调用便利方法走完整路径"""
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        sm.save()
        sm.start_round()
        sm.interrupt()
        sm.retry()
        sm.complete_round()
        assert sm.state == SessionState.COMPLETED


# ===============================================================
# 12. name 属性
# ===============================================================

class TestNameProperty:
    """name 属性返回当前状态的字符串值"""

    def test_name_init(self):
        sm = SessionStateMachine()
        assert sm.name == "init"

    def test_name_idle(self):
        sm = SessionStateMachine()
        sm.initialize()
        assert sm.name == "idle"

    def test_name_running(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        assert sm.name == "running"

    def test_name_completed(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.complete_round()
        assert sm.name == "completed"

    def test_name_interrupted(self):
        sm = SessionStateMachine()
        sm.initialize()
        sm.start_round()
        sm.interrupt()
        assert sm.name == "interrupted"

    def test_name_type(self):
        sm = SessionStateMachine()
        assert isinstance(sm.name, str)


# ===============================================================
# 13. __repr__ 表示
# ===============================================================

class TestRepr:
    def test_repr(self):
        sm = SessionStateMachine()
        assert repr(sm) == "<SessionStateMachine: init>"
        sm.initialize()
        assert repr(sm) == "<SessionStateMachine: idle>"


# ===============================================================
# 14. 多实例隔离
# ===============================================================

class TestInstanceIsolation:
    """多个状态机实例互不干扰"""

    def test_independent_instances(self):
        sm1 = SessionStateMachine()
        sm2 = SessionStateMachine()

        sm1.initialize()
        assert sm1.state == SessionState.IDLE
        assert sm2.state == SessionState.INIT

        sm2.initialize()
        sm2.start_round()
        assert sm2.state == SessionState.RUNNING
        assert sm1.state == SessionState.IDLE


# ===============================================================
# 15. InvalidTransitionError 基础属性
# ===============================================================

class TestInvalidTransitionError:
    """自定义异常的 state 和 action 属性"""

    def test_exception_attributes(self):
        exc = InvalidTransitionError(SessionState.INIT, "start_round")
        assert exc.state == SessionState.INIT
        assert exc.action == "start_round"

    def test_exception_is_exception(self):
        exc = InvalidTransitionError(SessionState.INIT, "start_round")
        assert isinstance(exc, Exception)
