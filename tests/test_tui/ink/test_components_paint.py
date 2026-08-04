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

        def flaky(text, width, style=None, hard=False):
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
    """committed 帧前缀复用（长回答 + 子代理 CPU 100% 修复）。

    ``staticlines._paint`` 维护 ``fiber._committed_prefix``（身份 Line 引用），
    ``render_frame`` 经该前缀 + 尾部重建 Frame——大历史下渲染 O(live)，
    不再每帧全量重建整帧。本测试锁定前缀复用 / 增量扩展 / 输出一致性。
    """

    def _make_root(self, lines, tail_text: str = "tail"):
        """构造共享 reconciler/root（同 fiber 跨帧复用，前缀缓存生命周期内扩展）。"""
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink import StaticLines
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(StaticLines, {"lines": lines}),
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
        import src.tui.ink.widgets.staticlines as _sl
        old = _sl._paint

        def old_paint(fiber, canvas):
            box = fiber.layout_box
            lns = fiber.props.get("lines") or []
            for i, line in enumerate(lns):
                row = box.y + i
                if 0 <= row < len(canvas):
                    canvas[row] = line

        with patch.object(_sl, "_paint", side_effect=old_paint):
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
        """StaticLines 在 header 下方：Frame = 头部行 + 前缀 + 尾部。"""
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.output import StyledRun
        from src.tui.ink import StaticLines

        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(30)]
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": "HEADER"}),
            h(StaticLines, {"lines": lines}),
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


class TestCommittedOverflowGuard:
    """E-COMMITTED-OVERFLOW 防御 — committed 前缀行超宽（reflow 未执行/失败）。

    终端宽度变化后 committed_lines 仍按旧宽度 wrap（reflow 未同步/异常被吞）
    时，前缀含超宽行——render_frame 前缀复用路径不经 E-OVERFLOW-GUARD，超宽
    行直接进帧破坏行宽不变量。修复：chat_view._paint 缓存重建时 O(n) 检查
    行宽（``all_ok`` 标志），render_frame 对 all_ok=False 前缀截断超宽行。
    """

    def _make_root(self, lines, tail_text: str = "tail", width: int = 80):
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink import StaticLines
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(StaticLines, {"lines": lines}),
            h(TEXT, {"children": tail_text}),
        ])
        return r, root, el

    def test_oversize_prefix_gets_truncated(self):
        """committed_lines 含超宽行（宽度 100 的 Line）→ 前缀复用路径截断到宽 60。"""
        from src.tui.ink.output import StyledRun
        lines = [
            Line([StyledRun("a" * 100, None)]),   # 超宽（> 60）
            Line([StyledRun("ok", None)]),         # 正常
        ]
        r, root, el = self._make_root(lines)
        # 宽 60 渲染：前缀含 100 宽行 → all_ok=False → 截断
        r.render(root, el, 60, 24)
        f = _components.render_frame(root, 60)
        assert f.lines[0].width <= 60, (
            f"超宽 committed 行应被截断: {f.lines[0].width}"
        )
        assert f.lines[1].plain == "ok"
        assert f.lines[2].plain == "tail"
        # 正常行保持原样（未截断）
        assert f.lines[1].width == 2

    def test_all_ok_flag_set_on_cache_build(self):
        """缓存重建时 all_ok 标志正确（正常行 True / 超宽行 False）。"""
        from src.tui.ink.output import StyledRun
        # 正常行 → all_ok=True
        lines_ok = [Line([StyledRun("abc", None)])]
        r, root, el = self._make_root(lines_ok)
        r.render(root, el, 60, 24)
        _components.render_frame(root, 60)
        cc = _components._find_committed_chat(root)
        assert cc._committed_prefix[2] is True
        # 超宽行 → all_ok=False
        lines_bad = [Line([StyledRun("a" * 100, None)])]
        r2, root2, el2 = self._make_root(lines_bad)
        r2.render(root2, el2, 60, 24)
        _components.render_frame(root2, 60)
        cc2 = _components._find_committed_chat(root2)
        assert cc2._committed_prefix[2] is False

    def test_oversize_prefix_no_crash_on_extend(self):
        """原地 extend 路径保留 all_ok（旧前缀 all_ok=False 时新增行也检查）。"""
        from src.tui.ink.output import StyledRun
        lines = [Line([StyledRun("a" * 100, None)])]  # 超宽 → all_ok=False
        r, root, el = self._make_root(lines)
        r.render(root, el, 60, 24)
        _components.render_frame(root, 60)
        # 原地 extend 正常行
        lines.append(Line([StyledRun("newline", None)]))
        r.render(root, el, 60, 24)
        f = _components.render_frame(root, 60)
        assert f.lines[0].width <= 60  # 超宽行仍截断
        assert f.lines[1].plain == "newline"
        assert f.lines[2].plain == "tail"


class TestFramePrefixConcatenation:
    """P-H4 评估锁定 — render_frame 前缀拼接（prefix + tail）行为。

    评估结论：不做结构性修改（prefix 列表跨帧可变，Frame.lines 直接复用
    prefix 引用会与上一帧共享列表破坏 diff 状态；prefix + tail 为浅拷贝，
    指针复制非内容复制，大历史下仍远优于全量重建）。本测试锁定拼接语义
    与身份复用不变（防未来改动破坏）。
    """

    def _frame_pair(self, n_lines=200):
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.output import StyledRun
        from src.tui.ink import StaticLines
        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(n_lines)]
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(StaticLines, {"lines": lines}),
            h(TEXT, {"children": "TAIL"}),
        ])
        r.render(root, el, 80, 24)
        f1 = _components.render_frame(root, 80)
        r.render(root, el, 80, 24)
        f2 = _components.render_frame(root, 80)
        return f1, f2, n_lines

    def test_top_prefix_tail_combined(self):
        """顶部前缀 + tail 拼接：高度与行内容正确。"""
        f1, _, n = self._frame_pair()
        assert f1.height == n + 1
        assert f1.lines[n].plain == "TAIL"
        assert f1.lines[0].plain == "line 0"
        assert f1.lines[n - 1].plain == f"line {n - 1}"

    def test_prefix_lines_identity_preserved(self):
        """前缀行 Line 身份跨帧复用（prefix + tail 浅拷贝未破坏身份短路）。"""
        f1, f2, n = self._frame_pair()
        for i in range(n):
            assert f2.lines[i] is f1.lines[i]
        # tail 行内容不变（新对象，值相等）
        assert f2.lines[n].plain == f1.lines[n].plain


class TestCanvasRowBatchAppend:
    """方向4 — _canvas_row_to_line 批量 append 优化（输出一致性锁定）。"""

    def _frame(self, el, width=30):
        from src.tui.ink.reconciler import Reconciler
        root = Reconciler.create_root()
        recon = Reconciler()
        recon.render(root, el, width, 24)
        return _components.render_frame(root, width)

    def test_batch_append_output_identical(self):
        """批量 append 与逐字符 append 输出一致（多 style 段 + 间隙）。"""
        from src.tui.ink import h, BOX, TEXT, StyledRun
        from src.tui.core.style import Style
        frame = self._frame(h(BOX, {"width": 20, "flexDirection": "row", "justifyContent": "center"}, [
            h(TEXT, {"children": "AA", "color": 45}),
            h(TEXT, {"children": "BB", "bold": True}),
        ]))
        line = frame.lines[0]
        # 非全屏流动模型：行宽 = 内容实际列（含前导偏移，不填充右边界）
        assert line.plain == "        AABB", f"实际 {line.plain!r}"
        # 第二段加粗
        assert any(r.style is not None and r.style.bold for r in line.runs)
        assert any(r.style is not None and r.style.fg == 45 for r in line.runs)

    def test_batch_append_cjk_gap(self):
        """批量 append 保留 CJK 间隙（显示宽度推进）。"""
        from src.tui.ink import h, BOX, TEXT
        frame = self._frame(h(BOX, {"width": 10, "flexDirection": "row", "justifyContent": "center"}, [
            h(TEXT, {"children": "中文"}),
            h(TEXT, {"children": "ab"}),
        ]))
        line = frame.lines[0]
        # 中文(4) + ab(2) = 6，center 偏移 (10-6)//2 = 2 → "  中文ab"
        assert line.plain == "  中文ab", f"实际 {line.plain!r}"
        assert line.width == 8


class TestMergeLineWideCharSecondCol:
    """E2 — _merge_line 宽字符第二列部分覆盖时新字符静默丢失修复。"""

    def _merge(self, row_dict, x, text):
        from src.tui.ink.output import StyledRun
        return _components._merge_line(row_dict, x, Line([StyledRun(text, None)]))

    def test_second_col_overwrite_not_lost(self):
        """row={0:('中'),2:('a')} + X@col1 → 输出 "X a"（X 不再静默丢失）。

        修复前：disjoint 快路径批量 update 后 row={0:'中',2:'a',1:'X'}，
        _canvas_row_to_line 中 col1 < prev（宽字符推进到 2）→ X 被跳过 → "中a"。
        修复后：宽字符第二列冲突检测到 → 逐键覆盖替换宽字符整体（row[0]=X、
        pop(1)）→ "X a"。
        """
        row = {0: ("中", None), 2: ("a", None)}
        merged = self._merge(row, 1, "X")
        line = _components._canvas_row_to_line(merged)
        assert line.plain == "X a", f"实际 {line.plain!r}"

    def test_bug61_residue_kept(self):
        """BUG-61 既有语义保持：覆盖宽字符首列 → 清除残留第二列。"""
        row = {0: ("中", None), 1: ("中", None)}
        merged = self._merge(row, 0, "a")
        line = _components._canvas_row_to_line(merged)
        assert line.plain == "a", f"实际 {line.plain!r}"

    def test_disjoint_fast_path_wide_second_col(self):
        """disjoint 但存在宽字符第二列冲突 → 降级逐键覆盖正确（X 不丢失）。"""
        row = {0: ("中", None), 3: ("x", None)}
        merged = self._merge(row, 1, "X")
        line = _components._canvas_row_to_line(merged)
        # 宽字符整体被替换（row[0]=X、pop(1)），x 保留在 col3 → "X  x"
        assert line.plain == "X  x", f"实际 {line.plain!r}"

    def test_disjoint_no_wide_conflict_batch_path(self):
        """无宽字符冲突的 disjoint 保持批量快路径（零行为变化）。"""
        row = {1: ("a", None), 2: ("b", None)}
        merged = self._merge(row, 0, "X")
        line = _components._canvas_row_to_line(merged)
        assert line.plain == "Xab", f"实际 {line.plain!r}"
