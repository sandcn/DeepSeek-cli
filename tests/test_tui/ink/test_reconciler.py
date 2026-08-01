"""测试 ink/reconciler.py — 调和 / key diff / fiber 复用。"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.tui.ink.element import h, BOX, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.fiber import Fiber, TAG_ROOT, TAG_FUNCTION


def _collect_hosts(root: Fiber) -> list[Fiber]:
    """前序遍历收集所有 host fiber。"""
    out = []

    def walk(f: Fiber | None):
        f2 = f
        while f2 is not None:
            if f2.is_host:
                out.append(f2)
            walk(f2.child)
            f2 = f2.sibling

    walk(root)
    return out


class TestMount:
    """挂载渲染。"""

    def test_mount_creates_host_tree(self):
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, h(TEXT, {"children": "hi"}), h(TEXT, {"children": "yo"}))
        r.render(root, el, 80, 24)
        assert root.tag == TAG_ROOT
        assert root.child is not None
        assert root.child.type == BOX
        # 两个 TEXT 兄弟
        texts = [f for f in _collect_hosts(root) if f.type == "text"]
        assert len(texts) == 2
        assert texts[0].props["children"] == "hi"
        assert texts[1].props["children"] == "yo"

    def test_layout_boxes_assigned(self):
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"width": 20}, h(TEXT, {"children": "abc"}))
        r.render(root, el, 80, 24)
        box = root.child
        assert box.layout_box is not None
        assert box.layout_box.w == 20
        text = box.child
        assert text.layout_box is not None
        assert text.layout_box.w == 20
        assert text.layout_box.h == 1


class TestUpdate:
    """更新渲染与 fiber 复用。"""

    def test_same_key_reuses_fiber(self):
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"key": "k"}, h(TEXT, {"children": "a"}))
        r.render(root, el, 80, 24)
        first = root.child
        # 更新 props（同 key/type）
        el2 = h(BOX, {"key": "k", "width": 30}, h(TEXT, {"children": "b"}))
        r.render(root, el2, 80, 24)
        assert root.child is first  # 复用同一 fiber
        assert root.child.props["width"] == 30
        assert root.child.child.props["children"] == "b"

    def test_key_change_creates_new_fiber(self):
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(BOX, {"key": "a"}, h(TEXT, {"children": "x"})), 80, 24)
        old = root.child
        r.render(root, h(BOX, {"key": "b"}, h(TEXT, {"children": "y"})), 80, 24)
        assert root.child is not old
        assert old.deleted is True

    def test_props_update_without_reset(self):
        """同 key/type 更新 props 不重建 fiber（hooks 状态保留）。"""
        from src.tui.ink.hooks import use_state
        seen = []

        def Comp(props):
            n, _ = use_state(0)
            seen.append((props.get("label"), n))
            return h(TEXT, {"children": props.get("label", "")})

        r = Reconciler()
        root = r.create_root()
        el = h(Comp, {"label": "A"})
        r.render(root, el, 80, 24)
        # 更新 props（同类型）
        el2 = h(Comp, {"label": "B"})
        r.render(root, el2, 80, 24)
        assert seen == [("A", 0), ("B", 0)]


class TestReconcileChildren:
    """子列表调和（key diff）。"""

    def test_reorder_by_key(self):
        """key 相同但顺序变化 → 复用 fiber 调整 sibling。"""
        r = Reconciler()
        root = r.create_root()

        def make(order):
            return h(
                BOX, None,
                h(TEXT, {"key": "1", "children": "one"}),
                h(TEXT, {"key": "2", "children": "two"}),
            )

        el = make(["1", "2"])
        r.render(root, el, 80, 24)
        texts1 = [f for f in _collect_hosts(root) if f.type == "text"]
        el2 = h(
            BOX, None,
            h(TEXT, {"key": "2", "children": "two"}),
            h(TEXT, {"key": "1", "children": "one"}),
        )
        r.render(root, el2, 80, 24)
        texts2 = [f for f in _collect_hosts(root) if f.type == "text"]
        # 两个 fiber 都被复用（同一对象）
        assert {id(t) for t in texts1} == {id(t) for t in texts2}
        # 顺序变化
        assert texts2[0].props["children"] == "two"
        assert texts2[1].props["children"] == "one"

    def test_remove_child_marks_deleted(self):
        r = Reconciler()
        root = r.create_root()
        r.render(
            root,
            h(BOX, None, h(TEXT, {"key": "1", "children": "a"}), h(TEXT, {"key": "2", "children": "b"})),
            80, 24,
        )
        removed = [f for f in _collect_hosts(root) if f.type == "text" and f.props["children"] == "a"][0]
        r.render(root, h(BOX, None, h(TEXT, {"key": "2", "children": "b"})), 80, 24)
        assert removed.deleted is True


class TestMovedFlag:
    """方向B 步骤11 — keyed 列表调和 moved 标记（位置变化信息）。"""

    def _make_list(self, order):
        return h(
            BOX, None,
            *(h(TEXT, {"key": label, "children": label}) for label in order),
        )

    def test_reorder_marks_moved(self):
        """keyed 列表重排 → fiber 复用 + moved 标记准确（位置不变为 False）。"""
        r = Reconciler()
        root = r.create_root()
        r.render(root, self._make_list(["a", "b", "c"]), 80, 24)
        texts1 = [f for f in _collect_hosts(root) if f.type == "text"]
        assert all(t.moved is False for t in texts1)  # 首渲染无移动
        # 重排：a 不动，c/b 互换
        r.render(root, self._make_list(["a", "c", "b"]), 80, 24)
        texts2 = [f for f in _collect_hosts(root) if f.type == "text"]
        # fiber 全部复用（同一对象，不重建）
        assert {id(t) for t in texts1} == {id(t) for t in texts2}
        by_label = {t.props["children"]: t for t in texts2}
        assert by_label["a"].moved is False  # 0→0 不变
        assert by_label["c"].moved is True   # 2→1 移动
        assert by_label["b"].moved is True   # 1→2 移动

    def test_insert_middle_marks_tail_moved(self):
        """插入中间项 → 后续项 moved（位置右移）；新项默认 False。"""
        r = Reconciler()
        root = r.create_root()
        r.render(root, self._make_list(["a", "b", "c"]), 80, 24)
        r.render(root, self._make_list(["a", "x", "b", "c"]), 80, 24)
        texts = [f for f in _collect_hosts(root) if f.type == "text"]
        by_label = {t.props["children"]: t for t in texts}
        assert by_label["a"].moved is False  # 0→0 不变
        assert by_label["x"].moved is False  # 新创建（默认 False）
        assert by_label["b"].moved is True   # 1→2 右移
        assert by_label["c"].moved is True   # 2→3 右移

    def test_reorder_preserves_hook_state(self):
        """keyed 重排 → fiber 复用保留 hook 状态（重排不重建）。"""
        from src.tui.ink.hooks import use_state
        seen = []
        holder = {}

        def Item(props):
            n, set_n = use_state(0)
            holder[props["label"]] = set_n
            seen.append((props["label"], n))
            return h(TEXT, {"children": props["label"]})

        r = Reconciler()
        root = r.create_root()

        def make(order):
            return h(
                BOX, None,
                *(h(Item, {"key": label, "label": label}) for label in order),
            )

        r.render(root, make(["a", "b", "c"]), 80, 24)
        holder["a"](42)  # 更新 a 状态
        r.render(root, make(["a", "c", "b"]), 80, 24)  # 重排
        # a 的 fiber 被复用 → 状态保持 42（若重建会重置为 0）
        a_frames = [n for lbl, n in seen if lbl == "a"]
        assert a_frames == [0, 42]


class TestRoot:
    """根 fiber。"""

    def test_create_root(self):
        root = Reconciler.create_root()
        assert root.tag == TAG_ROOT

    def test_function_fiber_tag(self):
        def Comp(props):
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        fn = root.child
        assert fn.tag == TAG_FUNCTION
        assert fn.child is not None
        assert fn.child.type == "text"


class TestContext:
    """INK-3 — create_context / use_context / Provider host。"""

    def test_create_context_provider_value_regression(self):
        """Provider 下子组件 use_context 取到 value。"""
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
        r.render(root, h(Provider, {"value": "hello"}), 80, 24)
        assert seen == ["hello"]

    def test_use_context_default_regression(self):
        """无 Provider 时 use_context 返回 default。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default-val")

        seen = []

        def Child(props):
            seen.append(use_context(Ctx))
            return h(TEXT, {"children": "x"})

        def NoProvider(props):
            return h(Child)

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(NoProvider), 80, 24)
        assert seen == ["default-val"]

    def test_use_context_nested_override_regression(self):
        """内层 Provider 覆盖外层 Provider。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default")

        seen = []

        def Child(props):
            seen.append(use_context(Ctx))
            return h(TEXT, {"children": "x"})

        def Inner(props):
            return h(Ctx.Provider, {"value": "inner"}, h(Child))

        def Outer(props):
            return h(Ctx.Provider, {"value": "outer"}, h(Inner))

        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Outer), 80, 24)
        assert seen == ["inner"]

    def test_context_provider_reset_across_renders_regression(self):
        """Provider 每次渲染重置 contexts（不残留旧值）。"""
        from src.tui.ink.hooks import create_context, use_context
        Ctx = create_context("default")

        seen = []

        def Child(props):
            seen.append(use_context(Ctx))
            return h(TEXT, {"children": "x"})

        def Comp(props):
            # 无 Provider：use_context 应回退 default（contexts 已被重置）
            return h(Child)

        r = Reconciler()
        root = r.create_root()
        # 先渲染一次 Provider 设置 contexts
        r.render(root, h(Ctx.Provider, {"value": "v"}, h(Child)), 80, 24)
        # 再渲染无 Provider 的组件树——use_context 应返回 default（不残留 "v"）
        r.render(root, h(Comp), 80, 24)
        assert seen == ["v", "default"]


class TestInputRouter:
    """INK-1 — reconciler 构建 composite input router。"""

    def _capture(self):
        from src.tui.ink.hooks import set_input_router_callback
        captured = {}
        set_input_router_callback(lambda router: captured.update(router=router))
        return captured

    def test_input_router_built_from_hooks_regression(self):
        """两个组件一个 active 一个 inactive → router 只调 active。"""
        from src.tui.ink.hooks import use_input
        captured = self._capture()
        active_handler = MagicMock(return_value=True)
        inactive_handler = MagicMock(return_value=True)

        def Comp(props):
            use_input(active_handler, True)
            use_input(inactive_handler, False)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            assert router is not None
            result = router(MagicMock())
            assert result is True
            active_handler.assert_called_once()
            inactive_handler.assert_not_called()
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_input_router_none_without_hooks_regression(self):
        """无 use_input hook 时发布 None（输入走旧路径，零行为变化）。"""
        captured = self._capture()

        def Comp(props):
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            assert captured["router"] is None
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_input_router_multiple_hooks_in_order_regression(self):
        """同帧多个 use_input 按 hook 顺序调用；首个消费后停止。"""
        from src.tui.ink.hooks import use_input
        captured = self._capture()
        first = MagicMock(return_value=False)
        second = MagicMock(return_value=True)
        third = MagicMock(return_value=False)

        def Comp(props):
            use_input(first, True)
            use_input(second, True)
            use_input(third, True)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            result = router(MagicMock())
            assert result is True
            first.assert_called_once()
            second.assert_called_once()
            third.assert_not_called()  # 第二个已消费 → 第三个不调用
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)


class TestEffectException:
    """方向C 步骤5 — effect 异常记录日志（含 fiber 上下文，不中断渲染）。"""

    def test_run_destroy_exception_logged(self, caplog):
        """_run_destroy 捕获 destroy 异常并记录 fiber 上下文（不抛）。"""
        import logging
        from src.tui.ink.fiber import EffectHook, Fiber, TAG_FUNCTION

        def BoomComp(props):
            return h(TEXT, {"children": "x"})

        def destroy():
            raise RuntimeError("destroy boom")

        hook = EffectHook(create=None, deps=None, destroy=destroy, last_deps=None)
        fiber = Fiber(TAG_FUNCTION, BoomComp, {})
        r = Reconciler()
        with caplog.at_level(logging.DEBUG, logger="src.tui.ink.reconciler"):
            r._run_destroy(fiber, hook)  # 不抛异常
        assert any(
            rec.name == "src.tui.ink.reconciler"
            and "effect 销毁执行异常" in rec.getMessage()
            and "BoomComp" in rec.getMessage()
            for rec in caplog.records
        )

    def test_effect_create_exception_logged(self, caplog):
        """live effect create 抛异常 → 渲染不崩溃 + 日志含 fiber 上下文。"""
        import logging
        from src.tui.ink.hooks import use_effect

        def BadComp(props):
            def create():
                raise RuntimeError("create boom")
            use_effect(create, [])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        with caplog.at_level(logging.DEBUG, logger="src.tui.ink.reconciler"):
            r.render(root, h(BadComp), 80, 24)  # 不抛异常
        assert any(
            rec.name == "src.tui.ink.reconciler"
            and "effect 执行异常" in rec.getMessage()
            and "BadComp" in rec.getMessage()
            for rec in caplog.records
        )

    def test_effect_exception_does_not_stop_batch(self, caplog):
        """同 fiber 多个 effect：首个 create 抛异常 → 后续 effect 仍执行。"""
        import logging
        from src.tui.ink.hooks import use_effect

        order = []

        def Comp(props):
            def create1():
                raise RuntimeError("create1 boom")

            def create2():
                order.append("second")

            use_effect(create1, [])
            use_effect(create2, [])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        with caplog.at_level(logging.DEBUG, logger="src.tui.ink.reconciler"):
            r.render(root, h(Comp), 80, 24)  # 不抛异常
        assert order == ["second"]  # 后续 effect 不被中断
        assert any(
            rec.name == "src.tui.ink.reconciler"
            and "effect 执行异常" in rec.getMessage()
            and "Comp" in rec.getMessage()
            for rec in caplog.records
        )


class TestErrorBoundary:
    """方向B 步骤9 — ErrorBoundary 函数组件异常局部降级。"""

    def test_boundary_catches_child_exception_renders_fallback(self):
        """boundary 内异常被捕获并渲染 fallback（不崩溃）。"""
        import pytest
        from src.tui.ink.error_boundary import ErrorBoundary

        def BoomComp(props):
            raise RuntimeError("boom")

        def Fallback(error):
            return h(TEXT, {"children": f"fallback:{type(error).__name__}"})

        r = Reconciler()
        root = r.create_root()
        el = h(ErrorBoundary, {"fallback": Fallback, "children": h(BoomComp)})
        r.render(root, el, 80, 24)  # 不抛异常
        texts = [f.props["children"] for f in _collect_hosts(root) if f.type == "text"]
        assert any(t == "fallback:RuntimeError" for t in texts)

    def test_boundary_default_fallback_placeholder(self):
        """未提供 fallback 时渲染默认占位（含异常类型与消息）。"""
        from src.tui.ink.error_boundary import ErrorBoundary

        def BoomComp(props):
            raise RuntimeError("boom")

        r = Reconciler()
        root = r.create_root()
        el = h(ErrorBoundary, {"children": h(BoomComp)})
        r.render(root, el, 80, 24)  # 不抛异常
        texts = [f.props["children"] for f in _collect_hosts(root) if f.type == "text"]
        assert any("RuntimeError" in t and "boom" in t for t in texts)

    def test_boundary_outside_exception_propagates(self):
        """无 boundary 时异常照常传播（崩溃恢复语义保留）。"""
        import pytest

        def BoomComp(props):
            raise RuntimeError("boom")

        r = Reconciler()
        root = r.create_root()
        with pytest.raises(RuntimeError):
            r.render(root, h(BoomComp), 80, 24)

    def test_on_error_called_once(self):
        """onError 只在异常首次发生时回调一次。"""
        from unittest.mock import MagicMock
        from src.tui.ink.error_boundary import ErrorBoundary
        on_error = MagicMock()

        def BoomComp(props):
            raise RuntimeError("boom")

        def Fallback(error):
            return h(TEXT, {"children": "fb"})

        r = Reconciler()
        root = r.create_root()
        el = h(ErrorBoundary, {"fallback": Fallback, "onError": on_error, "children": h(BoomComp)})
        r.render(root, el, 80, 24)
        on_error.assert_called_once()
        # 下一帧（fallback 仍渲染，不再调用子组件）→ 不重复回调
        r.render(root, el, 80, 24)
        on_error.assert_called_once()

    def test_nested_boundary_inner_fails_outer_unaffected(self):
        """嵌套 boundary：内层失败 → 内层 fallback，外层不受影响。"""
        from src.tui.ink.error_boundary import ErrorBoundary

        def BoomComp(props):
            raise RuntimeError("inner boom")

        def InnerFallback(error):
            return h(TEXT, {"children": "inner-fb"})

        def OuterFallback(error):
            return h(TEXT, {"children": "outer-fb"})

        el = h(ErrorBoundary, {
            "fallback": OuterFallback,
            "children": h(ErrorBoundary, {
                "fallback": InnerFallback,
                "children": h(BoomComp),
            }),
        })
        r = Reconciler()
        root = r.create_root()
        r.render(root, el, 80, 24)  # 不抛异常
        texts = [f.props["children"] for f in _collect_hosts(root) if f.type == "text"]
        assert any(t == "inner-fb" for t in texts)
        assert not any(t == "outer-fb" for t in texts)

    def test_fallback_exception_propagates(self):
        """fallback 自身抛异常 → 传播（递归边界：不二次兜底）。"""
        import pytest
        from src.tui.ink.error_boundary import ErrorBoundary

        def BoomComp(props):
            raise RuntimeError("boom")

        def BadFallback(error):
            raise ValueError("fallback boom")

        r = Reconciler()
        root = r.create_root()
        with pytest.raises(ValueError):
            r.render(root, h(ErrorBoundary, {"fallback": BadFallback, "children": h(BoomComp)}), 80, 24)

    def test_hook_state_error_propagates_even_inside_boundary(self):
        """hook 顺序/类型错误（HookStateError，编程错误）不参与边界捕获（仍传播）。"""
        import pytest
        from src.tui.ink.error_boundary import ErrorBoundary
        from src.tui.ink.hooks import use_state, use_effect

        state = {"first": True}

        def BadHooksComp(props):
            # 首次渲染 use_state；二次渲染 use_effect → hook 类型不一致
            if state["first"]:
                state["first"] = False
                use_state(0)
            else:
                use_effect(lambda: None, [])
            return h(TEXT, {"children": "x"})

        r = Reconciler()
        root = r.create_root()
        el = h(ErrorBoundary, {"children": h(BadHooksComp)})
        r.render(root, el, 80, 24)  # 首帧正常
        # 第二帧 hook 顺序变化 → HookStateError 传播（不被 boundary 吞掉）
        with pytest.raises(RuntimeError):
            r.render(root, el, 80, 24)


class TestMemoShortCircuit:
    """方向B 步骤10 — memo 组件级渲染短路。"""

    def test_memo_skips_render_when_props_unchanged(self):
        """props 相同 → 组件函数不再次调用（短路）。"""
        from src.tui.ink.hooks import memo
        calls = []

        def Plain(props):
            calls.append(props["label"])
            return h(TEXT, {"children": props["label"]})

        Memoized = memo(Plain)
        r = Reconciler()
        root = r.create_root()
        el = h(Memoized, {"label": "A"})
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)  # props 相同 → 短路
        assert calls == ["A"]  # 仅首帧调用

    def test_memo_rerenders_when_props_change(self):
        """props 变化 → 重渲染。"""
        from src.tui.ink.hooks import memo
        calls = []

        def Plain(props):
            calls.append(props["label"])
            return h(TEXT, {"children": props["label"]})

        Memoized = memo(Plain)
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Memoized, {"label": "A"}), 80, 24)
        r.render(root, h(Memoized, {"label": "B"}), 80, 24)
        assert calls == ["A", "B"]

    def test_memo_state_update_rerenders_even_same_props(self):
        """props 未变但 state 更新 → 不能短路（重渲染应用更新）。"""
        from src.tui.ink.hooks import memo, use_state
        holder = []
        seen = []

        def Plain(props):
            n, set_n = use_state(0)
            if not holder:
                holder.append(set_n)
            seen.append(n)
            return h(TEXT, {"children": str(n)})

        Memoized = memo(Plain)
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Memoized, {"label": "A"}), 80, 24)
        holder[0](5)  # state 更新
        r.render(root, h(Memoized, {"label": "A"}), 80, 24)
        assert seen == [0, 5]

    def test_memo_custom_are_equal(self):
        """自定义 are_equal：只看指定字段，其他字段变化仍短路。"""
        from src.tui.ink.hooks import memo
        calls = []

        def Plain(props):
            calls.append(props["v"])
            return h(TEXT, {"children": str(props["v"])})

        Memoized = memo(Plain, are_equal=lambda a, b: a["v"] == b["v"])
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Memoized, {"v": 1, "ignored": "x"}), 80, 24)
        r.render(root, h(Memoized, {"v": 1, "ignored": "y"}), 80, 24)
        assert calls == [1]  # are_equal 只看 v → 短路

    def test_memo_uncomparable_props_fallback(self):
        """props 含不可比较对象 → 默认比较 try/except 兜底为重渲染（不短路）。"""
        from src.tui.ink.hooks import memo
        calls = []

        class Uncomparable:
            def __eq__(self, other):
                raise TypeError("not comparable")

        def Plain(props):
            calls.append(1)
            return h(TEXT, {"children": "x"})

        Memoized = memo(Plain)
        r = Reconciler()
        root = r.create_root()
        # 两次渲染使用不同 Uncomparable 实例（同实例经 dict== 的 is 快捷返回 True）
        r.render(root, h(Memoized, {"obj": Uncomparable()}), 80, 24)
        r.render(root, h(Memoized, {"obj": Uncomparable()}), 80, 24)
        assert calls == [1, 1]  # 比较抛异常 → 视为不等 → 重渲染（两次都调用）

    def test_memo_reuses_child_fiber_across_skipped_frames(self):
        """memo 短路保留 fiber.child（不重建子树，同 key/type fiber 复用）。"""
        from src.tui.ink.hooks import memo

        def Plain(props):
            return h(TEXT, {"children": props["label"]})

        Memoized = memo(Plain)
        r = Reconciler()
        root = r.create_root()
        el = h(Memoized, {"label": "A"})
        r.render(root, el, 80, 24)
        first_child = root.child.child  # host text fiber
        r.render(root, el, 80, 24)  # 短路 → 不重建
        assert root.child.child is first_child


class TestInputRouterFocus:
    """方向B 步骤10 — useFocus 焦点仲裁。"""

    def _capture(self):
        from src.tui.ink.hooks import set_input_router_callback
        captured = {}
        set_input_router_callback(lambda router: captured.update(router=router))
        return captured

    def test_focused_hook_preferred_over_unfocused(self):
        """focused 集合非空 → 仅 focused hook 参与路由（非 focused 不消费）。"""
        from src.tui.ink.hooks import use_input, useFocus
        captured = self._capture()
        focused_handler = MagicMock(return_value=True)
        unfocused_handler = MagicMock(return_value=True)

        def Comp(props):
            use_input(focused_handler, True)
            useFocus(True)
            use_input(unfocused_handler, True)
            useFocus(False)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            assert router is not None
            result = router(MagicMock())
            assert result is True
            focused_handler.assert_called_once()
            unfocused_handler.assert_not_called()  # 非 focused 不消费
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_no_focused_falls_back_to_all_active(self):
        """focused 集合为空 → 回退全部 active hook（行为与现状一致）。"""
        from src.tui.ink.hooks import use_input, useFocus
        captured = self._capture()
        first = MagicMock(return_value=True)
        second = MagicMock(return_value=False)

        def Comp(props):
            use_input(first, True)
            useFocus(False)
            use_input(second, True)
            useFocus(False)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            assert router is not None
            result = router(MagicMock())
            assert result is True
            first.assert_called_once()  # 回退 → first 仍参与
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_default_focus_all_active_when_use_focus_absent(self):
        """未调用 useFocus → 默认全部 focused → 全部参与（零回归）。"""
        from src.tui.ink.hooks import use_input
        captured = self._capture()
        first = MagicMock(return_value=True)
        second = MagicMock(return_value=True)

        def Comp(props):
            use_input(first, True)
            use_input(second, True)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            router = captured["router"]
            result = router(MagicMock())
            assert result is True
            first.assert_called_once()
            second.assert_not_called()  # 首个消费 → 第二个不调用
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)
