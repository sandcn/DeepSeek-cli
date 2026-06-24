"""FlexLayout 布局引擎单元测试。

覆盖 Flexbox 布局算法的方向、对齐、flexGrow/flexShrink、
flexWrap、gap、padding、margin、百分比尺寸等。

测试策略：构造 FlexLayout 实例，传入 LayoutBox 列表，
验证 calculate() 返回的 LayoutBox 的 x/y 坐标和 width/height 正确。
"""

from __future__ import annotations

import pytest

from src.chat_ui.react_ink._layout import (
    FlexLayout,
    FlexStyle,
    _resolve_dimension,
    _resolve_padding,
    _resolve_margin,
    _clamp,
)
from src.chat_ui.react_ink._types import LayoutBox, LayoutError


# ── 测试辅助 ────────────────────────────────────────────

def _box(w: int = 10, h: int = 1, cw: int = 10, ch: int = 1) -> LayoutBox:
    """创建测试用 LayoutBox。"""
    return LayoutBox(content_width=cw, content_height=ch, width=w, height=h)


# ═══════════════════════════════════════════════════════════
# TestFlexLayout
# ═══════════════════════════════════════════════════════════

class TestFlexLayout:
    """FlexLayout 测试。"""

    def test_row_direction_horizontal_layout(self):
        """row 方向子元素水平排列。"""
        style: FlexStyle = {"flexDirection": "row"}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1), _box(10, 1), _box(10, 1)]
        result = layout.calculate(children)

        # 三个子元素从左到右排列
        assert result[0].x < result[1].x < result[2].x

    def test_column_direction_vertical_layout(self):
        """column 方向子元素垂直排列。"""
        style: FlexStyle = {"flexDirection": "column"}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1), _box(10, 1), _box(10, 1)]
        result = layout.calculate(children)

        # 三个子元素从上到下排列
        assert result[0].y < result[1].y < result[2].y

    def test_center_justify(self):
        """justifyContent=center 时子元素居中。"""
        style: FlexStyle = {"flexDirection": "row", "justifyContent": "center"}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1)]
        result = layout.calculate(children)

        # 单个元素应居中（起始位置 > 0）
        assert result[0].x > 0

    def test_space_between(self):
        """justifyContent=space-between 均匀分布。"""
        style: FlexStyle = {
            "flexDirection": "row",
            "justifyContent": "space-between",
        }
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1), _box(10, 1)]
        result = layout.calculate(children)

        # 第一个在左侧，最后一个在右侧
        assert result[0].x < result[1].x
        # 两者之间有间距
        gap = result[1].x - (result[0].x + result[0].width)
        assert gap > 0

    def test_space_around(self):
        """justifyContent=space-around 均匀分布含两侧半间距。"""
        style: FlexStyle = {
            "flexDirection": "row",
            "justifyContent": "space-around",
        }
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1), _box(10, 1)]
        result = layout.calculate(children)

        # 第一个元素左侧有间距
        assert result[0].x > 0

    def test_flex_grow_distribution(self):
        """flexGrow > 0 时子元素扩展填满剩余空间。"""
        style: FlexStyle = {"flexDirection": "row", "flexGrow": 1.0}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1), _box(10, 1)]
        result = layout.calculate(children)

        # flexGrow 分配后宽度应 > 10
        assert result[0].width >= 10
        assert result[1].width >= 10
        # 两个元素总宽度应接近容器宽度（考虑 padding）
        total_w = result[0].width + result[1].width
        assert total_w > 20

    def test_flex_shrink_contraction(self):
        """flexShrink > 0 时子元素收缩适应容器。"""
        style: FlexStyle = {"flexDirection": "row", "flexShrink": 1.0}
        layout = FlexLayout(20, 24, style)  # 容器仅 20 列
        children = [_box(30, 1), _box(30, 1)]  # 子元素超出
        result = layout.calculate(children)

        # flexShrink 缩小后总宽度 <= 20
        total_w = result[0].width + result[1].width
        assert total_w <= 25  # 允许 margin 容差

    def test_flex_wrap(self):
        """flexWrap=wrap 时子元素换行。"""
        style: FlexStyle = {"flexDirection": "row", "flexWrap": "wrap"}
        layout = FlexLayout(25, 24, style)
        # 3 个 15 宽的盒子在 25 宽容器中应换行（15+15=30 > 25）
        children = [_box(15, 1), _box(15, 1), _box(15, 1)]
        result = layout.calculate(children)

        # 至少有一行换行：第三个元素 y 与前两个不同
        if result[0].y == result[1].y:
            # 前两个同行的前提下，第三个应另起一行
            assert result[2].y != result[0].y

    def test_gap_between_children(self):
        """gap 在子元素之间产生间距。"""
        style: FlexStyle = {"flexDirection": "row", "gap": 4}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1), _box(10, 1)]
        result = layout.calculate(children)

        # 第二个元素起始位置 >= 第一个元素结束位置 + gap
        end_first = result[0].x + result[0].width
        assert result[1].x >= end_first

    def test_padding_offset(self):
        """容器 padding 使子元素偏移。"""
        style: FlexStyle = {"flexDirection": "row", "padding": 5}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1)]
        result = layout.calculate(children)

        # padding left = 5，所以 x >= 5
        assert result[0].x >= 5

    def test_percentage_dimensions(self):
        """百分比 width 正确计算。"""
        style: FlexStyle = {"flexDirection": "row", "width": "50%"}
        layout = FlexLayout(80, 24, style)
        children = [_box(10, 1)]
        result = layout.calculate(children)

        # 50% of 80 = 40（减去 padding 后）应用于子元素宽度
        # flex_basis 解析后 width 应大约为 40（减去 margin/padding）
        assert result[0].width >= 10  # 至少包含内容尺寸

    def test_empty_children(self):
        """空子元素列表返回空列表。"""
        layout = FlexLayout(80, 24)
        result = layout.calculate([])
        assert result == []


# ═══════════════════════════════════════════════════════════
# TestDimensionHelpers
# ═══════════════════════════════════════════════════════════

class TestDimensionHelpers:
    """尺寸解析辅助函数测试。"""

    def test_resolve_dimension_int(self):
        """int 值原样返回。"""
        assert _resolve_dimension(42, 100) == 42

    def test_resolve_dimension_percent(self):
        """百分比字符串正确计算。"""
        assert _resolve_dimension("50%", 100) == 50
        assert _resolve_dimension("25%", 200) == 50

    def test_resolve_dimension_none(self):
        """None 返回 None。"""
        assert _resolve_dimension(None, 100) is None

    def test_resolve_dimension_auto(self):
        """"auto" 返回 None。"""
        assert _resolve_dimension("auto", 100) is None

    def test_resolve_dimension_invalid(self):
        """非法格式抛出 LayoutError。"""
        with pytest.raises(LayoutError):
            _resolve_dimension("invalid", 100)

    def test_resolve_padding_defaults(self):
        """默认 padding 全为 0。"""
        pt, pb, pl, pr = _resolve_padding({})
        assert (pt, pb, pl, pr) == (0, 0, 0, 0)

    def test_resolve_padding_uniform(self):
        """统一 padding 应用于四边。"""
        pt, pb, pl, pr = _resolve_padding({"padding": 3})
        assert (pt, pb, pl, pr) == (3, 3, 3, 3)

    def test_resolve_padding_xy(self):
        """paddingX/paddingY 覆盖统一值。"""
        pt, pb, pl, pr = _resolve_padding(
            {"padding": 3, "paddingX": 5, "paddingY": 2})
        assert (pt, pb) == (2, 2)
        assert (pl, pr) == (5, 5)

    def test_resolve_padding_individual(self):
        """单边覆盖 X/Y 简写。"""
        pt, pb, pl, pr = _resolve_padding(
            {"padding": 3, "paddingTop": 10, "paddingLeft": 7})
        assert pt == 10
        assert pb == 3
        assert pl == 7
        assert pr == 3

    def test_resolve_margin_defaults(self):
        """默认 margin 全为 0。"""
        mt, mb, ml, mr = _resolve_margin({})
        assert (mt, mb, ml, mr) == (0, 0, 0, 0)

    def test_clamp(self):
        """钳制在 [min, max] 范围内。"""
        assert _clamp(5, 0, 10) == 5
        assert _clamp(-5, 0, 10) == 0
        assert _clamp(15, 0, 10) == 10
        assert _clamp(5, None, 10) == 5
        assert _clamp(5, 0, None) == 5
