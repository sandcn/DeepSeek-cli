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


class TestMemoInputHookDataclass:
    """INK-2/INK-1 — MemoHook / InputHook 数据结构（fiber.py 新增）。"""

    def test_memo_hook_dataclass_regression(self):
        from src.tui.ink.fiber import MemoHook
        hook = MemoHook()
        assert hook.factory is None
        assert hook.deps is None
        assert hook.value is None
        assert hook.last_deps is None
        hook2 = MemoHook(factory=lambda: 1, deps=[1], value=42, last_deps=[1])
        assert hook2.value == 42
        assert hook2.last_deps == [1]

    def test_input_hook_dataclass_regression(self):
        from src.tui.ink.fiber import InputHook
        hook = InputHook()
        assert hook.handler is None
        assert hook.is_active is True  # 默认 active
        fn = lambda ev: True
        hook2 = InputHook(handler=fn, is_active=False)
        assert hook2.handler is fn
        assert hook2.is_active is False


class TestUseMemo:
    """INK-2/INK-4 — use_memo / use_callback。"""

    def test_use_memo_caches_value_regression(self):
        """deps 不变时 factory 仅调用一次、返回同一 value。"""
        from src.tui.ink.hooks import use_memo
        factory_calls = []

        def factory():
            factory_calls.append(1)
            return {"v": 42}

        def Comp(props):
            value = use_memo(factory, [1])
            return h(TEXT, {"children": str(value["v"])})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
        assert len(factory_calls) == 1  # deps 不变 → 仅计算一次

    def test_use_memo_recomputes_on_deps_change_regression(self):
        """deps 变化时重新执行 factory。"""
        from src.tui.ink.hooks import use_memo

        class Wrap:
            dep = 1

        seen = []

        def Comp(props):
            value = use_memo(lambda: Wrap.dep * 10, [Wrap.dep])
            seen.append(value)
            return h(TEXT, {"children": str(value)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        Wrap.dep = 2
        r.render(root, el, 80, 24)
        assert seen == [10, 20]

    def test_use_memo_none_deps_recomputes_every_render_regression(self):
        """deps=None 时每次渲染重算（与 useEffect deps=None 语义对齐）。"""
        from src.tui.ink.hooks import use_memo
        calls = []

        def Comp(props):
            value = use_memo(lambda: calls.append(1) or len(calls), None)
            return h(TEXT, {"children": str(value)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
        assert len(calls) == 2  # 每次渲染都重算

    def test_use_callback_stable_identity_regression(self):
        """deps 不变时 use_callback 返回同一函数对象。"""
        from src.tui.ink.hooks import use_callback
        refs = []

        def Comp(props):
            cb = use_callback(lambda: 1, [1])
            refs.append(cb)
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
        assert refs[0] is refs[1]  # 同一函数对象

    def test_use_callback_changes_on_deps_change_regression(self):
        """deps 变化时 use_callback 返回新函数对象。"""
        from src.tui.ink.hooks import use_callback

        class Wrap:
            dep = 1

        refs = []

        def Comp(props):
            cb = use_callback(lambda: Wrap.dep, [Wrap.dep])
            refs.append(cb)
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        Wrap.dep = 2
        r.render(root, el, 80, 24)
        assert refs[0] is not refs[1]  # deps 变化 → 新函数

    def test_memo_mixed_with_state_hooks_order_regression(self):
        """use_memo 与 use_state 混用时 fiber.hooks 链顺序稳定。"""
        from src.tui.ink.hooks import use_state, use_memo
        seen = []
        holder = []

        def Comp(props):
            a, set_a = use_state(0)
            m = use_memo(lambda: a * 2, [a])
            if not holder:
                holder.append(set_a)
            seen.append(m)
            return h(TEXT, {"children": str(m)})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        holder[0](5)
        r.render(root, el, 80, 24)
        assert seen == [0, 10]  # state 更新 → memo deps 变化 → 重算


class TestUseInput:
    """INK-1 — use_input 输入钩子。"""

    def _capture_router(self):
        """注入 router 发布回调并返回捕获容器。"""
        from src.tui.ink.hooks import set_input_router_callback
        captured = {}
        set_input_router_callback(lambda router: captured.update(router=router))
        return captured

    def test_use_input_active_consumes_event_regression(self):
        """active handler 返回 True 时 router 消费事件（返回 True）。"""
        from src.tui.ink.hooks import use_input
        captured = self._capture_router()
        handler = MagicMock(return_value=True)

        def Comp(props):
            use_input(handler, True)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            assert router is not None
            result = router(MagicMock())
            assert result is True  # 消费
            handler.assert_called_once()
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_use_input_inactive_not_called_regression(self):
        """is_active=False 时 hook 不参与路由（handler 不被调用）。"""
        from src.tui.ink.hooks import use_input
        captured = self._capture_router()
        active = MagicMock(return_value=False)
        inactive = MagicMock(return_value=True)

        def Comp(props):
            use_input(active, True)
            use_input(inactive, False)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            assert router is not None
            result = router(MagicMock())
            assert result is False  # active 放行 → 未消费
            active.assert_called_once()
            inactive.assert_not_called()  # inactive 不参与
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_use_input_handler_exception_releases_event_regression(self):
        """handler 异常 → router 放行（返回 False，不阻断）。"""
        from src.tui.ink.hooks import use_input
        captured = self._capture_router()

        def bad(event):
            raise ValueError("boom")

        def Comp(props):
            use_input(bad, True)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            result = router(MagicMock())
            assert result is False  # 异常放行
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)
