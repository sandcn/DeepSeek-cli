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
