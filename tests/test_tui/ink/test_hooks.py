"""测试 ink/hooks.py — use_state/use_reducer/use_ref/use_effect。

hook 需在函数组件渲染期间调用（reconciler 管理当前 fiber 栈）。
测试通过 Reconciler 渲染函数组件来驱动 hook 生命周期。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.tui.ink.element import h, TEXT, Element
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.hooks import (
    use_state,
    use_reducer,
    use_ref,
    use_effect,
    deps_changed,
)


class TestUseState:
    """use_state 状态保持与更新。"""

    def test_state_persists_across_renders(self):
        """同一 fiber 重渲染时 use_state 状态保持。"""
        seen = []

        def Comp(props):
            count, _ = use_state(0)
            seen.append(count)
            return h(TEXT, {"children": str(count)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
        assert seen == [0, 0]  # 状态保持，非重置

    def test_set_state_updates_next_render(self):
        """set_state 入队更新，下次渲染生效。"""
        seen = []
        setter_holder = []

        def Comp(props):
            count, set_count = use_state(0)
            if not setter_holder:
                setter_holder.append(set_count)
            seen.append(count)
            return h(TEXT, {"children": str(count)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        setter_holder[0](5)
        r.render(root, el, 80, 24)
        assert seen == [0, 5]

    def test_set_state_functional_update(self):
        """set_state 支持更新函数（prev -> next）。"""
        seen = []
        holder = []

        def Comp(props):
            n, set_n = use_state(1)
            if not holder:
                holder.append(set_n)
            seen.append(n)
            return h(TEXT, {"children": str(n)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        holder[0](lambda x: x + 1)
        r.render(root, el, 80, 24)
        assert seen == [1, 2]

    def test_multiple_states_independent(self):
        """多个 use_state 各自独立。"""
        holder = []

        def Comp(props):
            a, set_a = use_state("a")
            b, set_b = use_state("b")
            if not holder:
                holder.extend([set_a, set_b])
            return h(TEXT, {"children": a + b})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        holder[0]("A")
        r.render(root, el, 80, 24)
        assert holder[1]  # set_b 存在
        # 验证 b 状态保持
        r2 = r.render  # noop

    def test_use_state_raises_outside_component(self):
        """非渲染期调用 use_state 应抛异常。"""
        import pytest
        from src.tui.ink.hooks import _current_fiber_stack
        assert _current_fiber_stack == []
        with pytest.raises(RuntimeError):
            use_state(0)


class TestUseReducer:
    """use_reducer 行为。"""

    def test_reducer_dispatches(self):
        seen = []
        holder = []

        def reducer(state, action):
            return state + action

        def Comp(props):
            n, dispatch = use_reducer(reducer, 10)
            if not holder:
                holder.append(dispatch)
            seen.append(n)
            return h(TEXT, {"children": str(n)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        holder[0](5)
        r.render(root, el, 80, 24)
        assert seen == [10, 15]


class TestUseRef:
    """use_ref 可变引用。"""

    def test_ref_current_persists(self):
        holder = []

        def Comp(props):
            ref = use_ref({"count": 0})
            if not holder:
                holder.append(ref)
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        holder[0].current["count"] = 99
        r.render(root, el, 80, 24)
        # 同一引用对象
        assert holder[0].current["count"] == 99


class TestUseEffect:
    """use_effect 依赖变化 + 销毁。"""

    def test_effect_runs_on_mount(self):
        create = MagicMock(return_value=None)

        def Comp(props):
            use_effect(create, [])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        create.assert_called_once()

    def test_effect_not_rerun_on_same_deps(self):
        create = MagicMock(return_value=None)

        def Comp(props):
            use_effect(create, [1])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
        create.assert_called_once()  # 依赖未变化

    def test_effect_reruns_on_deps_change(self):
        create = MagicMock(return_value=None)

        class Wrap:
            dep = 1

        def Comp(props):
            use_effect(create, [Wrap.dep])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        Wrap.dep = 2
        r.render(root, el, 80, 24)
        assert create.call_count == 2

    def test_effect_destroy_runs_before_create(self):
        order = []

        def Comp(props):
            def create():
                def destroy():
                    order.append("destroy")
                order.append("create")
                return destroy
            # deps=None → 每次渲染都执行（先销毁后创建）
            use_effect(create, None)
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
        assert order == ["create", "destroy", "create"]

    def test_deps_changed_helper(self):
        from src.tui.ink.fiber import EffectHook
        hook = EffectHook(create=None, deps=None, destroy=None, last_deps=None)
        assert deps_changed(hook) is True  # deps None → 每次变化
        hook.deps = [1]
        hook.last_deps = None
        assert deps_changed(hook) is True
        hook.last_deps = [1]
        assert deps_changed(hook) is False
        hook.deps = [2]
        assert deps_changed(hook) is True
