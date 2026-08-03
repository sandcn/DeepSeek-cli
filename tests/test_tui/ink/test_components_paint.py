"""测试 ink/components.py — 内置 host paint（render_frame / _paint 隔离）。

方向2 P7（建议7）：内置 host paint（text/spacer/container）异常隔离——
单节点 paint 抛异常 → 该节点跳过、整帧仍渲染、异常不传播（与自定义 host
一致）。layout 异常仍由 session 层退避兜底（本测试不覆盖 layout）。

★ 命名说明：本文件命名 ``test_components_paint`` 而非 ``test_components``
  —— ``tests/test_tui/test_components.py`` 已存在（app 组件渲染断言），
  与 ``tests/test_tui/ink/test_components.py`` 基名冲突会触发 pytest
  import file mismatch（目录无 __init__.py 时按基名注册模块）。改用
  唯一基名避免整目录收集失败。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.ink.fiber import Fiber
from src.tui.ink.layout import LayoutBox
from src.tui.ink.output import Line
from src.tui.ink import components as _components


class TestBuiltinPaintIsolation:
    """方向2 P7（建议7）— 内置 host paint 异常隔离。"""

    def _make_static_tree(self):
        """构造未布局（无 _wrapped_lines）的 static 容器 + 两个 text 子节点。

        手工构造 fiber 树（不经 Reconciler 布局）——使 paint 阶段走
        ``wrap_text_lines`` 回退路径（正常调和流程下 _wrapped_lines 已缓存，
        不会触发该调用；本测试直接验证 paint 隔离本身）。
        """
        root = Fiber("host", "static", {})
        root.layout_box = LayoutBox(x=0, y=0, w=80, h=2)
        child0 = Fiber("host", "text", {"children": "first"})
        child0.layout_box = LayoutBox(x=0, y=0, w=80, h=1)
        child1 = Fiber("host", "text", {"children": "second"})
        child1.layout_box = LayoutBox(x=0, y=1, w=80, h=1)
        child0.sibling = child1
        root.child = child0
        return root

    def test_text_paint_exception_isolated_regression(self):
        """单 text 节点 paint 抛异常 → 该节点跳过、整帧仍渲染、异常不传播。"""
        root = self._make_static_tree()
        calls = {"n": 0}

        def flaky(text, width, style=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("paint boom")
            return [Line.of(text, style)] if text else []

        with patch("src.tui.ink.components.wrap_text_lines", side_effect=flaky):
            frame = _components.render_frame(root, 80)  # 不抛异常
        assert frame.height == 2
        # 第一个节点 paint 失败被跳过（空行）；第二个正常绘制
        assert frame.lines[0].plain == ""
        assert frame.lines[1].plain == "second"

    def test_container_paint_exception_isolated_regression(self):
        """容器（BOX/STATIC/APP）paint（_paint_border）抛异常 → 整帧仍渲染。"""
        root = Fiber("host", "static", {})
        root.layout_box = LayoutBox(x=0, y=0, w=80, h=2)
        child0 = Fiber("host", "static", {"border": 1})
        child0.layout_box = LayoutBox(x=0, y=0, w=80, h=2)
        child1 = Fiber("host", "text", {"children": "ok"})
        child1.layout_box = LayoutBox(x=0, y=1, w=80, h=1)
        child0.sibling = child1
        root.child = child0

        with patch("src.tui.ink.components._paint_border", side_effect=RuntimeError("border boom")):
            frame = _components.render_frame(root, 80)  # 不抛异常
        assert frame.height == 2
        # child0 因 border 失败跳过（空行）；child1 正常绘制
        assert frame.lines[0].plain == ""
        assert frame.lines[1].plain == "ok"


class TestBorderZeroWidthGuard:
    """方向1 — box.w=0 边框负索引防御（修复前 x1=x0-1 负索引写污染画布）。"""

    def test_zero_width_border_no_crash(self):
        """LayoutBox(w=0,h=1) 边框绘制不抛异常且画布无越界写。"""
        root = Fiber("host", "static", {"border": 1})
        root.layout_box = LayoutBox(x=0, y=0, w=0, h=1)
        frame = _components.render_frame(root, 0)  # 不抛异常（width 与 box.w 匹配）
        assert frame.height == 1

    def test_zero_height_border_no_crash(self):
        """LayoutBox(w=5,h=0) 边框绘制不抛异常（零高直接返回）。"""
        root = Fiber("host", "static", {"border": 1})
        root.layout_box = LayoutBox(x=0, y=0, w=5, h=0)
        frame = _components.render_frame(root, 5)  # 不抛异常
        assert frame.height == 1

    def test_border_normal_still_draws(self):
        """正常 box 边框仍绘制（防御不破坏既有绘制）。"""
        root = Fiber("host", "static", {"border": 1})
        root.layout_box = LayoutBox(x=0, y=0, w=80, h=3)
        frame = _components.render_frame(root, 80)
        assert frame.height == 3
        top = frame.lines[0].plain
        assert top.startswith("┌") and top.endswith("┐")


class TestCanvasRowCache:
    """方向4 — 画布行级缓存：同引用同 box 整行复用 Line 对象（identity）。"""

    def test_same_ref_same_box_reuses_line(self):
        """同 styled/text 引用 + 同 box 两次 render_frame → 第二次复用 Line 对象。"""
        from src.tui.ink.element import h, TEXT
        from src.tui.ink.reconciler import Reconciler
        r = Reconciler()
        root = r.create_root()
        el = h(TEXT, {"children": "hello"})
        r.render(root, el, 80, 24)
        frame1 = _components.render_frame(root, 80)
        first_line = frame1.lines[0]
        # 第二次渲染（同 fiber 同 props 同 box）→ 画布行直接复用 Line 对象
        r.render(root, el, 80, 24)
        frame2 = _components.render_frame(root, 80)
        assert frame2.lines[0] is first_line, (
            "同引用同 box 应整行复用 Line 对象（identity 短路）"
        )

    def test_box_change_rebuilds(self):
        """box 变化（宽度）→ 缓存失效重建（不同 Line 对象）。"""
        from src.tui.ink.element import h, TEXT
        from src.tui.ink.reconciler import Reconciler
        r = Reconciler()
        root = r.create_root()
        el = h(TEXT, {"children": "hello"})
        r.render(root, el, 80, 24)
        frame1 = _components.render_frame(root, 80)
        first_line = frame1.lines[0]
        # 显式宽度变化 → box.w 变化 → 缓存失效
        el2 = h(TEXT, {"children": "hello", "width": 10})
        r.render(root, el2, 80, 24)
        frame2 = _components.render_frame(root, 10)
        assert frame2.lines[0] is not first_line

    def test_ref_change_rebuilds(self):
        """styled 内容变化 → 缓存失效重建（不同 Line 对象）。"""
        from src.tui.ink.element import h, TEXT
        from src.tui.ink.output import StyledRun
        from src.tui.ink.reconciler import Reconciler
        r = Reconciler()
        root = r.create_root()
        el = h(TEXT, {"styled": [StyledRun("hello", None)]})
        r.render(root, el, 80, 24)
        frame1 = _components.render_frame(root, 80)
        # 内容变化（hello → world）→ 换行结果变化 → 重建
        el2 = h(TEXT, {"styled": [StyledRun("world", None)]})
        r.render(root, el2, 80, 24)
        frame2 = _components.render_frame(root, 80)
        assert frame2.lines[0] is not frame1.lines[0]


class TestCommittedPrefixCache:
    """committed-chat 帧前缀复用（长回答 + 子代理 CPU 100% 修复）。

    ``chat_view._paint`` 维护 ``fiber._committed_prefix``（身份 Line 引用），
    ``render_frame`` 经该前缀 + 尾部重建 Frame——大历史下渲染 O(live)，
    不再每帧全量重建整帧。本测试锁定前缀复用 / 增量扩展 / 输出一致性。
    """

    def _make_root(self, lines, tail_text: str = "tail"):
        """构造共享 reconciler/root（同 fiber 跨帧复用，前缀缓存生命周期内扩展）。"""
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        import src.tui.app.chat_view as _cv
        _cv.register()  # 幂等：注册 committed-chat host
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h("committed-chat", {"lines": lines}),
            h(TEXT, {"children": tail_text}),
        ])
        return r, root, el

    def test_committed_prefix_reused_across_frames(self):
        """同 committed_lines 引用两帧 → 前缀 Line 对象身份复用（免重建）。"""
        from src.tui.ink.output import StyledRun
        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(500)]
        r, root, el = self._make_root(lines)
        r.render(root, el, 80, 24)
        f1 = _components.render_frame(root, 80)
        assert f1.height == 501  # 500 committed + 1 tail
        assert f1.lines[0].plain == "line 0"
        assert f1.lines[499].plain == "line 499"
        assert f1.lines[500].plain == "tail"
        # 同 root 同 lines 再次渲染 → 前缀 Line 对象身份复用（大历史下 O(1)）
        r.render(root, el, 80, 24)
        f2 = _components.render_frame(root, 80)
        assert f2.lines[0] is f1.lines[0]
        assert f2.lines[499] is f1.lines[499]

    def test_committed_prefix_extend_on_growth(self):
        """committed_lines 原地增长（增量提交）→ 前缀同一列表增量追加。"""
        from src.tui.ink.output import StyledRun
        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(100)]
        r, root, el = self._make_root(lines)
        r.render(root, el, 80, 24)
        f1 = _components.render_frame(root, 80)
        cc = _components._find_committed_chat(root)
        prefix = cc._committed_prefix[1]
        assert len(prefix) == 100
        # 原地 extend（模拟 commit_open_block 增量提交）→ 前缀同一列表追加
        lines.extend(Line([StyledRun(f"new {i}", None)]) for i in range(5))
        r.render(root, el, 80, 24)
        f2 = _components.render_frame(root, 80)
        assert f2.height == 106
        assert f2.lines[100].plain == "new 0"
        assert f2.lines[104].plain == "new 4"
        # 缓存前缀仍是同一列表对象（增量扩展，未全量重建）
        assert cc._committed_prefix[1] is prefix
        assert len(cc._committed_prefix[1]) == 105

    def test_committed_prefix_matches_canvas_reference(self):
        """前缀快路径输出与旧全画布转换输出字节一致。"""
        from src.tui.ink.output import StyledRun
        from unittest.mock import patch
        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(50)]
        r, root, el = self._make_root(lines)
        r.render(root, el, 80, 24)
        f_fast = _components.render_frame(root, 80)
        fast_plain = [l.plain for l in f_fast.lines]
        # 旧 _paint（始终写画布）作为参考实现
        import src.tui.app.chat_view as _cv
        old = _cv._paint

        def old_paint(fiber, canvas):
            box = fiber.layout_box
            lns = fiber.props.get("lines") or []
            for i, line in enumerate(lns):
                row = box.y + i
                if 0 <= row < len(canvas):
                    canvas[row] = line

        with patch.object(_cv, "_paint", side_effect=old_paint):
            r2, root2, el2 = self._make_root(lines)
            r2.render(root2, el2, 80, 24)
            f_ref = _components.render_frame(root2, 80)
        assert [l.plain for l in f_ref.lines] == fast_plain


class TestCommittedPrefixNonTop:
    """方向3 — committed-chat 非顶部（y>0，如 TopHeader 在其上方）前缀路径。

    render_frame 非顶部前缀改为「头部画布 + 前缀 + 尾部画布」直接拼接——
    头部行（TopHeader）正确保留、前缀 Line 身份复用、尾部 live 区重建。
    """

    def test_non_top_prefix_preserves_header_and_tail(self):
        """committed-chat 在 header 下方：Frame = 头部行 + 前缀 + 尾部。"""
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.output import StyledRun
        import src.tui.app.chat_view as _cv
        _cv.register()  # 幂等

        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(30)]
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": "HEADER"}),
            h("committed-chat", {"lines": lines}),
            h(TEXT, {"children": "TAIL"}),
        ])
        r.render(root, el, 80, 24)
        f1 = _components.render_frame(root, 80)
        assert f1.height == 32  # header + 30 + tail
        assert f1.lines[0].plain == "HEADER"
        assert f1.lines[1].plain == "line 0"
        assert f1.lines[30].plain == "line 29"
        assert f1.lines[31].plain == "TAIL"

        # 同 root 同 lines 再次渲染：前缀 Line 身份复用（大历史 O(1)）
        r.render(root, el, 80, 24)
        f2 = _components.render_frame(root, 80)
        assert f2.lines[0].plain == "HEADER"
        assert f2.lines[1] is f1.lines[1]      # 前缀行身份复用
        assert f2.lines[30] is f1.lines[30]    # 前缀行身份复用
        assert f2.lines[31].plain == "TAIL"
