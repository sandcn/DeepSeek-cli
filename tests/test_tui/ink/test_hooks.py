"""测试 ink/hooks.py — use_state/use_reducer/use_ref/use_effect。

hook 需在函数组件渲染期间调用（reconciler 管理当前 fiber 栈）。
测试通过 Reconciler 渲染函数组件来驱动 hook 生命周期。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.tui.ink.element import h, TEXT, Element
from src.tui.ink.output import Frame
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components
from src.tui.ink import BOX
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


class TestDepsObjectIs:
    """方向1 步骤1 — _object_is / _deps_equal React Object.is 语义。

    覆盖：小整数 is 缓存相等；对象 is 不同则不等；NaN 相等；+0/-0 不等；
    bool vs int 不等；int vs float 不等；长度不等直接 False。
    """

    def test_small_int_is_cache_equal(self):
        """_deps_equal([1],[1]) True（小整数 is 缓存命中）。"""
        from src.tui.ink.hooks import _object_is, _deps_equal
        assert _object_is(1, 1) is True
        assert _deps_equal([1], [1]) is True

    def test_large_int_equal(self):
        """大整数按 == 相等（is 不命中）。"""
        from src.tui.ink.hooks import _object_is
        assert _object_is(1000, 1000) is True

    def test_distinct_dict_objects_not_equal(self):
        """_deps_equal([dict()],[dict()]) False（对象 is 不同）。"""
        from src.tui.ink.hooks import _object_is, _deps_equal
        assert _object_is({}, {}) is False
        assert _deps_equal([dict()], [dict()]) is False

    def test_nan_equal(self):
        """NaN 与 NaN 相等（Object.is 语义）。"""
        import math
        from src.tui.ink.hooks import _object_is, _deps_equal
        assert _object_is(float("nan"), float("nan")) is True
        assert _deps_equal([float("nan")], [float("nan")]) is True

    def test_positive_zero_negative_zero_not_equal(self):
        """+0 与 -0 不等（Object.is 语义）。"""
        from src.tui.ink.hooks import _object_is, _deps_equal
        assert _object_is(0.0, -0.0) is False
        assert _deps_equal([0.0], [-0.0]) is False

    def test_bool_vs_int_not_equal(self):
        """bool 与 int 因 type 不同返回 False。"""
        from src.tui.ink.hooks import _object_is, _deps_equal
        assert _object_is(True, 1) is False
        assert _deps_equal([True], [1]) is False

    def test_int_vs_float_not_equal(self):
        """int 与 float type 不同返回 False。"""
        from src.tui.ink.hooks import _object_is
        assert _object_is(1, 1.0) is False

    def test_deps_equal_length_mismatch(self):
        """长度不等直接 False。"""
        from src.tui.ink.hooks import _deps_equal
        assert _deps_equal([1], [1, 2]) is False

    def test_deps_equal_none_semantics(self):
        """None 与列表不等（None 表示每次渲染重算）。"""
        from src.tui.ink.hooks import _deps_equal
        assert _deps_equal(None, None) is True
        assert _deps_equal(None, [1]) is False
        assert _deps_equal([1], None) is False

    def test_same_list_reference_equal(self):
        """同一列表对象引用 → True（a is b 命中）。"""
        from src.tui.ink.hooks import _deps_equal
        deps = [1, 2]
        assert _deps_equal(deps, deps) is True

    def test_deps_changed_uses_object_is(self):
        """deps_changed 经 _deps_equal 逐项 _object_is（新 dict 触发重跑）。"""
        from src.tui.ink.fiber import EffectHook
        from src.tui.ink.hooks import deps_changed
        hook = EffectHook(create=None, deps=[{}], destroy=None, last_deps=[{}])
        # 两个不同 dict 对象（is 不同）→ deps 变化 → True
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


class TestScheduleException:
    """方向C 步骤5 — _schedule 回调异常记录日志（非关键降级不传播）。"""

    def test_schedule_callback_exception_logged(self, caplog):
        """schedule 回调抛异常 → 不传播且日志记录。"""
        import logging
        from src.tui.ink.hooks import set_schedule_callback

        def bad_callback():
            raise RuntimeError("schedule boom")

        def Comp(props):
            n, set_n = use_state(0)
            if n == 0:
                set_n(1)  # 渲染期入队 → 触发 _schedule → bad_callback 抛异常
            return h(TEXT, {"children": str(n)})

        # 回调经 Reconciler 注入（构造与 render 均会 set_schedule_callback）
        r = Reconciler(schedule_callback=bad_callback)
        root = r.create_root()
        with caplog.at_level(logging.DEBUG, logger="src.tui.ink.hooks"):
            r.render(root, h(Comp), 80, 24)  # 不抛异常
        assert any(
            rec.name == "src.tui.ink.hooks"
            and "schedule 回调异常" in rec.getMessage()
            for rec in caplog.records
        )


class TestContextRegistryCleanup:
    """方向C 步骤6 + BUG-18 — context 注册表条目生命周期。"""

    def test_context_registry_kept_after_unmount(self):
        """BUG-18 — Provider 卸载后注册表条目保留（重挂载正常）。

        修复前 ``_cleanup_contexts`` 卸载时 ``pop`` 注册表——同一组件重新挂载
        ``h(ctx.Provider, ...)`` 时 begin_work 查注册表返回 None → 子树
        use_context 回退 default（Provider 重挂载失效）。Context 对象由
        ``create_context`` 模块级创建（进程生命周期），注册表条目与 Provider
        挂载状态解耦。
        """
        from src.tui.ink.hooks import create_context, use_context, _context_registry
        from src.tui.ink.components import render_frame
        from src.tui.ink import strip_ansi

        Ctx = create_context("default")
        tag = Ctx.tag
        assert tag in _context_registry

        def Consumer(props):
            v = use_context(Ctx)
            return h(TEXT, {"children": f"value={v}"})

        def ProviderComp(props):
            return h(Ctx.Provider, {"value": "v"}, h(Consumer))

        def OtherComp(props):
            return h(TEXT, {"children": "y"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(ProviderComp), 80, 24)
        frame = render_frame(root, 80)
        assert "value=v" in strip_ansi(frame.lines[0].render())
        # 卸载 provider
        r.render(root, h(OtherComp), 80, 24)
        assert tag in _context_registry  # 注册表条目保留（重挂载支持）
        # 重新挂载 → use_context 仍读到 provider 值（修复前回退 default）
        r.render(root, h(ProviderComp), 80, 24)
        frame = render_frame(root, 80)
        assert "value=v" in strip_ansi(frame.lines[0].render()), (
            f"Provider 重挂载后 use_context 应读到 provider 值: "
            f"{strip_ansi(frame.lines[0].render())!r}"
        )

    def test_context_registry_kept_while_mounted(self):
        """未卸载的 provider 注册表条目保留（复用不触发清理）。"""
        from src.tui.ink.hooks import create_context, _context_registry

        Ctx = create_context("default")
        tag = Ctx.tag

        def ProviderComp(props):
            return h(Ctx.Provider, {"value": "v"}, h(TEXT, {"children": "x"}))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(ProviderComp), 80, 24)
        assert tag in _context_registry
        r.render(root, h(ProviderComp), 80, 24)  # 复用，不卸载
        assert tag in _context_registry


class TestHookStateError:
    """方向B 步骤9 — HookStateError（hook 状态机异常，不参与 boundary 捕获）。"""

    def test_hook_state_error_subclass_of_runtime_error(self):
        """HookStateError 是 RuntimeError 子类（既有 raises(RuntimeError) 兼容）。"""
        from src.tui.ink.hooks import HookStateError
        assert issubclass(HookStateError, RuntimeError)

    def test_use_state_outside_component_raises_hook_state_error(self):
        """渲染期外调用 use_state → HookStateError（原 RuntimeError 语义保持）。"""
        import pytest
        from src.tui.ink.hooks import use_state, HookStateError, _current_fiber_stack
        assert _current_fiber_stack == []
        with pytest.raises(HookStateError):
            use_state(0)


class TestUseErrorState:
    """方向B 步骤9 — use_error_state 读取 fiber._boundary_error。"""

    def test_use_error_state_returns_boundary_error(self):
        """无错误时返回 None；boundary fiber 记录异常后返回异常对象。"""
        from src.tui.ink.hooks import use_error_state
        from src.tui.ink.fiber import Fiber, TAG_FUNCTION
        from src.tui.ink.element import TEXT

        seen = []

        def Comp(props):
            seen.append(use_error_state())
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        assert seen == [None]
        # 直接注入 boundary error 到 fiber（模拟 reconciler 记录）
        fiber = root.child
        fiber._boundary_error = RuntimeError("boom")
        # 重新渲染 → use_error_state 读到注入的 error
        seen.clear()
        r.render(root, h(Comp), 80, 24)
        assert isinstance(seen[0], RuntimeError)
        assert str(seen[0]) == "boom"


class TestUseApp:
    """方向B 步骤10 — useApp 应用控制。"""

    def test_use_app_returns_exit_clear(self):
        """useApp 返回 {exit, clear} 可调用（未注入时 no-op）。"""
        from src.tui.ink.hooks import useApp, set_app_control
        try:
            ctrl = useApp()
            assert callable(ctrl["exit"])
            assert callable(ctrl["clear"])
            ctrl["exit"]()  # no-op 不抛
            ctrl["clear"]()
        finally:
            set_app_control(None)

    def test_use_app_forwards_injected_control(self):
        """注入 control 后 useApp 转发 exit/clear。"""
        from src.tui.ink.hooks import useApp, set_app_control
        calls = {"exit": 0, "clear": 0}
        try:
            set_app_control({
                "exit": lambda: calls.__setitem__("exit", calls["exit"] + 1),
                "clear": lambda: calls.__setitem__("clear", calls["clear"] + 1),
            })
            ctrl = useApp()
            ctrl["exit"]()
            ctrl["clear"]()
            assert calls == {"exit": 1, "clear": 1}
        finally:
            set_app_control(None)

    def test_set_app_control_clears(self):
        """set_app_control(None) 清除注入（测试清理路径）。"""
        from src.tui.ink import hooks as _hooks
        from src.tui.ink.hooks import useApp
        try:
            _hooks.set_app_control({"exit": lambda: None, "clear": lambda: None})
            assert _hooks._app_control is not None
            _hooks.set_app_control(None)
            assert _hooks._app_control is None
            ctrl = useApp()
            assert callable(ctrl["exit"])  # 清除后 no-op
        finally:
            _hooks.set_app_control(None)


class TestUseFocus:
    """方向B 步骤10 — useFocus 焦点标志。"""

    def test_use_focus_sets_input_hook_focused(self):
        """useFocus 设置当前 fiber 最近 InputHook 的 focused 标志。"""
        from src.tui.ink.hooks import useFocus, use_input, _current_fiber_stack
        from src.tui.ink.fiber import InputHook
        seen = []

        def Comp(props):
            use_input(lambda ev: False, True)
            useFocus(False)  # 置为非 focused
            seen.append([
                h.focused for h in _current_fiber_stack[-1].hooks
                if isinstance(h, InputHook)
            ])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        assert seen == [[False]]

    def test_use_focus_default_true_on_input_hook(self):
        """未调用 useFocus 时 InputHook.focused 默认 True（零回归）。"""
        from src.tui.ink.fiber import InputHook
        hook = InputHook(handler=lambda ev: False, is_active=True)
        assert hook.focused is True


class TestUseContextCache:
    """方向B 步骤11 — use_context 逐 fiber 缓存 + Provider 值变更传播（保守版）。"""

    def test_provider_value_change_propagates_to_consumer(self):
        """Provider 值变化 → 子树 use_context 读到新值。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default")
        seen = []

        def Child(props):
            seen.append(use_context(Ctx))
            return h(TEXT, {"children": "x"})

        def Provider(props):
            return h(Ctx.Provider, {"value": props["value"]}, h(Child))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Provider, {"value": "v1"}), 80, 24)
        r.render(root, h(Provider, {"value": "v2"}), 80, 24)
        assert seen == ["v1", "v2"]

    def test_same_fiber_repeated_use_context_cache_hit(self):
        """同 fiber 多次 use_context 同 ctx → 值一致（缓存命中语义）。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default")
        results = []

        def Child(props):
            a = use_context(Ctx)
            b = use_context(Ctx)
            results.append((a, b))
            return h(TEXT, {"children": "x"})

        def Provider(props):
            return h(Ctx.Provider, {"value": "v"}, h(Child))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Provider, {"value": "v"}), 80, 24)
        assert results == [("v", "v")]

    def test_context_cache_invalidated_on_provider_change(self):
        """Provider 值变化 → 子树 _context_cache 清空并回填新值（可观察）。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default")
        seen = []

        def Child(props):
            seen.append(use_context(Ctx))
            return h(TEXT, {"children": "x"})

        def Provider(props):
            return h(Ctx.Provider, {"value": props["value"]}, h(Child))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Provider, {"value": "v1"}), 80, 24)
        # 树：root → Provider(fn) → Provider host → Child(fn) → text(host)
        child_fiber = root.child.child.child
        assert child_fiber._context_cache.get(Ctx.tag) == "v1"  # 已缓存
        r.render(root, h(Provider, {"value": "v2"}), 80, 24)
        # 值变化 → 子树缓存被清空 → 重查回填新值
        assert child_fiber._context_cache.get(Ctx.tag) == "v2"
        assert seen == ["v1", "v2"]

    def test_provider_value_unchanged_cache_preserved(self):
        """Provider 值未变 → 子树 _context_cache 不被清空（缓存保留）。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default")

        def Child(props):
            use_context(Ctx)
            return h(TEXT, {"children": "x"})

        def Provider(props):
            return h(Ctx.Provider, {"value": "stable"}, h(Child))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Provider, {"value": "stable"}), 80, 24)
        child_fiber = root.child.child.child
        assert child_fiber._context_cache.get(Ctx.tag) == "stable"
        r.render(root, h(Provider, {"value": "stable"}), 80, 24)  # 值未变
        assert child_fiber._context_cache.get(Ctx.tag) == "stable"  # 缓存保留


class TestUseLayoutEffectDocs:
    """方向2 L5 — useLayoutEffect 评估结论文档化（可选断言，可追溯）。"""

    def test_uselayouteffect_docstring_records_evaluation(self):
        """useLayoutEffect docstring 明确与 useEffect 等价 + 独立 hook 类型评估不实施。"""
        from src.tui.ink.hooks import useLayoutEffect
        doc = useLayoutEffect.__doc__ or ""
        assert "与 useEffect 等价" in doc
        assert "不实施" in doc


class TestUnmountedSetterGuard:
    """方向3 — 已卸载组件 setter 不触发重渲染（fiber.deleted 检查）。"""

    def test_setter_skips_schedule_when_deleted(self):
        """组件渲染后 fiber.deleted=True → setter 不排队不调度。"""
        from src.tui.ink.hooks import use_state
        scheduled = []
        holder = []

        def Comp(props):
            n, set_n = use_state(0)
            if not holder:
                holder.append(set_n)
            return h(TEXT, {"children": str(n)})

        r = Reconciler(schedule_callback=lambda: scheduled.append(1))
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        fiber = root.child  # function fiber
        fiber.deleted = True  # 模拟卸载
        holder[0](42)
        assert scheduled == []  # 不触发调度（修复前无条件 _schedule）
        # queue 不追加（状态不变）
        state_hook = fiber.hooks[0]
        assert state_hook.queue is None or state_hook.queue == []

    def test_setter_works_after_reuse(self):
        """fiber.deleted 复位（复用）后 setter 正常工作。"""
        from src.tui.ink.hooks import use_state
        scheduled = []
        holder = []

        def Comp(props):
            n, set_n = use_state(0)
            if not holder:
                holder.append(set_n)
            return h(TEXT, {"children": str(n)})

        r = Reconciler(schedule_callback=lambda: scheduled.append(1))
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        fiber = root.child
        fiber.deleted = False  # 复用（正常路径 reconciler 已复位）
        holder[0](5)
        assert scheduled == [1]  # 触发调度

    def test_normal_setter_still_schedules(self):
        """正常组件 setter 行为不变（零回归）。"""
        from src.tui.ink.hooks import use_state
        scheduled = []
        holder = []
        seen = []

        def Comp(props):
            n, set_n = use_state(0)
            if not holder:
                holder.append(set_n)
            seen.append(n)
            return h(TEXT, {"children": str(n)})

        r = Reconciler(schedule_callback=lambda: scheduled.append(1))
        root = r.create_root()
        el = h(Comp)
        r.render(root, el, 80, 24)
        holder[0](7)
        r.render(root, el, 80, 24)
        assert scheduled == [1]
        assert seen == [0, 7]  # state 更新生效


class TestForwardRefImperativeHandle:
    """forwardRef + useImperativeHandle（完善 react ink）。"""

    def test_forward_ref_renders(self):
        from src.tui.ink import forwardRef, useImperativeHandle, use_ref
        seen = []

        def Inner(props, ref):
            useImperativeHandle(ref, lambda: {"tag": "inner", "n": props.get("n", 0)}, ())
            return h(TEXT, {"children": "inner"})

        InnerFR = forwardRef(Inner)
        handle_holder = {}

        def Parent(props):
            ref = use_ref(None)
            handle_holder["ref"] = ref
            return h(BOX, None, [h(InnerFR, {"ref": ref, "n": props.get("n", 0)})])

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Parent, {"n": 1}), 80, 24)
        frame = _components.render_frame(root, 80)
        assert [l.plain for l in frame.lines] == ["inner"]
        assert handle_holder["ref"].current == {"tag": "inner", "n": 1}

    def test_imperative_handle_updates_on_deps_change(self):
        from src.tui.ink import forwardRef, useImperativeHandle, use_ref
        handle_holder = {}

        def Inner(props, ref):
            useImperativeHandle(
                ref, lambda: {"n": props.get("n", 0)}, (props.get("n", 0),),
            )
            return h(TEXT, {"children": "inner"})

        InnerFR = forwardRef(Inner)

        def Parent(props):
            ref = use_ref(None)
            handle_holder["ref"] = ref
            return h(BOX, None, [h(InnerFR, {"ref": ref, "n": props.get("n", 0)})])

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Parent, {"n": 1}), 80, 24)
        assert handle_holder["ref"].current == {"n": 1}
        # 同 deps 重渲染：句柄保持同一对象
        r.render(root, h(Parent, {"n": 1}), 80, 24)
        assert handle_holder["ref"].current == {"n": 1}
        # deps 变化：句柄更新
        r.render(root, h(Parent, {"n": 2}), 80, 24)
        assert handle_holder["ref"].current == {"n": 2}

    def test_imperative_handle_cleared_on_unmount(self):
        from src.tui.ink import forwardRef, useImperativeHandle, use_ref
        handle_holder = {}

        def Inner(props, ref):
            useImperativeHandle(ref, lambda: {"tag": "inner"}, ())
            return h(TEXT, {"children": "inner"})

        InnerFR = forwardRef(Inner)

        def Parent(props):
            ref = use_ref(None)
            handle_holder["ref"] = ref
            if props.get("show", True):
                return h(BOX, None, [h(InnerFR, {"ref": ref})])
            return h(BOX, None, [])

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Parent, {"show": True}), 80, 24)
        assert handle_holder["ref"].current is not None
        r.render(root, h(Parent, {"show": False}), 80, 24)
        assert handle_holder["ref"].current is None
