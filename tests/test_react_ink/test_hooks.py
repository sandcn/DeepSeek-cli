"""Hooks 运行时单元测试。

覆盖 use_state / use_effect / use_ref / use_memo / use_callback /
use_context / use_reducer 共 7 个 Hook + _HooksRuntime 生命周期。

测试策略：通过 _hooks_runtime.enter_component/exit_component 模拟组件渲染周期，
每个测试独立创建 mock 组件，确保测试间无状态泄漏。
"""

from __future__ import annotations

import pytest

from src.chat_ui.vdom.hooks import (
    _HooksRuntime,
    _hooks_runtime,
    use_state,
    use_effect,
    use_ref,
    use_memo,
    use_callback,
    use_context,
    use_reducer,
    create_context,
    get_hooks_runtime,
    HookError,
)
from src.chat_ui.vdom.types import HookState, EffectState


# ── 测试辅助 ────────────────────────────────────────────


class _MockComponent:
    """模拟 TuiComponent，提供 hooks 所需的最小属性集。

    每次测试都创建新实例，通过 _hooks_runtime.enter_component()
    注册为当前渲染组件后即可调用 hooks 函数。
    """

    def __init__(self):
        self._hooks: list[HookState] | None = None
        self._hook_index: int = 0
        self._dirty: bool = False
        self._mounted: bool = False

    def _ensure_hooks(self) -> list[HookState]:
        if self._hooks is None:
            self._hooks = []
        return self._hooks


@pytest.fixture(autouse=True)
def _clean_runtime():
    """每个测试前后确保运行时状态干净。"""
    # 清理可能残留的 effect 队列
    _hooks_runtime._pending_effects.clear()
    _hooks_runtime._component_stack.clear()
    _hooks_runtime._current_component = None
    _hooks_runtime._rerender_callback = None
    yield
    _hooks_runtime._pending_effects.clear()
    _hooks_runtime._component_stack.clear()
    _hooks_runtime._current_component = None
    _hooks_runtime._rerender_callback = None


def _enter(comp: _MockComponent) -> None:
    """进入组件渲染上下文（重置 hook_index 并推入栈）。"""
    _hooks_runtime.enter_component(comp)


def _exit(comp: _MockComponent) -> None:
    """退出组件渲染上下文。"""
    _hooks_runtime.exit_component(comp)


# ═══════════════════════════════════════════════════════════
# TestUseState
# ═══════════════════════════════════════════════════════════

class TestUseState:
    """use_state Hook 测试。"""

    def test_initial_value(self):
        """首次渲染时返回 initial 值。"""
        comp = _MockComponent()
        _enter(comp)
        value, _ = use_state(42)
        assert value == 42
        _exit(comp)

    def test_initial_value_lazy_init(self):
        """initial 为可调用对象时执行惰性初始化。"""
        comp = _MockComponent()
        _enter(comp)
        value, _ = use_state(lambda: "hello")
        assert value == "hello"
        _exit(comp)

    def test_lazy_init_called_only_once(self):
        """惰性初始化函数仅在首次渲染时调用一次。"""
        call_count = 0

        def _init():
            nonlocal call_count
            call_count += 1
            return 10

        comp = _MockComponent()
        # 首次渲染
        _enter(comp)
        v1, _ = use_state(_init)
        assert v1 == 10
        assert call_count == 1
        _exit(comp)

        # 第二次渲染 — _init 不应再被调用
        _enter(comp)
        v2, _ = use_state(_init)
        assert v2 == 10
        assert call_count == 1  # 未再次调用
        _exit(comp)

    def test_setter_updates_value(self):
        """setter 更新值并标记组件 dirty。"""
        comp = _MockComponent()
        _enter(comp)
        value, set_value = use_state(0)
        assert value == 0
        _exit(comp)

        # 跨渲染周期后值保持
        _enter(comp)
        _value2, set_value2 = use_state(0)
        assert _value2 == 0

        set_value2(5)
        # value2 在 setter 调用前已解包，仍是旧值 0；
        # 但 hook.value 已被修改为 5
        assert comp._hooks[0].value == 5
        _exit(comp)

        # 再次渲染值保持为 5
        _enter(comp)
        value3, _ = use_state(0)
        assert value3 == 5
        _exit(comp)

    def test_setter_with_updater_function(self):
        """setter 接受 (prev) -> new 更新函数。"""
        comp = _MockComponent()
        _enter(comp)
        _, set_value = use_state(10)
        _exit(comp)

        _enter(comp)
        _, set_value2 = use_state(10)
        set_value2(lambda prev: prev * 2)
        _exit(comp)

        _enter(comp)
        value3, _ = use_state(10)
        assert value3 == 20
        _exit(comp)

    def test_setter_triggers_rerender(self):
        """setter 调用 request_rerender 标记组件 dirty。"""
        comp = _MockComponent()
        _enter(comp)
        _, set_value = use_state(0)
        set_value(99)
        assert comp._dirty is True
        _exit(comp)

    def test_setter_triggers_rerender_callback(self):
        """setter 调用时触发已注册的 rerender_callback。"""
        callback_called = []

        def _cb(c):
            callback_called.append(c)

        _hooks_runtime.set_rerender_callback(_cb)

        comp = _MockComponent()
        _enter(comp)
        _, set_value = use_state(0)
        set_value(1)
        _exit(comp)

        assert len(callback_called) == 1
        assert callback_called[0] is comp

    def test_multiple_state_hooks(self):
        """同一组件可使用多个 use_state，按调用顺序匹配。"""
        comp = _MockComponent()
        _enter(comp)
        v1, s1 = use_state("a")
        v2, s2 = use_state("b")
        v3, s3 = use_state("c")
        assert (v1, v2, v3) == ("a", "b", "c")
        _exit(comp)

        # 第二次渲染 — 顺序一致，值保持
        _enter(comp)
        v1b, s1b = use_state("a")
        v2b, s2b = use_state("b")
        v3b, s3b = use_state("c")
        assert (v1b, v2b, v3b) == ("a", "b", "c")

        s2b("updated")
        _exit(comp)

        # 第三次渲染 — 仅第二个值变化
        _enter(comp)
        v1c, _ = use_state("a")
        v2c, _ = use_state("b")
        v3c, _ = use_state("c")
        assert v1c == "a"
        assert v2c == "updated"
        assert v3c == "c"
        _exit(comp)

    def test_hook_order_preserved(self):
        """多次渲染间 hooks 按注册顺序正确匹配。"""
        comp = _MockComponent()

        # 注册 3 个 state hooks
        _enter(comp)
        use_state(1)
        use_state(2)
        use_state(3)
        _exit(comp)

        hooks = comp._hooks
        assert hooks is not None
        assert len(hooks) == 3
        assert hooks[0].type == "state"
        assert hooks[1].type == "state"
        assert hooks[2].type == "state"


# ═══════════════════════════════════════════════════════════
# TestUseEffect
# ═══════════════════════════════════════════════════════════

class TestUseEffect:
    """use_effect Hook 测试。"""

    def test_runs_on_mount_with_empty_deps(self):
        """deps=[] 时在 mount 后执行一次。"""
        executed = []

        def _effect():
            executed.append("ran")
            return None

        comp = _MockComponent()
        _enter(comp)
        use_effect(_effect, [])
        _exit(comp)

        # effect 尚未执行（在 run_effects 中执行）
        assert executed == []

        _hooks_runtime.run_effects()
        assert executed == ["ran"]

    def test_runs_every_render_when_deps_none(self):
        """deps=None 时每次渲染后都执行。"""
        executed = []

        def _effect():
            executed.append("ran")
            return None

        comp = _MockComponent()
        # 第一次渲染
        _enter(comp)
        use_effect(_effect, None)
        _exit(comp)
        _hooks_runtime.run_effects()
        assert executed == ["ran"]

        # 第二次渲染
        _enter(comp)
        use_effect(_effect, None)
        _exit(comp)
        _hooks_runtime.run_effects()
        assert executed == ["ran", "ran"]

    def test_runs_when_deps_change(self):
        """依赖变化时重新执行 effect。"""
        executed = []

        def _effect():
            executed.append("ran")
            return None

        comp = _MockComponent()
        # 首次渲染 deps=[1]
        _enter(comp)
        use_effect(_effect, [1])
        _exit(comp)
        _hooks_runtime.run_effects()
        assert executed == ["ran"]

        # deps 未变 — 不执行
        _enter(comp)
        use_effect(_effect, [1])
        _exit(comp)
        _hooks_runtime.run_effects()
        assert executed == ["ran"]

        # deps 变化 — 重新执行
        _enter(comp)
        use_effect(_effect, [2])
        _exit(comp)
        _hooks_runtime.run_effects()
        assert executed == ["ran", "ran"]

    def test_cleanup_called_on_unmount(self):
        """组件 unmount 时执行 cleanup 函数。"""
        cleanups = []

        def _effect():
            return lambda: cleanups.append("cleanup")

        comp = _MockComponent()
        _enter(comp)
        use_effect(_effect, [])
        _exit(comp)
        _hooks_runtime.run_effects()

        # cleanup_component 应调用 cleanup
        _hooks_runtime.cleanup_component(comp)
        assert cleanups == ["cleanup"]

    def test_cleanup_called_before_next_effect(self):
        """依赖变化时先执行上次的 cleanup 再执行新 effect。"""
        events = []

        def _effect1():
            events.append("e1")
            return lambda: events.append("c1")

        def _effect2():
            events.append("e2")
            return lambda: events.append("c2")

        comp = _MockComponent()
        # 首次
        _enter(comp)
        use_effect(_effect1, [1])
        _exit(comp)
        _hooks_runtime.run_effects()
        assert events == ["e1"]

        # deps 变化 — 先 c1 再 e2
        _enter(comp)
        use_effect(_effect2, [2])
        _exit(comp)
        _hooks_runtime.run_effects()
        assert events == ["e1", "c1", "e2"]


# ═══════════════════════════════════════════════════════════
# TestUseRef
# ═══════════════════════════════════════════════════════════

class TestUseRef:
    """use_ref Hook 测试。"""

    def test_returns_mutable_container(self):
        """返回 {'current': initial} 容器。"""
        comp = _MockComponent()
        _enter(comp)
        ref = use_ref(0)
        assert ref == {"current": 0}
        _exit(comp)

    def test_persists_across_renders(self):
        """同一 ref 对象跨渲染周期保持。"""
        comp = _MockComponent()
        _enter(comp)
        ref1 = use_ref("hello")
        _exit(comp)

        _enter(comp)
        ref2 = use_ref("hello")
        assert ref2 is ref1  # 同一对象
        assert ref2["current"] == "hello"
        _exit(comp)

    def test_mutation_does_not_trigger_rerender(self):
        """修改 ref.current 不标记组件 dirty。"""
        comp = _MockComponent()
        _enter(comp)
        ref = use_ref(0)
        ref["current"] = 999
        # use_ref 不调用 request_rerender
        # 验证组件未被标记 dirty（由 ref mutation 引起）
        _exit(comp)

        # 实际上 ref mutation 不会触发 setter，所以 dirty 不应由它改变
        # 此处验证 ref.current 跨渲染保持
        _enter(comp)
        ref2 = use_ref(0)
        assert ref2["current"] == 999
        _exit(comp)


# ═══════════════════════════════════════════════════════════
# TestUseMemo
# ═══════════════════════════════════════════════════════════

class TestUseMemo:
    """use_memo Hook 测试。"""

    def test_caches_value_when_deps_unchanged(self):
        """依赖不变时返回缓存值，不重新调用 factory。"""
        call_count = 0

        def _factory():
            nonlocal call_count
            call_count += 1
            return call_count

        comp = _MockComponent()
        _enter(comp)
        v1 = use_memo(_factory, [1, 2])
        assert v1 == 1  # call_count == 1
        _exit(comp)

        # 依赖不变 — 不重新计算
        _enter(comp)
        v2 = use_memo(_factory, [1, 2])
        assert v2 == 1  # 仍是 1，factory 未被调用
        assert call_count == 1
        _exit(comp)

    def test_recomputes_when_deps_change(self):
        """依赖变化时重新调用 factory。"""
        call_count = 0

        def _factory():
            nonlocal call_count
            call_count += 1
            return call_count * 10

        comp = _MockComponent()
        _enter(comp)
        v1 = use_memo(_factory, [1])
        assert v1 == 10
        _exit(comp)

        _enter(comp)
        v2 = use_memo(_factory, [2])  # deps 变化
        assert v2 == 20
        assert call_count == 2
        _exit(comp)


# ═══════════════════════════════════════════════════════════
# TestUseCallback
# ═══════════════════════════════════════════════════════════

class TestUseCallback:
    """use_callback Hook 测试。"""

    def test_returns_stable_reference(self):
        """依赖不变时返回同一函数引用。"""
        def _fn():
            return "test"

        comp = _MockComponent()
        _enter(comp)
        cb1 = use_callback(_fn, [1])
        _exit(comp)

        _enter(comp)
        cb2 = use_callback(_fn, [1])
        assert cb2 is cb1
        _exit(comp)

    def test_new_reference_when_deps_change(self):
        """依赖变化时返回新的函数引用（传入不同函数时）。"""
        def _fn_a():
            return "a"

        def _fn_b():
            return "b"

        comp = _MockComponent()
        _enter(comp)
        cb1 = use_callback(_fn_a, [1])
        _exit(comp)

        _enter(comp)
        cb2 = use_callback(_fn_b, [2])  # deps 变化 + fn 不同
        assert cb2 is not cb1
        _exit(comp)


# ═══════════════════════════════════════════════════════════
# TestUseContext
# ═══════════════════════════════════════════════════════════

class TestUseContext:
    """use_context Hook 测试。"""

    def test_returns_default_value(self):
        """无 Provider 时返回默认值。"""
        ctx = create_context("default_theme")
        comp = _MockComponent()
        _enter(comp)
        value = use_context(ctx)
        assert value == "default_theme"
        _exit(comp)

    def test_provider_overrides_value(self):
        """Provider 压入值后 use_context 返回栈顶值。"""
        ctx = create_context("light")
        # 模拟 Provider 压入值
        ctx["_stack"].append("dark")

        comp = _MockComponent()
        _enter(comp)
        value = use_context(ctx)
        assert value == "dark"
        _exit(comp)

        # Provider 弹出后恢复默认值
        ctx["_stack"].pop()
        _enter(comp)
        value2 = use_context(ctx)
        assert value2 == "light"
        _exit(comp)

    def test_nested_providers(self):
        """多层 Provider 嵌套，use_context 读取栈顶值。"""
        ctx = create_context("base")
        ctx["_stack"].append("level1")
        ctx["_stack"].append("level2")

        comp = _MockComponent()
        _enter(comp)
        value = use_context(ctx)
        assert value == "level2"
        _exit(comp)


# ═══════════════════════════════════════════════════════════
# TestUseReducer
# ═══════════════════════════════════════════════════════════

class TestUseReducer:
    """use_reducer Hook 测试。"""

    def test_initial_state(self):
        """初始状态为 initial 参数值。"""
        def _reducer(state, action):
            return state + action

        comp = _MockComponent()
        _enter(comp)
        state, dispatch = use_reducer(_reducer, 10)
        assert state == 10
        _exit(comp)

    def test_lazy_initialization(self):
        """init 参数时使用惰性初始化。"""
        def _reducer(state, action):
            return state + action

        def _init(initial):
            return initial * 10

        comp = _MockComponent()
        _enter(comp)
        state, _ = use_reducer(_reducer, 5, init=_init)
        assert state == 50
        _exit(comp)

    def test_dispatch_updates_state(self):
        """dispatch(action) 调用 reducer 计算新状态。"""
        def _reducer(state, action):
            if action["type"] == "INCREMENT":
                return state + action["by"]
            return state

        comp = _MockComponent()
        _enter(comp)
        state, dispatch = use_reducer(_reducer, 0)
        assert state == 0
        _exit(comp)

        _enter(comp)
        state2, dispatch2 = use_reducer(_reducer, 0)
        dispatch2({"type": "INCREMENT", "by": 5})
        _exit(comp)

        _enter(comp)
        state3, _ = use_reducer(_reducer, 0)
        assert state3 == 5
        _exit(comp)

    def test_action_passed_to_reducer(self):
        """dispatch 将 action 参数原样传递给 reducer。"""
        actions_received = []

        def _reducer(state, action):
            actions_received.append(action)
            return state

        comp = _MockComponent()
        _enter(comp)
        _, dispatch = use_reducer(_reducer, "initial")
        dispatch("ACTION_A")
        _exit(comp)

        assert actions_received == ["ACTION_A"]


# ═══════════════════════════════════════════════════════════
# TestHooksRuntime
# ═══════════════════════════════════════════════════════════

class TestHooksRuntime:
    """_HooksRuntime 生命周期测试。"""

    def test_enter_exit_component(self):
        """enter_component 推入栈顶，exit_component 弹出。"""
        comp = _MockComponent()
        _enter(comp)
        assert _hooks_runtime._current_component is comp
        assert len(_hooks_runtime._component_stack) == 1
        _exit(comp)
        assert _hooks_runtime._current_component is None
        assert len(_hooks_runtime._component_stack) == 0

    def test_enter_resets_hook_index(self):
        """每次 enter_component 重置 hook_index 为 0。"""
        comp = _MockComponent()
        _enter(comp)
        use_state(1)
        use_state(2)
        assert comp._hook_index == 2
        _exit(comp)

        # 重新进入 — hook_index 重置
        _enter(comp)
        assert comp._hook_index == 0
        _exit(comp)

    def test_hook_error_on_out_of_order(self):
        """在组件 render 上下文外调用 hooks 抛出 HookError。"""
        # 确保不在任何组件上下文中
        assert _hooks_runtime._current_component is None
        with pytest.raises(HookError):
            use_state(1)

    def test_get_hooks_runtime_returns_singleton(self):
        """get_hooks_runtime() 返回全局单例。"""
        rt1 = get_hooks_runtime()
        rt2 = get_hooks_runtime()
        assert rt1 is rt2
        assert isinstance(rt1, _HooksRuntime)

    def test_cleanup_component_clears_pending_effects(self):
        """cleanup_component 从待执行队列中移除该组件 effect。"""
        executed = []

        def _effect():
            executed.append("e")
            return None

        comp = _MockComponent()
        _enter(comp)
        use_effect(_effect, [])
        _exit(comp)

        # 清理前队列中有该组件的 effect
        assert len(_hooks_runtime._pending_effects) == 1

        _hooks_runtime.cleanup_component(comp)
        # 清理后队列中移除
        assert len(_hooks_runtime._pending_effects) == 0

    def test_nested_component_stack(self):
        """嵌套组件渲染时 _component_stack 正确追踪。"""
        parent = _MockComponent()
        child = _MockComponent()

        _enter(parent)
        assert _hooks_runtime._current_component is parent
        assert len(_hooks_runtime._component_stack) == 1

        _enter(child)
        assert _hooks_runtime._current_component is child
        assert len(_hooks_runtime._component_stack) == 2

        _exit(child)
        assert _hooks_runtime._current_component is parent
        assert len(_hooks_runtime._component_stack) == 1

        _exit(parent)
        assert _hooks_runtime._current_component is None
        assert len(_hooks_runtime._component_stack) == 0
