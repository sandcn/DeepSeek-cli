"""测试 ink 功能完善（gap / borderStyle / lazy useState / FrameBuilder）。

覆盖「完善 react ink」新增能力：
  - gap：子节点间距（row/column，gap 优先于 margin）
  - borderStyle：single/double/round/bold 边框变体
  - use_state 惰性初始化（callable initial 仅首渲染求值一次）
  - FrameBuilder.append_line 完整行语义（不额外插入空行）
"""

from __future__ import annotations

from src.tui.ink import BOX, TEXT, h, use_state
from src.tui.ink.element import h as mk_h
from src.tui.ink.output import FrameBuilder, Line, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components


def _render(el, width: int = 30):
    root = Reconciler.create_root()
    recon = Reconciler()
    recon.render(root, el, width, 24)
    return _components.render_frame(root, width)


def _plains(frame):
    return [line.plain for line in frame.lines]


class TestGap:
    """gap 子节点间距（优先于 margin）。"""

    def test_gap_column(self):
        frame = _render(h(BOX, {"gap": 2}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
        ]))
        assert _plains(frame) == ["AAA", "", "", "BBB"]

    def test_gap_row(self):
        frame = _render(h(BOX, {"flexDirection": "row", "gap": 2}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
        ]))
        assert _plains(frame) == ["AAA  BBB"]

    def test_gap_overrides_margin(self):
        frame = _render(h(BOX, {"flexDirection": "row", "gap": 1, "margin": 4}, [
            h(TEXT, {"children": "AAA"}),
            h(TEXT, {"children": "BBB"}),
        ]))
        # gap=1 优先于 margin=4
        assert _plains(frame) == ["AAA BBB"]

    def test_gap_column_total_height(self):
        frame = _render(h(BOX, {"gap": 1}, [
            h(TEXT, {"children": "A"}),
            h(TEXT, {"children": "B"}),
            h(TEXT, {"children": "C"}),
        ]))
        assert frame.height == 5  # 3 行 + 2 间隙


class TestBorderStyle:
    """borderStyle 边框变体。"""

    def test_double(self):
        frame = _render(h(BOX, {"border": 1, "borderStyle": "double", "width": 10}, [
            h(TEXT, {"children": "hi"}),
        ]))
        assert _plains(frame)[0] == "╔════════╗"
        assert _plains(frame)[2] == "╚════════╝"

    def test_round(self):
        frame = _render(h(BOX, {"border": 1, "borderStyle": "round", "width": 10}, [
            h(TEXT, {"children": "hi"}),
        ]))
        assert _plains(frame)[0] == "╭────────╮"
        assert _plains(frame)[2] == "╰────────╯"

    def test_bold(self):
        frame = _render(h(BOX, {"border": 1, "borderStyle": "bold", "width": 10}, [
            h(TEXT, {"children": "hi"}),
        ]))
        assert _plains(frame)[0] == "┏━━━━━━━━┓"
        assert _plains(frame)[2] == "┗━━━━━━━━┛"

    def test_unknown_falls_back_to_single(self):
        frame = _render(h(BOX, {"border": 1, "borderStyle": "fancy", "width": 10}, [
            h(TEXT, {"children": "hi"}),
        ]))
        assert _plains(frame)[0] == "┌────────┐"


class TestLazyState:
    """use_state 惰性初始化（callable initial 仅首渲染求值）。"""

    def test_lazy_initializer_called_once(self):
        calls = {"n": 0}

        def lazy_init():
            calls["n"] += 1
            return 42

        def Comp(_props):
            count, _ = use_state(lazy_init)
            return h(TEXT, {"children": f"count={count}"})

        el = mk_h(Comp, {})
        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, el, 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["count=42"]
        # 再次渲染：惰性初始化不应重复执行
        recon.render(root, el, 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["count=42"]
        assert calls["n"] == 1

    def test_non_callable_initial_kept(self):
        def Comp(_props):
            count, _ = use_state(7)
            return h(TEXT, {"children": f"count={count}"})

        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, mk_h(Comp, {}), 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["count=7"]


class TestFrameBuilderAppendLineSemantics:
    """FrameBuilder.append_line 完整行语义。"""

    def test_append_line_no_extra_blank(self):
        fb = FrameBuilder(width=20)
        fb.append("aaa")
        fb.newline()
        fb.append_line(Line.of("BBB"))
        fb.append("ccc")
        assert [l.plain for l in fb.build().lines] == ["aaa", "BBB", "ccc"]

    def test_append_line_from_fresh(self):
        fb = FrameBuilder(width=20)
        fb.append_line(Line.of("BBB"))
        fb.append("ccc")
        assert [l.plain for l in fb.build().lines] == ["BBB", "ccc"]

    def test_append_line_after_content_continues(self):
        fb = FrameBuilder(width=20)
        fb.append("aaa")
        fb.append_line(Line.of("BBB"))
        assert [l.plain for l in fb.build().lines] == ["aaa", "BBB"]


class TestUseReducerInit:
    """use_reducer 惰性 init 函数（React useReducer 第三参）。"""

    def test_reducer_init_called_once(self):
        from src.tui.ink import use_reducer
        init_calls = {"n": 0}

        def reducer(state, action):
            return state + action

        def init(arg):
            init_calls["n"] += 1
            return arg * 10

        def Comp(_props):
            state, dispatch = use_reducer(reducer, 5, init)
            return h(TEXT, {"children": f"state={state} init={init_calls['n']}"})

        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, mk_h(Comp, {}), 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["state=50 init=1"]
        # 再次渲染：init 不应重复调用
        recon.render(root, mk_h(Comp, {}), 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["state=50 init=1"]
        assert init_calls["n"] == 1

    def test_reducer_without_init(self):
        from src.tui.ink import use_reducer

        def reducer(state, action):
            return state + action

        def Comp(_props):
            state, _ = use_reducer(reducer, 3)
            return h(TEXT, {"children": f"state={state}"})

        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, mk_h(Comp, {}), 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["state=3"]


class TestMinMaxWidth:
    """minWidth / maxWidth 布局钳制。"""

    def test_max_width_clamps(self):
        frame = _render(h(BOX, {"width": 30, "maxWidth": 10, "border": 1}, [
            h(TEXT, {"children": "hello"}),
        ]))
        assert frame.lines[0].plain == "┌────────┐"

    def test_min_width_raises(self):
        frame = _render(h(BOX, {"width": 3, "minWidth": 10, "border": 1}, [
            h(TEXT, {"children": "hi"}),
        ]))
        assert frame.lines[0].plain == "┌────────┐"

    def test_min_max_width_no_explicit(self):
        frame = _render(h(BOX, {"minWidth": 8, "maxWidth": 12, "border": 1}, [
            h(TEXT, {"children": "hello"}),
        ]))
        # 填充可用宽度(30) → maxWidth 钳制 12（含边框）
        assert len(frame.lines[0].plain) == 12


class TestStableListFastPath:
    """_try_reuse_stable 快路径（append-only 稳定列表复用）。"""

    def test_append_only_keyed_reuse(self):
        from src.tui.ink.reconciler import Reconciler
        root = Reconciler.create_root()
        recon = Reconciler()
        el1 = h(BOX, None, [
            h(TEXT, {"key": "0", "children": "a"}),
            h(TEXT, {"key": "1", "children": "b"}),
        ])
        recon.render(root, el1, 80, 24)
        f1 = _components.render_frame(root, 80)
        assert _plains(f1) == ["a", "b"]
        # 追加一行（append-only）→ 快路径复用旧 fiber
        el2 = h(BOX, None, [
            h(TEXT, {"key": "0", "children": "a"}),
            h(TEXT, {"key": "1", "children": "b"}),
            h(TEXT, {"key": "2", "children": "c"}),
        ])
        recon.render(root, el2, 80, 24)
        f2 = _components.render_frame(root, 80)
        assert _plains(f2) == ["a", "b", "c"]

    def test_reorder_falls_back_to_full(self):
        from src.tui.ink.reconciler import Reconciler
        root = Reconciler.create_root()
        recon = Reconciler()
        el1 = h(BOX, None, [
            h(TEXT, {"key": "0", "children": "a"}),
            h(TEXT, {"key": "1", "children": "b"}),
        ])
        recon.render(root, el1, 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["a", "b"]
        # 重排 → key 位置不匹配 → 走完整算法
        el2 = h(BOX, None, [
            h(TEXT, {"key": "1", "children": "b"}),
            h(TEXT, {"key": "0", "children": "a"}),
        ])
        recon.render(root, el2, 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["b", "a"]

    def test_no_key_prop_update(self):
        from src.tui.ink.reconciler import Reconciler
        root = Reconciler.create_root()
        recon = Reconciler()
        el1 = h(BOX, None, [h(TEXT, {"children": "aaa"}), h(TEXT, {"children": "bbb"})])
        recon.render(root, el1, 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["aaa", "bbb"]
        # 无 key 列表 props 更新 → 复用同一位置 fiber
        el2 = h(BOX, None, [h(TEXT, {"children": "AAA"}), h(TEXT, {"children": "BBB"})])
        recon.render(root, el2, 80, 24)
        assert _plains(_components.render_frame(root, 80)) == ["AAA", "BBB"]
