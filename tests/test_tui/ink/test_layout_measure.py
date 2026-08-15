"""布局舍入补偿 + wrap flexBasis 应用测试（L4）。

修复背景（2026-08-15 L4）：
  - 舍入偏差：``_shrink_row_children`` 以 float 迭代收缩、写回
    ``int(round(widths[i]))``——多子节点各自舍入后收缩总量可能偏差 ±1 列
    （舍入偏大导致总宽 > target_w，破坏行宽不变量）。修复：写回后核算实际
    总宽，若仍 > target_w 对 shrinkable 子节点循环减 1（预算内补偿，宁欠勿超）。
  - wrap flexBasis 失效：``direction=="row" and flex_wrap`` 分支测量子节点
    后未应用 ``flexBasis``（row 分支有、wrap 场景静默失效）。修复：wrap
    分支两次测量点后补 flexBasis 应用（与 row 分支同逻辑）。

本测试锁定：舍入不变量（总宽 <= target_w）、补偿生效、flexShrink:0 不受
补偿影响、wrap flexBasis 生效、换行判断基于应用后宽度、无 flexBasis 回归。
"""

from __future__ import annotations

import pytest

from src.tui.ink.fiber import Fiber, TAG_HOST
from src.tui.ink._layout_measure import LayoutBox, _measure, _shrink_row_children


def _text_fiber(children: str, **props) -> Fiber:
    """TEXT host fiber 最小构造（children 文本 + 附加 props）。"""
    return Fiber(TAG_HOST, "text", {"children": children, **props})


def _row_container(width: int, **props) -> Fiber:
    """row wrap 容器最小构造（显式 width + 可选 flexWrap/flexBasis 等）。"""
    p = {"flexDirection": "row", "width": width}
    p.update(props)
    return Fiber(TAG_HOST, "box", p)


def _link(container: Fiber, children: list[Fiber]) -> None:
    """链接容器子节点（child/sibling/return_ 指针）。"""
    for i, child in enumerate(children):
        if i == 0:
            container.child = child
        else:
            children[i - 1].sibling = child
        child.return_ = container


# ── L4：_shrink_row_children 舍入补偿 ─────────────────────

def test_shrink_row_rounding_overshoot_compensated_regression():
    """L4：多子节点舍入写回超宽时预算内补偿——收缩后总宽 <= target_w。

    3 子各宽 4（used=12），target=11（deficit=1）：per=1/3 → 每子 3.667
    round 到 4（舍入偏大，总宽 12 > 11）→ 补偿减 1 → 总宽 11。
    """
    children = [_text_fiber("aaaa"), _text_fiber("bbbb"), _text_fiber("cccc")]
    for i, c in enumerate(children):
        c.layout_box = LayoutBox(x=i * 4, y=0, w=4, h=1)
    _shrink_row_children(children, used_w=12, target_w=11, spacing=0, inner_x=0, inner_y=0)
    total = sum(c.layout_box.w for c in children)
    assert total <= 11
    # 补偿确实发生（超宽 1 列被减掉）
    assert total == 11


def test_shrink_row_rounding_no_overshoot_regression():
    """L4：正常收缩（舍入不超宽）输出不变——总宽 <= target_w。"""
    children = [_text_fiber("aaaa"), _text_fiber("bbbb"), _text_fiber("cccc")]
    for i, c in enumerate(children):
        c.layout_box = LayoutBox(x=i * 4, y=0, w=4, h=1)
    _shrink_row_children(children, used_w=12, target_w=10, spacing=0, inner_x=0, inner_y=0)
    total = sum(c.layout_box.w for c in children)
    assert total <= 10


def test_shrink_row_flex_shrink_zero_not_compensated_regression():
    """L4 回归：显式 flexShrink:0 子节点不参与收缩与补偿（宽度保持）。"""
    children = [
        _text_fiber("aaaaaa", flexShrink=0),  # 宽 6，禁缩
        _text_fiber("bbbb"),                   # 宽 4，shrink=1
    ]
    children[0].layout_box = LayoutBox(x=0, y=0, w=6, h=1)
    children[1].layout_box = LayoutBox(x=6, y=0, w=4, h=1)
    _shrink_row_children(children, used_w=10, target_w=9, spacing=0, inner_x=0, inner_y=0)
    assert children[0].layout_box.w == 6  # 显式禁缩不受影响
    total = sum(c.layout_box.w for c in children)
    assert total <= 9


# ── L4：wrap flexBasis 应用 ───────────────────────────────

def test_wrap_flex_basis_applied_regression():
    """L4：flexWrap="wrap" 容器子节点 flexBasis 生效（测量宽度被覆盖）。"""
    container = _row_container(40, flexWrap="wrap")
    child = _text_fiber("abc", flexBasis=8)  # 内容宽 3，flexBasis 8
    _link(container, [child])
    _measure(container, 0, 0, 100, fill=True)
    assert child.layout_box.w == 8


def test_wrap_no_flex_basis_content_width_regression():
    """L4 回归：无 flexBasis 时 wrap 子节点宽度 == 内容宽（行为不变）。"""
    container = _row_container(40, flexWrap="wrap")
    child = _text_fiber("abc")
    _link(container, [child])
    _measure(container, 0, 0, 100, fill=True)
    assert child.layout_box.w == 3  # "abc" 内容宽 3


def test_wrap_flex_basis_affects_line_break_regression():
    """L4：换行判断基于应用后宽度——flexBasis 使子节点超宽时正确换行。"""
    container = _row_container(10, flexWrap="wrap")
    c1 = _text_fiber("aa", flexBasis=8)      # 应用后宽 8（内容宽 2）
    c2 = _text_fiber("bbbb")                 # 内容宽 4，无 flexBasis
    _link(container, [c1, c2])
    _measure(container, 0, 0, 100, fill=True)
    # c1 占 0..8；c2 8+4=12 > wrap_inner_w=10 → 换行（第二行 y=1）
    assert c1.layout_box.w == 8
    assert c2.layout_box.w == 4
    assert c1.layout_box.y == 0
    assert c2.layout_box.y == 1


def test_wrap_flex_basis_greater_than_inner_w_solo_line_regression():
    """L4 边界：flexBasis 超 wrap_inner_w 时子节点单独成行（不超宽不溢出）。"""
    container = _row_container(5, flexWrap="wrap")
    c1 = _text_fiber("aa", flexBasis=20)  # 应用后宽 20 > wrap_inner_w=5
    c2 = _text_fiber("bb")                # 内容宽 2
    _link(container, [c1, c2])
    _measure(container, 0, 0, 100, fill=True)
    assert c1.layout_box.w == 20  # flexBasis 覆盖（显式声明，单独成行）
    assert c2.layout_box.w == 2
    assert c1.layout_box.y == 0
    assert c2.layout_box.y == 1  # c1 独占第一行，c2 换到第二行
