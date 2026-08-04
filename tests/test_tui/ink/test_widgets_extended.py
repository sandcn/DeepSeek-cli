"""测试 ink/widgets 新增标准控件 — Toggle / Panel / Tree。

覆盖：
  - Toggle：开关指示符 / 键盘切换 / ref 镜像 / 样式 / 标签；
  - Panel：四边框完整性 / 标题状态 / 边框变体 / 窄屏安全 / 空内容；
  - Tree：可见节点收集 / 展开折叠指示符 / 键盘导航 / 叶子选择 / 越界钳制。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets import (
    Toggle, Panel, Tree, Flex, Spacer, ListView, FocusGroup, Key,
)


def _render(element, width=80, height=24):
    """渲染元素树，返回 Frame。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, element, width, height)
    return render_frame(root, width)


# ═══════════════════════════════════════════════════════════
# Toggle
# ═══════════════════════════════════════════════════════════


def _key(kind: str, char: str | None = None):
    """构造 KeyEvent 结构（控件 handler 消费）。"""
    ev = type("KeyEvent", (), {"kind": kind, "char": char})()
    return ev


def _render_with_router(element, event, rerender=False) -> tuple[list[str], list]:
    """渲染元素树 + 注入 input router + 分发单个按键，返回 (行文本, 回调记录)。

    rerender=True 时按键后重新调和（应用 state queue——测试状态更新后的渲染）。
    """
    captured = []
    from src.tui.ink.hooks import set_input_router_callback

    def _capture(router):
        captured.append(router)

    set_input_router_callback(_capture)
    try:
        r = Reconciler()
        root = r.create_root()
        r.render(root, element, 80, 24)
        router = captured[-1] if captured else None
        if router is not None:
            router(event)
        if rerender and router is not None:
            r.render(root, element, 80, 24)
        frame = render_frame(root, 80)
        return [ln.plain for ln in frame.lines], captured
    finally:
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(None)


class TestToggle:
    def test_checked_render(self):
        frame = _render(h(Toggle, {"value": True, "label": "启用"}))
        assert frame.lines[0].plain == "● 启用"

    def test_unchecked_render(self):
        frame = _render(h(Toggle, {"value": False, "label": "启用"}))
        assert frame.lines[0].plain == "○ 启用"

    def test_no_label(self):
        frame = _render(h(Toggle, {"value": True}))
        assert frame.lines[0].plain == "● "

    def test_checked_style(self):
        frame = _render(h(Toggle, {"value": True, "checkedStyle": Style(fg=2)}))
        assert frame.lines[0].runs[0].style.fg == 2

    def test_space_toggles_on_change(self):
        calls = []
        el = h(Toggle, {"value": False, "onChange": lambda v: calls.append(v)})
        _, captured = _render_with_router(el, _key("space"))
        assert calls == [True]

    def test_enter_toggles(self):
        calls = []
        el = h(Toggle, {"value": True, "onChange": lambda v: calls.append(v)})
        _render_with_router(el, _key("enter"))
        assert calls == [False]

    def test_char_space_toggles(self):
        calls = []
        el = h(Toggle, {"value": False, "onChange": lambda v: calls.append(v)})
        _render_with_router(el, _key("char", " "))
        assert calls == [True]

    def test_focus_false_no_consume(self):
        """focus=False 不参与输入路由——onChange 不触发。"""
        calls = []
        el = h(Toggle, {"value": False, "focus": False, "onChange": lambda v: calls.append(v)})
        _render_with_router(el, _key("space"))
        assert calls == []

    def test_other_key_passthrough(self):
        """非切换键不消费（回调不触发）。"""
        calls = []
        el = h(Toggle, {"value": False, "onChange": lambda v: calls.append(v)})
        _render_with_router(el, _key("arrow_up"))
        assert calls == []

    def test_custom_prefix(self):
        frame = _render(h(Toggle, {
            "value": True, "checkedPrefix": "[x] ", "uncheckedPrefix": "[ ] ",
        }))
        assert frame.lines[0].plain == "[x] "

    def test_callback_exception_isolated(self):
        """onChange 回调异常不阻断输入分发。"""
        def boom(v):
            raise RuntimeError("boom")

        el = h(Toggle, {"value": False, "onChange": boom})
        _render_with_router(el, _key("space"))  # 不应抛异常


# ═══════════════════════════════════════════════════════════
# Panel
# ═══════════════════════════════════════════════════════════


class TestPanel:
    def test_full_border(self):
        el = h(Panel, {"title": "状态", "width": 20}, h("text", {"children": "内容"}))
        frame = _render(el, width=20)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0] == "┌──────────────────┐"
        assert lines[-1] == "└──────────────────┘"
        # 中间行全部带左右竖线
        for line in lines[1:-1]:
            assert line.startswith("│"), line
            assert line.endswith("│"), line

    def test_title_and_status(self):
        el = h(Panel, {"title": "状态", "status": "完成", "width": 20},
               h("text", {"children": "内容"}))
        frame = _render(el, width=20)
        plains = [ln.plain for ln in frame.lines]
        assert any("状态" in p for p in plains)
        assert any("完成" in p for p in plains)

    def test_border_double(self):
        el = h(Panel, {"title": "面板", "borderStyle": "double", "width": 16},
               h("text", {"children": "内容"}))
        frame = _render(el, width=16)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0].startswith("╔")
        assert lines[-1].startswith("╚")
        assert lines[1].startswith("║")

    def test_narrow_no_overflow(self):
        """窄屏（width=6）：所有行宽 <= 6。"""
        el = h(Panel, {"title": "状态", "width": 6},
               h("text", {"children": "很长很长很长很长很长"}))
        frame = _render(el, width=6)
        for line in frame.lines:
            assert line.width <= 6, f"行超宽: {line.plain!r} w={line.width}"

    def test_empty_content(self):
        frame = _render(h(Panel, {"width": 10}), width=10)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0] == "┌────────┐"
        assert lines[-1] == "└────────┘"

    def test_border_color_style(self):
        el = h(Panel, {"borderColor": "red", "width": 8})
        frame = _render(el, width=8)
        assert frame.lines[0].runs[0].style.fg == 1  # red → 1

    def test_children_wrap_inside(self):
        """主体内容按内宽换行（flexGrow 语义）。"""
        el = h(Panel, {"width": 10}, h("text", {"children": "a" * 30}))
        frame = _render(el, width=10)
        # 顶/底边框 + 内容行（每行 ≤ 内宽 8）
        for line in frame.lines:
            assert line.width <= 10
        # 内容换行后仍有竖线
        assert any("a" in ln.plain for ln in frame.lines)


# ═══════════════════════════════════════════════════════════
# Tree
# ═══════════════════════════════════════════════════════════


_TREE_DATA = [
    {"label": "root", "children": [
        {"label": "leaf1"},
        {"label": "dir", "children": [
            {"label": "nested"},
        ]},
    ]},
    "plain",
]


class TestTree:
    def test_render_visible_nodes(self):
        frame = _render(h(Tree, {"data": _TREE_DATA, "width": 30}), width=30)
        plains = [ln.plain for ln in frame.lines]
        # root（展开）→ leaf1 → dir（展开）→ nested → plain
        assert plains[0].startswith("▾ root")
        assert plains[1].startswith("    leaf1")
        assert plains[2].startswith("  ▾ dir")
        assert plains[3].startswith("      nested")
        assert plains[4].startswith("  plain")

    def test_closed_node_hides_children(self):
        data = [{"label": "dir", "open": False, "children": [{"label": "hidden"}]}]
        frame = _render(h(Tree, {"data": data, "width": 20}), width=20)
        plains = [ln.plain for ln in frame.lines]
        assert any("dir" in p for p in plains)
        assert not any("hidden" in p for p in plains)

    def test_string_leaf_shortcut(self):
        frame = _render(h(Tree, {"data": ["a", "b"], "width": 20}), width=20)
        plains = [ln.plain for ln in frame.lines]
        assert plains[0].endswith("a")
        assert plains[1].endswith("b")

    def test_indent_custom(self):
        data = [{"label": "r", "children": [{"label": "c"}]}]
        frame = _render(h(Tree, {"data": data, "indent": 4, "width": 20}), width=20)
        plains = [ln.plain for ln in frame.lines]
        assert plains[1].startswith(" " * 4 + "  c")

    def test_enter_selects_leaf(self):
        calls = []
        el = h(Tree, {"data": [{"label": "a"}, "b"], "onSelect": lambda n: calls.append(n["label"])})
        _render_with_router(el, _key("enter"))
        assert calls == ["a"]  # 初始光标在第一个节点（叶子 → 选择）

    def test_arrow_navigation(self):
        """arrow_down 移动光标到下一可见节点；enter 选择该节点。"""
        calls = []
        el = h(Tree, {"data": [{"label": "a"}, {"label": "b"}], "onSelect": lambda n: calls.append(n["label"])})
        r = Reconciler()
        root = r.create_root()
        captured = []
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: captured.append(router))
        try:
            r.render(root, el, 80, 24)
            router = captured[-1]
            router(_key("arrow_down"))  # 光标 → b
            router(_key("enter"))       # 选择 b
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)
        assert calls == ["b"]

    def test_space_toggles_expand(self):
        """space 在含子级节点上切换展开/折叠。"""
        data = [{"label": "dir", "open": False, "children": [{"label": "hidden"}]}]
        r = Reconciler()
        root = r.create_root()
        captured = []
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: captured.append(router))
        try:
            r.render(root, h(Tree, {"data": data, "width": 20}), 80, 24)
            router = captured[-1]
            # 初始折叠：hidden 不可见
            frame = render_frame(root, 80)
            assert not any("hidden" in ln.plain for ln in frame.lines)
            # space → 展开（重新调和应用 state）
            router(_key("space"))
            r.render(root, h(Tree, {"data": data, "width": 20}), 80, 24)
            frame = render_frame(root, 80)
            assert any("hidden" in ln.plain for ln in frame.lines)
            # space → 折叠
            router(_key("space"))
            r.render(root, h(Tree, {"data": data, "width": 20}), 80, 24)
            frame = render_frame(root, 80)
            assert not any("hidden" in ln.plain for ln in frame.lines)
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_empty_data(self):
        frame = _render(h(Tree, {"data": [], "width": 20}), width=20)
        assert all(not ln.plain for ln in frame.lines)

    def test_line_width_invariant(self):
        """树渲染行宽不超容器宽（长 label 截断/换行）。"""
        data = [{"label": "x" * 100, "children": [{"label": "y" * 100}]}]
        frame = _render(h(Tree, {"data": data, "width": 20}), width=20)
        for line in frame.lines:
            assert line.width <= 20, f"行超宽: {line.plain!r} w={line.width}"

    def test_newline_label_sanitized(self):
        """label 含 \n 归一化为空格（防行级 diff 破坏）。"""
        data = [{"label": "a\nb"}]
        frame = _render(h(Tree, {"data": data, "width": 20}), width=20)
        for line in frame.lines:
            assert "\n" not in line.plain


# ═══════════════════════════════════════════════════════════
# Flex / Spacer（布局门面）
# ═══════════════════════════════════════════════════════════


class TestFlex:
    def test_flex_column_default(self):
        el = h(Flex, {"width": 10}, h("text", {"children": "a"}), h("text", {"children": "b"}))
        frame = _render(el, width=10)
        assert [ln.plain for ln in frame.lines] == ["a", "b"]

    def test_flex_row(self):
        el = h(Flex, {"flexDirection": "row", "width": 10},
               h("text", {"children": "a"}), h("text", {"children": "b"}))
        frame = _render(el, width=10)
        assert "".join(ln.plain for ln in frame.lines) == "ab"

    def test_flex_explicit_props(self):
        el = h(Flex, {"flexDirection": "row", "justifyContent": "center", "width": 10},
               h("text", {"children": "ab"}))
        frame = _render(el, width=10)
        assert "".join(ln.plain for ln in frame.lines) == "    ab"


class TestSpacer:
    def test_spacer_flex_grow(self):
        """Spacer 在 row 中撑开剩余空间（前后内容推到两端）。"""
        el = h(Flex, {"flexDirection": "row", "width": 20},
               h("text", {"children": "L"}), h(Spacer, {}), h("text", {"children": "R"}))
        frame = _render(el, width=20)
        assert frame.lines[0].plain == "L                  R"

    def test_spacer_flex_grow_zero(self):
        """flexGrow=0 不拉伸（固定占位）。"""
        el = h(Flex, {"flexDirection": "row", "width": 20},
               h("text", {"children": "L"}), h(Spacer, {"flexGrow": 0, "width": 3}),
               h("text", {"children": "R"}))
        frame = _render(el, width=20)
        assert frame.lines[0].plain == "L   R"

    def test_spacer_height_in_column(self):
        """Column 中 Spacer 撑开剩余纵向空间（容器有显式高度时）。"""
        el = h("box", {"width": 5, "height": 5},
               h("text", {"children": "a"}), h(Spacer, {}), h("text", {"children": "b"}))
        frame = _render(el, width=5)
        plains = [ln.plain for ln in frame.lines]
        # a 在顶部、b 在底部（Spacer 撑开中间）
        assert plains[0] == "a"
        assert plains[-1] == "b"


# ═══════════════════════════════════════════════════════════
# ListView（虚拟滚动列表）
# ═══════════════════════════════════════════════════════════


def _key_event(kind: str, char: str = ""):
    """构造 KeyEvent（ListView/Toggle/Tree 控件 handler 消费）。"""
    from src.tui._input_parser import KeyEvent
    return KeyEvent(kind=kind, char=char)


class TestListView:
    def test_virtualized_render(self):
        """大列表只渲染视口内行数（100 项 / 视口 5 → 5 行）。"""
        el = h(ListView, {"items": [f"item-{i}" for i in range(100)], "height": 5})
        frame = _render(el, width=20)
        plains = [ln.plain for ln in frame.lines]
        assert len(plains) == 5
        assert plains == ["item-0", "item-1", "item-2", "item-3", "item-4"]

    def test_render_item_custom(self):
        el = h(ListView, {
            "items": ["a", "b"], "height": 2,
            "renderItem": lambda item, i: h("text", {"children": f"[{i}]{item}"}),
        })
        frame = _render(el, width=20)
        assert [ln.plain for ln in frame.lines] == ["[0]a", "[1]b"]

    def test_default_render_str(self):
        el = h(ListView, {"items": [1, 2, 3], "height": 2})
        frame = _render(el, width=20)
        assert [ln.plain for ln in frame.lines] == ["1", "2"]

    def test_arrow_scrolls_window(self):
        """光标越过视口边界时自动滚动 offset。"""
        el = h(ListView, {"items": [f"item-{i}" for i in range(20)], "height": 5})
        r = Reconciler()
        root = r.create_root()
        captured = []
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: captured.append(router))
        try:
            r.render(root, el, 80, 24)
            router = captured[-1]
            for _ in range(6):
                router(_key_event("arrow_down"))
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
            plains = [ln.plain for ln in frame.lines]
            assert plains[0] == "item-2"
            assert plains[-1] == "item-6"
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_home_end(self):
        el = h(ListView, {"items": [f"item-{i}" for i in range(20)], "height": 5})
        r = Reconciler()
        root = r.create_root()
        captured = []
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: captured.append(router))
        try:
            r.render(root, el, 80, 24)
            router = captured[-1]
            router(_key_event("end"))
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
            assert [ln.plain for ln in frame.lines] == [
                "item-15", "item-16", "item-17", "item-18", "item-19",
            ]
            router(_key_event("home"))
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
            assert [ln.plain for ln in frame.lines] == [
                "item-0", "item-1", "item-2", "item-3", "item-4",
            ]
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_enter_select(self):
        calls = []
        el = h(ListView, {
            "items": ["a", "b"], "height": 2,
            "onSelect": lambda item, i: calls.append((item, i)),
        })
        _render_with_router(el, _key_event("enter"))
        assert calls == [("a", 0)]

    def test_focus_false_no_consume(self):
        calls = []
        el = h(ListView, {
            "items": ["a", "b"], "height": 2, "focus": False,
            "onSelect": lambda item, i: calls.append((item, i)),
        })
        _render_with_router(el, _key_event("enter"))
        assert calls == []

    def test_empty_items(self):
        frame = _render(h(ListView, {"items": [], "height": 3}), width=20)
        assert len(frame.lines) == 3  # 视口高度占位

    def test_line_width_invariant(self):
        el = h(ListView, {"items": ["x" * 100], "height": 1})
        frame = _render(el, width=20)
        for line in frame.lines:
            assert line.width <= 20


# ═══════════════════════════════════════════════════════════
# FocusGroup / Key（焦点管理）
# ═══════════════════════════════════════════════════════════


class TestFocusGroup:
    def _setup(self):
        """渲染 FocusGroup + 两个 Key（FakeInput 记录 focus prop）。"""
        captured = []
        routers = []

        def FakeInput(props):
            captured.append(props.get("focus", None))
            return h("text", {"children": "F", "height": 1})

        el = h(FocusGroup, None, [
            h(Key, None, h(FakeInput, {})),
            h(Key, None, h(FakeInput, {})),
        ])
        r = Reconciler()
        root = r.create_root()
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: routers.append(router))
        return captured, routers, el, r, root

    def test_initial_focus_first(self):
        captured, routers, el, r, root = self._setup()
        try:
            r.render(root, el, 80, 24)
            assert captured[-2:] == [True, False]
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_tab_cycles_focus(self):
        captured, routers, el, r, root = self._setup()
        try:
            r.render(root, el, 80, 24)
            routers[-1](_key_event("tab"))
            r.render(root, el, 80, 24)
            assert captured[-2:] == [False, True]
            routers[-1](_key_event("tab"))
            r.render(root, el, 80, 24)
            assert captured[-2:] == [True, False]
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_arrow_left_cycles_back(self):
        captured, routers, el, r, root = self._setup()
        try:
            r.render(root, el, 80, 24)
            routers[-1](_key_event("arrow_left"))
            r.render(root, el, 80, 24)
            assert captured[-2:] == [False, True]  # 回到最后一个
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_unmanaged_key_no_crash(self):
        """不受 FocusGroup 管理的独立 Key：不参与切换，正常渲染。"""
        frame = _render(h(Key, None, h("text", {"children": "x"})), width=10)
        assert "".join(ln.plain for ln in frame.lines) == "x"

    def test_non_key_children_passthrough(self):
        """FocusGroup 中非 Key 子节点原样透传（不注入）。"""
        el = h(FocusGroup, None, [
            h("text", {"children": "plain"}),
            h(Key, None, h("text", {"children": "keyed"})),
        ])
        frame = _render(el, width=10)
        plains = [ln.plain for ln in frame.lines]
        assert "plain" in plains
        assert "keyed" in plains

    def test_text_input_focus_injected(self):
        """真实 TextInput 在 Key 内：激活时 focus=True，未激活 False。"""
        from src.tui.ink.widgets import TextInput
        captured = []
        routers = []

        def TrackInput(props):
            captured.append(props.get("focus", None))
            return h(TextInput, props)

        el = h(FocusGroup, None, [
            h(Key, None, h(TrackInput, {"placeholder": "A"})),
            h(Key, None, h(TrackInput, {"placeholder": "B"})),
        ])
        r = Reconciler()
        root = r.create_root()
        from src.tui.ink.hooks import set_input_router_callback
        set_input_router_callback(lambda router: routers.append(router))
        try:
            r.render(root, el, 80, 24)
            assert captured[-2:] == [True, False]
            routers[-1](_key_event("tab"))
            r.render(root, el, 80, 24)
            assert captured[-2:] == [False, True]
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)


# ═══════════════════════════════════════════════════════════
# 导出完整性
# ═══════════════════════════════════════════════════════════


class TestExports:
    def test_widgets_all(self):
        from src.tui.ink.widgets import (
            Toggle, Panel, Tree, Flex, Spacer, ListView, FocusGroup, Key,
            Box, Text,
        )
        assert callable(Toggle)
        assert callable(Panel)
        assert callable(Tree)
        assert callable(Flex)
        assert callable(Spacer)
        assert callable(ListView)
        assert callable(FocusGroup)
        assert callable(Key)
        assert callable(Box)
        assert callable(Text)

    def test_ink_all(self):
        import src.tui.ink as ink
        assert callable(ink.Toggle)
        assert callable(ink.Panel)
        assert callable(ink.Tree)
        assert callable(ink.Flex)
        assert callable(ink.Spacer)
        assert callable(ink.ListView)
        assert callable(ink.FocusGroup)
        assert callable(ink.Key)
        assert callable(ink.Box)
        assert callable(ink.Text)
