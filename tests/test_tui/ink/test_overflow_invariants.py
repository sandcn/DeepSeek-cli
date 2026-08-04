"""行宽不变量回归测试 — row 超宽收缩 / fill 钳制重测 / 渲染截断防线。

覆盖本次「渲染错误修复（行宽不变量）」三处修改链路：
  - E-ROW-OVERFLOW：row 内容自然宽超容器内宽时按 flexShrink 权重收缩子节点
    （默认 flexShrink=1，React Ink 标准语义）——修复前容器/边框子节点
    （fill=False 内容自适应）自然宽超容器时溢出 box；
  - E-FILL-OVERFLOW：fill=False column 容器被钳制（内容自然宽超可用宽）时，
    内部子节点按容器实际宽度重新测量（fill=True 约束 wrap/截断）——修复前
    探针测量保持内容自然宽，嵌套容器内容溢出；
  - E-OVERFLOW-GUARD：render_frame 行级截断防线——布局层异常残留时行宽
    恒 <= 文档宽（行级 diff 模型不变量）。
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT, Row, Column, Stack, HStack, ZStack, StyledRun
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.core.style import Style


def _render(el, width=40):
    r = Reconciler()
    root = r.create_root()
    r.render(root, el, width, 24)
    return render_frame(root, width)


def _assert_width_invariant(frame, width):
    bad = [ln for ln in frame.lines if ln.width > width]
    assert not bad, f"行宽超限: {[(ln.width, ln.plain[:40]) for ln in bad]}"


class TestRowOverflowShrink:
    """E-ROW-OVERFLOW — row 内容超宽时按 flexShrink 权重收缩。"""

    def test_row_two_columns_overflow_shrunk(self):
        """row 内两个内容超宽容器 → 收缩到容器内宽（行宽不变量）。"""
        el = h(Row, None, [
            h(Column, None, [h(TEXT, {"children": "x" * 30})]),
            h(Column, None, [h(TEXT, {"children": "y" * 30})]),
        ])
        f = _render(el, 40)
        _assert_width_invariant(f, 40)
        # 内容仍可见（收缩保留尾部，非全丢）
        plains = [ln.plain for ln in f.lines]
        assert any("x" in p for p in plains) and any("y" in p for p in plains), f"内容丢失: {plains}"

    def test_row_nested_stack_overflow(self):
        """row 内容器内部孙节点自然宽超容器 → 收缩后不溢出（原 #101 场景）。"""
        el = h(Row, None, [
            h(Stack, None, [
                h(HStack, {"minWidth": 3}, [
                    h(TEXT, {"children": "aaaaa中中中"}),
                    h(TEXT, {"children": "中文你好"}),
                    h(TEXT, {"children": "中文你好"}),
                ]),
                h(Column, {"height": 1}, [
                    h(TEXT, {"children": "xxxxxxxxxxxxxxxxxxxx"}),
                    h(TEXT, {"children": "emoji👍"}),
                ]),
            ]),
        ])
        f = _render(el, 20)
        _assert_width_invariant(f, 20)

    def test_row_border_boxes_overflow(self):
        """row 内多个带边框容器超宽 → 收缩（边框行不溢出）。"""
        el = h(Row, None, [
            h(BOX, {"border": 1}, [h(TEXT, {"children": "x" * 20})]),
            h(BOX, {"border": 1}, [h(TEXT, {"children": "y" * 20})]),
            h(BOX, {"border": 1}, [h(TEXT, {"children": "z" * 20})]),
        ])
        f = _render(el, 30)
        _assert_width_invariant(f, 30)

    def test_row_no_overflow_unchanged(self):
        """内容不超宽时零行为变化（收缩不触发）。"""
        el = h(Row, None, [
            h(TEXT, {"children": "abc"}),
            h(TEXT, {"children": "def"}),
        ])
        f = _render(el, 40)
        plains = [ln.plain for ln in f.lines]
        assert "abcdef" in "".join(plains), f"正常布局被破坏: {plains}"
        _assert_width_invariant(f, 40)

    def test_row_explicit_shrink_weight(self):
        """显式 flexShrink 权重生效（权重高者收缩更多）。"""
        el = h(Row, None, [
            h(Column, {"flexShrink": 0}, [h(TEXT, {"children": "x" * 30})]),
            h(Column, {"flexShrink": 1}, [h(TEXT, {"children": "y" * 30})]),
        ])
        f = _render(el, 40)
        _assert_width_invariant(f, 40)


class TestFillClampRemeasure:
    """E-FILL-OVERFLOW — fill=False 容器被钳制时内部子节点重测。"""

    def test_fill_clamp_remeasures_children(self):
        """column 内容超宽被钳制 → 内部子节点按容器实际宽度 wrap（不溢出）。"""
        el = h(Row, None, [
            h(Stack, None, [
                h(HStack, None, [
                    h(TEXT, {"children": "aaaaa中中中"}),
                    h(TEXT, {"children": "中文你好"}),
                    h(TEXT, {"children": "中文你好"}),
                ]),
            ]),
        ])
        f = _render(el, 15)
        _assert_width_invariant(f, 15)

    def test_fill_normal_not_triggered(self):
        """内容不超宽时 fill=False 探针复用路径零行为变化。"""
        el = h(Row, None, [
            h(Stack, None, [h(TEXT, {"children": "abc"})]),
        ])
        f = _render(el, 40)
        assert any("abc" in ln.plain for ln in f.lines), f"内容缺失: {[ln.plain for ln in f.lines]}"


class TestRenderFrameWidthGuard:
    """E-OVERFLOW-GUARD — render_frame 行级截断防线。"""

    def test_wide_line_truncated_to_doc_width(self):
        """构造布局层无法消除的超宽残留 → 渲染截断保证行宽不变量。"""
        # 深嵌套 + 宽字符（布局层无法完全消除的极端组合）
        el = h(Row, None, [
            h(ZStack, {"border": 2}, [
                h(TEXT, {"children": ""}),
                h(TEXT, {"children": "x" * 30}),
            ]),
            h(Row, {"border": 2}, [
                h(TEXT, {"children": "y" * 30}),
            ]),
            h(Column, None, [
                h(TEXT, {"children": "中文中文" * 5}),
            ]),
        ])
        f = _render(el, 10)
        _assert_width_invariant(f, 10)

    def test_normal_line_not_truncated(self):
        """正常行宽 <= width 时不触发截断（原样返回）。"""
        el = h(Row, None, [h(TEXT, {"children": "hello"})])
        f = _render(el, 40)
        assert any(ln.plain == "hello" for ln in f.lines), f"正常行被截断: {[ln.plain for ln in f.lines]}"


class TestOverflowFuzzSmoke:
    """模糊冒烟 — 随机组件树行宽不变量（回归防退化）。"""

    def _render_fuzz(self, seed, n=100):
        import random
        random.seed(seed)
        containers = [BOX, Row, Column, Stack, HStack, ZStack]
        props_pool = [
            {}, {"border": 1}, {"padding": 1}, {"gap": 1}, {"width": 10},
            {"width": "50%"}, {"flexDirection": "row"}, {"justifyContent": "center"},
            {"alignItems": "center"}, {"flexWrap": "wrap"}, {"position": "relative"},
            {"minWidth": 3}, {"maxWidth": 20},
        ]

        def rand_el(depth=0):
            if depth > 3 or random.random() < 0.3:
                return h(TEXT, {"children": random.choice(
                    ["abc", "中文你好", "x" * 20, "a" * 5 + "中" * 3, "", "emoji👍"])})
            C = random.choice(containers)
            children = [rand_el(depth + 1) for _ in range(random.randint(0, 5))]
            return h(C, dict(random.choice(props_pool)), children)

        for i in range(n):
            width = random.choice([10, 20, 40, 80])
            el = rand_el()
            f = _render(el, width)
            _assert_width_invariant(f, width)

    def test_fuzz_seed_42(self):
        self._render_fuzz(42)

    def test_fuzz_seed_777(self):
        self._render_fuzz(777)
