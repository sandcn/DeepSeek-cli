"""测试 React Ink v6 完整特性补齐（方向 A~G）。

覆盖：
  - A. Text 样式：strikethrough / inverse（Style ANSI + resolve_text_style）
  - B. 布局：row-reverse / column-reverse / wrap-reverse / baseline /
       alignContent / columnGap / rowGap / position=static / overflow /
       aspectRatio
  - C. 边框：各边颜色 / 自定义对象 / 各边显隐 / dim / 背景色
  - D. Box 级 backgroundColor（填充 + 子 Text 继承）
  - E. Hooks：usePaste / useBoxMetrics / useWindowSize / useFocusManager /
       useCursor / useIsScreenReaderEnabled / useAnimation / useApp 扩展
  - F. render() 生命周期 API
  - G. use_input (input, key) 签名 / Static items 模式 / Transform (line, index)
"""

from __future__ import annotations

from src.tui.ink import (
    h, BOX, TEXT, Static, Transform,
    use_input, useFocus, useFocusManager, usePaste, useCursor,
    useIsScreenReaderEnabled, useAnimation, useBoxMetrics, use_ref, useApp,
)
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.helpers import line_to_ansi
from src.tui.core.style import Style
from src.tui._input_parser import KeyEvent


def _render(el, width=80, height=24):
    """渲染元素树，返回 Frame。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, el, width, height)
    return render_frame(root, width)


def _plain_lines(frame):
    """Frame 行转纯文本列表（去 ANSI）。"""
    return [line.plain for line in frame.lines]


def _ansi_lines(frame):
    """Frame 行转 ANSI 字符串列表。"""
    return [line_to_ansi(line) for line in frame.lines]


# ═══════════════════════════════════════════════════════════
# A. Text 样式：strikethrough / inverse
# ═══════════════════════════════════════════════════════════


class TestTextStyleStrikethroughInverse:
    def test_style_has_strikethrough_inverse_fields(self):
        s = Style(strikethrough=True, inverse=True)
        assert s.strikethrough and s.inverse
        assert "\033[7m" in s.to_ansi()  # inverse
        assert "\033[9m" in s.to_ansi()  # strikethrough

    def test_resolve_text_style_props(self):
        from src.tui.ink.helpers import resolve_text_style
        st = resolve_text_style({"strikethrough": True, "inverse": True})
        assert st is not None and st.strikethrough and st.inverse

    def test_style_fingerprint_covers_new_fields(self):
        from src.tui.ink._style_fp import style_fingerprint
        a = Style(strikethrough=True)
        b = Style()
        c = Style(inverse=True)
        assert style_fingerprint(a) != style_fingerprint(b)
        assert style_fingerprint(b) != style_fingerprint(c)
        # renderer Style（无新字段）兼容（getattr 兜底）
        from src.renderer.ansi.style import Style as RStyle
        assert style_fingerprint(RStyle(bold=True)) is not None

    def test_render_strikethrough_ansi(self):
        frame = _render(h(TEXT, {"children": "X", "strikethrough": True}))
        assert "\033[9m" in _ansi_lines(frame)[0]

    def test_render_inverse_ansi(self):
        frame = _render(h(TEXT, {"children": "X", "inverse": True}))
        assert "\033[7m" in _ansi_lines(frame)[0]

    def test_style_merge_keeps_new_fields(self):
        merged = Style(bold=True).merge(Style(strikethrough=True))
        assert merged.bold and merged.strikethrough and not merged.inverse


# ═══════════════════════════════════════════════════════════
# B. 布局扩展
# ═══════════════════════════════════════════════════════════


class TestLayoutReactInk:
    def test_row_reverse(self):
        """row-reverse：视觉顺序反转（首子最右）。"""
        frame = _render(h(BOX, {"flexDirection": "row-reverse", "width": 10}, [
            h(TEXT, {"children": "A"}), h(TEXT, {"children": "B"}),
        ]), width=10)
        assert _plain_lines(frame)[0] == "BA"

    def test_column_reverse(self):
        """column-reverse：视觉顺序反转（首子最下）。"""
        frame = _render(h(BOX, {"flexDirection": "column-reverse", "height": 3}, [
            h(TEXT, {"children": "A"}), h(TEXT, {"children": "B"}),
        ]), width=10)
        assert _plain_lines(frame)[0] == "B"
        assert _plain_lines(frame)[1] == "A"

    def test_wrap_reverse_line_order(self):
        """wrap-reverse：行序反转（首行在最下）。"""
        frame = _render(h(BOX, {"flexDirection": "row", "flexWrap": "wrap-reverse", "width": 2, "height": 3}, [
            h(TEXT, {"children": "A"}), h(TEXT, {"children": "B"}), h(TEXT, {"children": "C"}),
        ]), width=4)
        lines = [p for p in _plain_lines(frame) if p.strip()]
        assert lines[0] == "C"
        assert lines[1] == "AB"

    def test_column_gap_row_gap(self):
        """columnGap（row 水平间距）/ rowGap（column 垂直间距）。"""
        frame = _render(h(BOX, {"flexDirection": "row", "columnGap": 3}, [
            h(TEXT, {"children": "A"}), h(TEXT, {"children": "B"}),
        ]), width=10)
        assert _plain_lines(frame)[0] == "A   B"
        frame2 = _render(h(BOX, {"flexDirection": "column", "rowGap": 2}, [
            h(TEXT, {"children": "A"}), h(TEXT, {"children": "B"}),
        ]), width=10)
        assert _plain_lines(frame2)[1] == ""

    def test_align_items_baseline(self):
        """alignItems=baseline：近似底部对齐（不崩溃 + 布局正常）。"""
        frame = _render(h(BOX, {"flexDirection": "row", "alignItems": "baseline", "height": 3}, [
            h(TEXT, {"children": "AB"}), h(TEXT, {"children": "X"}),
        ]), width=10)
        assert len(frame.lines) == 3

    def test_align_self_auto_follows_parent(self):
        """alignSelf=auto 跟随父 alignItems。"""
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"flexDirection": "column", "width": 10, "alignItems": "center"}, [
            h(BOX, {"width": 4, "flexDirection": "column"}, h(TEXT, {"children": "X"})),
        ])
        r.render(root, el, 20, 10)
        boxes = []
        def _collect(f):
            while f is not None:
                if f.is_host and f.layout_box is not None:
                    boxes.append((f.type, (f.layout_box.x, f.layout_box.w)))
                if f.child:
                    _collect(f.child)
                f = f.sibling
        _collect(root)
        # alignSelf=auto → 继承 center → X 盒居中（x = (10-4)//2 = 3）
        inner = [b for t, b in boxes if t == "box" and b[1] == 4]
        assert inner and inner[0][0] == 3

    def test_align_content_space_between(self):
        """alignContent=space-between：首行顶、末行底。"""
        frame = _render(h(BOX, {"flexDirection": "row", "flexWrap": "wrap", "width": 3,
                                "height": 7, "alignContent": "space-between"}, [
            h(TEXT, {"children": "A"}), h(TEXT, {"children": "B"}),
            h(TEXT, {"children": "C"}), h(TEXT, {"children": "D"}),
        ]), width=6)
        lines = _plain_lines(frame)
        assert lines[0].strip() == "ABC"
        assert lines[6].strip() == "D"

    def test_position_static_ignores_offsets(self):
        """position=static：不参与绝对定位基准（top/left 被忽略）。"""
        frame = _render(h(BOX, {"flexDirection": "column", "width": 10}, [
            h(BOX, {"position": "static", "left": 5, "width": 3, "flexDirection": "column"},
              h(TEXT, {"children": "X"})),
        ]), width=20)
        # static 元素正常流定位（left 被忽略）→ x=0
        assert _plain_lines(frame)[0].strip() == "X"

    def test_overflow_hidden_clips_vertical(self):
        """overflowY=hidden：超出容器高度的内容被裁剪。"""
        frame = _render(h(BOX, {"flexDirection": "column", "width": 6, "height": 2,
                                "overflowY": "hidden"}, [
            h(TEXT, {"children": "l1"}), h(TEXT, {"children": "l2"}), h(TEXT, {"children": "l3"}),
        ]), width=10)
        lines = _plain_lines(frame)
        assert lines[0].strip() == "l1"
        assert lines[1].strip() == "l2"
        assert len(lines) == 2  # l3 被裁剪

    def test_overflow_x_hidden_clips_horizontal(self):
        """overflowX=hidden：超出容器宽度的内容被裁剪。"""
        frame = _render(h(BOX, {"flexDirection": "column", "width": 3, "overflowX": "hidden"}, [
            h(TEXT, {"children": "abcdef"}),
        ]), width=10)
        # TEXT 已按容器宽 wrap 为多行，但每行 <= 3 列
        for p in _plain_lines(frame):
            assert len(p) <= 3

    def test_aspect_ratio_width_to_height(self):
        """aspectRatio：width 已知推导 height（ratio = w/h）。"""
        frame = _render(h(BOX, {"width": 10, "aspectRatio": 2, "flexDirection": "column"},
                          h(TEXT, {"children": "X"})), width=20)
        assert len(frame.lines) == 5  # h = 10 / 2

    def test_aspect_ratio_height_to_width(self):
        """aspectRatio：height 已知推导 width（ratio = w/h）。"""
        # w = 4 * 2 = 8
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, {"height": 4, "aspectRatio": 2, "flexDirection": "column"},
               h(TEXT, {"children": "X"}))
        r.render(root, el, 20, 10)
        # 检查 layout box 宽度为 8
        boxes = []
        def _collect(f):
            while f is not None:
                if f.is_host and f.layout_box is not None and f.type == "box":
                    boxes.append((f.layout_box.w, f.layout_box.h))
                if f.child:
                    _collect(f.child)
                f = f.sibling
        _collect(root)
        assert (8, 4) in boxes


# ═══════════════════════════════════════════════════════════
# C. 边框增强
# ═══════════════════════════════════════════════════════════


class TestBorderReactInk:
    def test_border_custom_object(self):
        """borderStyle 自定义对象字符。"""
        frame = _render(h(BOX, {"border": 1, "width": 5, "height": 3,
                                "flexDirection": "column",
                                "borderStyle": {"topLeft": "A", "top": "B", "topRight": "C",
                                                "left": "L", "bottomLeft": "D", "bottom": "E",
                                                "bottomRight": "F", "right": "R"}},
                          h(TEXT, {"children": "hi"})), width=10)
        lines = _plain_lines(frame)
        assert lines[0] == "ABBBC"
        assert lines[1] == "Lhi R"
        assert lines[2] == "DBBBF"

    def test_border_edge_colors(self):
        """borderTopColor 等各边独立颜色。"""
        frame = _render(h(BOX, {"border": 1, "width": 5, "height": 3,
                                "flexDirection": "column",
                                "borderTopColor": "green", "borderBottomColor": "blue",
                                "borderLeftColor": "red", "borderRightColor": "yellow"},
                          h(TEXT, {"children": "x"})), width=10)
        ansi = _ansi_lines(frame)
        # 顶边绿色（38;5;2）、底边蓝色（38;5;4）、左边红色（38;5;1）、右边黄色（38;5;3）
        assert "\033[38;5;2m" in ansi[0]
        assert "\033[38;5;4m" in ansi[2]
        assert "\033[38;5;1m" in ansi[1]
        assert "\033[38;5;3m" in ansi[1]

    def test_border_dim(self):
        """borderDimColor：dim 边框。"""
        frame = _render(h(BOX, {"border": 1, "width": 5, "height": 3,
                                "flexDirection": "column", "borderDimColor": True},
                          h(TEXT, {"children": "x"})), width=10)
        assert "\033[2m" in _ansi_lines(frame)[0]

    def test_border_background_color(self):
        """borderBackgroundColor：边框背景色。"""
        frame = _render(h(BOX, {"border": 1, "width": 5, "height": 3,
                                "flexDirection": "column", "borderBackgroundColor": "red"},
                          h(TEXT, {"children": "x"})), width=10)
        assert "\033[48;5;1m" in _ansi_lines(frame)[0]

    def test_border_edge_visibility(self):
        """borderTop/borderLeft=False：隐藏对应边。"""
        frame = _render(h(BOX, {"border": 1, "width": 5, "height": 3,
                                "flexDirection": "column",
                                "borderTop": False, "borderLeft": False},
                          h(TEXT, {"children": "hi"})), width=10)
        lines = _plain_lines(frame)
        assert lines[0] == ""          # 顶边隐藏
        assert lines[1] == " hi │"     # 左边隐藏、右边保留
        assert lines[2] == "└───┘"     # 底边保留


# ═══════════════════════════════════════════════════════════
# D. Box 级 backgroundColor
# ═══════════════════════════════════════════════════════════


class TestBoxBackgroundColor:
    def test_box_background_fills_area(self):
        """Box backgroundColor 填充整个 box 区域。"""
        frame = _render(h(BOX, {"backgroundColor": "red", "width": 6, "height": 2,
                                "flexDirection": "column"}, h(TEXT, {"children": "hi"})), width=10)
        ansi = _ansi_lines(frame)
        assert "\033[48;5;1m" in ansi[0] and "\033[48;5;1m" in ansi[1]

    def test_text_inherits_box_background(self):
        """子 Text 未指定自身背景色时继承 Box 背景。"""
        frame = _render(h(BOX, {"backgroundColor": "red", "width": 6, "height": 1,
                                "flexDirection": "column"}, h(TEXT, {"children": "hi"})), width=10)
        ansi = _ansi_lines(frame)[0]
        assert "hi" in ansi and "\033[48;5;1m" in ansi

    def test_text_own_background_overrides(self):
        """子 Text 自身 backgroundColor 优先（覆盖继承）。"""
        frame = _render(h(BOX, {"backgroundColor": "red", "width": 8, "height": 1,
                                "flexDirection": "column"},
                          h(TEXT, {"children": "hi", "backgroundColor": "blue"})), width=10)
        ansi = _ansi_lines(frame)[0]
        assert "hi" in ansi and "\033[48;5;4m" in ansi


# ═══════════════════════════════════════════════════════════
# E. Hooks 扩展
# ═══════════════════════════════════════════════════════════


class TestHooksReactInk:
    def test_use_is_screen_reader_enabled(self):
        assert useIsScreenReaderEnabled() is False

    def test_use_animation(self):
        anim = useAnimation({"fps": 10, "duration": 1})
        assert "frame" in anim and "timestamp" in anim
        assert isinstance(anim["frame"], int)
        assert isinstance(anim["timestamp"], float)
        anim2 = useAnimation()
        assert "frame" in anim2

    def test_use_cursor(self):
        from src.tui.ink.hooks import set_cursor_position_fn
        calls = []
        set_cursor_position_fn(lambda pos: calls.append(pos))
        try:
            cur = useCursor()
            assert callable(cur["setCursorPosition"])
            cur["setCursorPosition"]({"x": 2, "y": 1})
            assert calls == [{"x": 2, "y": 1}]
        finally:
            set_cursor_position_fn(None)

    def test_use_app_extension(self):
        app = useApp()
        for k in ("waitUntilRenderFlush", "suspendTerminal"):
            assert callable(app[k])

    def test_use_paste_handler(self):
        seen = []
        def Comp(props):
            usePaste(lambda text: (seen.append(text) or True))
            return h(TEXT, {"children": "x"})
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        router = r._build_input_router(root)
        router(KeyEvent(kind="char", char="pasted text"))
        assert seen == ["pasted text"]

    def test_use_paste_blocks_use_input(self):
        """usePaste 消费粘贴后 use_input 不收到（独立通道）。"""
        events = []
        def Comp(props):
            usePaste(lambda text: True)
            use_input(lambda ev: (events.append("input") or False), True)
            return h(TEXT, {"children": "x"})
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        router = r._build_input_router(root)
        router(KeyEvent(kind="char", char="abc"))
        assert events == []

    def test_use_focus_manager_cycle(self):
        """useFocusManager：focusNext/focusPrevious/focus(id)/activeId。"""
        import src.tui.ink.hooks as H
        results = {}
        def CompA(props):
            use_input(lambda ev: False, True)
            results["A"] = useFocus({"id": "A", "autoFocus": True})
            return h(TEXT, {"children": "A"})
        def CompB(props):
            use_input(lambda ev: False, True)
            results["B"] = useFocus({"id": "B"})
            return h(TEXT, {"children": "B"})
        r = Reconciler()
        root = r.create_root()
        el = h("box", {"flexDirection": "column"}, [h(CompA), h(CompB)])
        r.render(root, el, 80, 24)
        assert results["A"]["isFocused"] is True
        assert results["B"]["isFocused"] is False
        H._focus_next()
        r.render(root, el, 80, 24)
        assert results["A"]["isFocused"] is False
        assert results["B"]["isFocused"] is True
        H._focus_to("A")
        r.render(root, el, 80, 24)
        assert results["A"]["isFocused"] is True
        mgr = useFocusManager()
        assert mgr["activeId"] in ("A", "B")

    def test_tab_key_focus_switch(self):
        """router 中 Tab 触发焦点切换并消费事件。"""
        results = {}
        events = []
        def CompA(props):
            use_input(lambda ev: (events.append("A") or False), True)
            results["A"] = useFocus({"id": "A", "autoFocus": True})
            return h(TEXT, {"children": "A"})
        def CompB(props):
            use_input(lambda ev: (events.append("B") or False), True)
            results["B"] = useFocus({"id": "B"})
            return h(TEXT, {"children": "B"})
        r = Reconciler()
        root = r.create_root()
        el = h("box", {"flexDirection": "column"}, [h(CompA), h(CompB)])
        r.render(root, el, 80, 24)
        router = r._build_input_router(root)
        router(KeyEvent(kind="tab"))
        # Tab 触发焦点切换 + 消费事件；isFocused 在重新渲染后更新
        r.render(root, el, 80, 24)
        assert results["A"]["isFocused"] is False
        assert results["B"]["isFocused"] is True
        assert events == []  # Tab 被焦点切换消费

    def test_use_box_metrics_returns_dict(self):
        """useBoxMetrics 返回 width/height/left/top/hasMeasured。"""
        out = {}
        def Comp(props):
            m = useBoxMetrics(use_ref(None))
            out.update(m)
            return h(TEXT, {"children": "x"})
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        for k in ("width", "height", "left", "top", "hasMeasured"):
            assert k in out

    def test_use_window_size_store(self):
        """useWindowSize store：accessor 注入后刷新。"""
        import src.tui.ink.hooks as H
        H.set_window_size_accessor(lambda: (120, 50))
        H._refresh_window_size()
        assert H._window_size == (120, 50)
        H.set_window_size_accessor(None)


# ═══════════════════════════════════════════════════════════
# F. render() 生命周期 API
# ═══════════════════════════════════════════════════════════


class TestRenderApi:
    def test_render_returns_control_object(self):
        """render() 返回 waitUntilExit/unmount/cleanup/rerender/clear。"""
        import io
        from src.tui.ink.session import render
        s = io.StringIO()
        s.isatty = lambda: False  # type: ignore[attr-defined]
        ctrl = render(h(TEXT, {"children": "hi"}), stream=s, width=20, height=10)
        try:
            for k in ("waitUntilExit", "unmount", "cleanup", "rerender", "clear"):
                assert callable(ctrl[k])
        finally:
            ctrl["unmount"]()


# ═══════════════════════════════════════════════════════════
# G. 输入与组件完善
# ═══════════════════════════════════════════════════════════


class TestInputAndComponentsReactInk:
    def test_use_input_input_key_signature(self):
        """use_input 兼容 React Ink (input, key) 双参签名。"""
        seen = []
        def Comp(props):
            use_input(lambda inp, key: (seen.append((inp, key)) or True), True)
            return h(TEXT, {"children": "x"})
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        router = r._build_input_router(root)
        router(KeyEvent(kind="char", char="a"))
        assert seen[-1][0] == "a"
        assert seen[-1][1]["return"] is False
        router(KeyEvent(kind="enter"))
        assert seen[-1][1]["return"] is True
        router(KeyEvent(kind="arrow_up"))
        assert seen[-1][1]["upArrow"] is True
        router(KeyEvent(kind="backspace"))
        assert seen[-1][1]["backspace"] is True
        router(KeyEvent(kind="delete"))
        assert seen[-1][1]["delete"] is True
        router(KeyEvent(kind="home"))
        assert seen[-1][1]["home"] is True
        router(KeyEvent(kind="end"))
        assert seen[-1][1]["end"] is True

    def test_text_wrap_hard(self):
        """wrap="hard"：字符级硬拆填满行宽（不保留词完整性）。"""
        frame = _render(h(BOX, {"width": 7, "flexDirection": "column"},
                          h(TEXT, {"children": "Hello World", "wrap": "hard"})), width=20)
        # hard 模式字符级硬拆：每行填满 7 列（不含空格断点）
        lines = _plain_lines(frame)
        assert lines[0] == "Hello W"
        assert lines[1] == "orld"

    def test_text_wrap_default_keeps_words(self):
        """wrap 默认（wrap）：空格处断行保留词完整性。"""
        frame = _render(h(BOX, {"width": 7, "flexDirection": "column"},
                          h(TEXT, {"children": "Hello World"})), width=20)
        lines = _plain_lines(frame)
        assert lines[0] == "Hello"
        assert lines[1] == "World"

    def test_use_input_single_arg_unchanged(self):
        """单参数 handler 零回归（收 KeyEvent）。"""
        seen = []
        def Comp(props):
            use_input(lambda ev: (seen.append(ev) or False), True)
            return h(TEXT, {"children": "x"})
        r = Reconciler()
        root = r.create_root()
        r.render(root, h(Comp), 80, 24)
        router = r._build_input_router(root)
        router(KeyEvent(kind="char", char="z"))
        assert seen[-1].kind == "char" and seen[-1].char == "z"

    def test_static_items_mode(self):
        """Static items 数组模式。"""
        frame = _render(h("box", {"flexDirection": "column"}, [
            h(Static, {"items": ["one", "two"],
                       "children": lambda item, index: h(BOX, {"key": index}, h(TEXT, {"children": item}))}),
        ]), width=40)
        lines = [p for p in _plain_lines(frame) if p.strip()]
        assert lines[0] == "one"
        assert lines[1] == "two"

    def test_transform_line_index_signature(self):
        """Transform 支持 (line, index) 签名。"""
        frame = _render(h(Transform, {"transform": lambda line, index: ("  " * index + line) if index else line},
                          "first\nsecond\nthird"), width=40)
        lines = _plain_lines(frame)
        assert lines[0] == "first"
        assert lines[1] == "  second"
        assert lines[2] == "    third"

    def test_transform_single_arg_backward_compat(self):
        """Transform 单参签名零回归。"""
        frame = _render(h(Transform, {"transform": lambda s: s.upper()}, "hello"), width=40)
        assert _plain_lines(frame)[0] == "HELLO"
