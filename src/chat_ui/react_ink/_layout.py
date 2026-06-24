"""Flexbox 布局引擎 — 纯 Python CSS Flexbox 子集实现。

支持的 CSS 属性：
  - flexDirection (row / column / row-reverse / column-reverse)
  - justifyContent (flex-start / flex-end / center / space-between / space-around / space-evenly)
  - alignItems (flex-start / flex-end / center / stretch / baseline)
  - alignContent (flex-start / flex-end / center / stretch / space-between / space-around / space-evenly)
  - flexGrow / flexShrink / flexBasis
  - flexWrap (nowrap / wrap / wrap-reverse)
  - gap / rowGap / columnGap
  - padding / margin（四边独立 + X/Y 简写）
  - width / height（数值 + 百分比）
  - minWidth / maxWidth / minHeight / maxHeight
  - display (flex / none)
  - position (relative / absolute / static)
  - overflow (visible / hidden)
  - aspectRatio

布局算法：两遍计算（measure → layout），输出标准化的 LayoutBox 数据结构。
布局器设计为独立 LayoutEngine 协议，未来可替换为 CSS Grid 等方案。

分两遍算法：
  1. measure：测量所有子元素的内容尺寸（自然宽高）— 由调用方完成
  2. layout：在容器约束下计算每个子元素的最终位置和尺寸

核心算法：
  主轴空间分配 → justifyContent 对齐 → alignItems 对齐 → flexWrap 换行 → alignContent
"""

from __future__ import annotations

import copy
from typing import Any, Literal, TypedDict

from ._types import LayoutBox, LayoutError


# ── 布局属性 Literal 类型 ─────────────────────────────────

FlexDirection = Literal["row", "row-reverse", "column", "column-reverse"]
FlexWrap = Literal["nowrap", "wrap", "wrap-reverse"]
JustifyContent = Literal[
    "flex-start", "center", "flex-end",
    "space-between", "space-around", "space-evenly",
]
AlignItems = Literal["flex-start", "center", "flex-end", "stretch", "baseline"]
AlignContent = Literal[
    "flex-start", "flex-end", "center", "stretch",
    "space-between", "space-around", "space-evenly",
]
Position = Literal["relative", "absolute", "static"]
Overflow = Literal["visible", "hidden"]
Display = Literal["flex", "none"]


# ── FlexStyle TypedDict ──────────────────────────────────

class FlexStyle(TypedDict, total=False):
    """Flex 布局样式属性集合。

    所有属性均为可选（total=False），未设置的属性使用默认值。
    flexGrow / flexShrink / flexBasis 等子元素属性在此版本中统一应用于所有子元素。
    """

    display: Display
    flexDirection: FlexDirection
    flexWrap: FlexWrap
    justifyContent: JustifyContent
    alignItems: AlignItems
    alignContent: AlignContent
    flexGrow: float
    flexShrink: float
    flexBasis: int | str
    width: int | str | None
    height: int | str | None
    minWidth: int | None
    minHeight: int | None
    maxWidth: int | None
    maxHeight: int | None
    aspectRatio: float | None
    position: Position
    overflow: Overflow
    gap: int
    columnGap: int
    rowGap: int
    padding: int
    paddingX: int
    paddingY: int
    paddingTop: int
    paddingBottom: int
    paddingLeft: int
    paddingRight: int
    margin: int
    marginX: int
    marginY: int
    marginTop: int
    marginBottom: int
    marginLeft: int
    marginRight: int


# ── 尺寸解析工具函数 ──────────────────────────────────────

def _resolve_dimension(value: int | str | None, reference: int) -> int | None:
    """解析尺寸值，支持百分比。

    Args:
        value: 尺寸值。
            - int: 直接返回。
            - "50%": 返回 int(reference * 0.5)。
            - "auto" 或 None: 返回 None（自动尺寸）。
        reference: 百分比计算的参考尺寸。

    Returns:
        解析后的整数尺寸，或 None 表示自动尺寸。

    Raises:
        LayoutError: 百分比格式非法时抛出。
    """
    if value is None or value == "auto":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.endswith("%"):
            try:
                pct = float(s[:-1]) / 100.0
                return int(reference * pct)
            except (ValueError, TypeError):
                raise LayoutError(f"无效的百分比格式: {value!r}") from None
        # 尝试解析为整数
        try:
            return int(s)
        except (ValueError, TypeError):
            raise LayoutError(f"无法解析尺寸值: {value!r}") from None
    return None


def _resolve_padding(style: FlexStyle) -> tuple[int, int, int, int]:
    """解析容器四边内边距，处理简写属性级联。

    优先级：单边 > X/Y 简写 > 统一简写 > 默认 0。

    Returns:
        (padding_top, padding_bottom, padding_left, padding_right)
    """
    p = style.get("padding", 0)
    px = style.get("paddingX", p)
    py = style.get("paddingY", p)
    return (
        style.get("paddingTop", py),
        style.get("paddingBottom", py),
        style.get("paddingLeft", px),
        style.get("paddingRight", px),
    )


def _resolve_margin(style: FlexStyle) -> tuple[int, int, int, int]:
    """解析四边外边距，处理简写属性级联。

    优先级：单边 > X/Y 简写 > 统一简写 > 默认 0。

    Returns:
        (margin_top, margin_bottom, margin_left, margin_right)
    """
    m = style.get("margin", 0)
    mx = style.get("marginX", m)
    my = style.get("marginY", m)
    return (
        style.get("marginTop", my),
        style.get("marginBottom", my),
        style.get("marginLeft", mx),
        style.get("marginRight", mx),
    )


# ── 轴抽象辅助函数 ────────────────────────────────────────

def _is_row(direction: FlexDirection) -> bool:
    """判断主轴是否为水平方向。"""
    return direction in ("row", "row-reverse")


def _is_reverse(direction: FlexDirection) -> bool:
    """判断主轴方向是否反转。"""
    return direction in ("row-reverse", "column-reverse")


def _get_main_size(box: LayoutBox, direction: FlexDirection) -> int:
    """获取元素在主轴上尺寸。"""
    return box.width if _is_row(direction) else box.height


def _set_main_size(box: LayoutBox, direction: FlexDirection, value: int) -> None:
    """设置元素在主轴上尺寸。"""
    if _is_row(direction):
        box.width = value
    else:
        box.height = value


def _get_cross_size(box: LayoutBox, direction: FlexDirection) -> int:
    """获取元素在交叉轴上尺寸。"""
    return box.height if _is_row(direction) else box.width


def _set_cross_size(box: LayoutBox, direction: FlexDirection, value: int) -> None:
    """设置元素在交叉轴上尺寸。"""
    if _is_row(direction):
        box.height = value
    else:
        box.width = value


def _get_main_pos(box: LayoutBox, direction: FlexDirection) -> int:
    """获取元素在主轴上坐标。"""
    return box.x if _is_row(direction) else box.y


def _set_main_pos(box: LayoutBox, direction: FlexDirection, value: int) -> None:
    """设置元素在主轴上坐标。"""
    if _is_row(direction):
        box.x = value
    else:
        box.y = value


def _get_cross_pos(box: LayoutBox, direction: FlexDirection) -> int:
    """获取元素在交叉轴上坐标。"""
    return box.y if _is_row(direction) else box.x


def _set_cross_pos(box: LayoutBox, direction: FlexDirection, value: int) -> None:
    """设置元素在交叉轴上坐标。"""
    if _is_row(direction):
        box.y = value
    else:
        box.x = value


def _get_content_main(box: LayoutBox, direction: FlexDirection) -> int:
    """获取元素内容在主轴上尺寸。"""
    return box.content_width if _is_row(direction) else box.content_height


def _get_content_cross(box: LayoutBox, direction: FlexDirection) -> int:
    """获取元素内容在交叉轴上尺寸。"""
    return box.content_height if _is_row(direction) else box.content_width


def _clamp(value: int, min_val: int | None, max_val: int | None) -> int:
    """将值钳制到 [min_val, max_val] 范围内。

    Args:
        value: 原始值。
        min_val: 最小值（None 表示无下限）。
        max_val: 最大值（None 表示无上限）。

    Returns:
        钳制后的值。
    """
    if min_val is not None and value < min_val:
        return min_val
    if max_val is not None and value > max_val:
        return max_val
    return value


# ── FlexLayout 核心类 ─────────────────────────────────────

class FlexLayout:
    """纯 Python Flexbox 布局引擎。

    分两遍算法：
      1. measure：测量所有子元素的内容尺寸（自然宽高）— 调用方传入时已完成。
      2. layout：在容器约束下计算每个子元素的最终位置和尺寸。

    使用方式：
        layout = FlexLayout(container_width=80, container_height=24, style={...})
        result = layout.calculate(children)

    Attributes:
        container_width: 容器可用宽度（列数）。
        container_height: 容器可用高度（行数）。
        style: 容器 Flex 样式配置。
    """

    def __init__(
        self,
        container_width: int,
        container_height: int,
        style: FlexStyle | None = None,
    ) -> None:
        """初始化布局引擎。

        Args:
            container_width: 容器可用宽度（列数，≥0）。
            container_height: 容器可用高度（行数，≥0）。
            style: 容器样式配置。未提供时使用默认值。
        """
        self.container_width = container_width
        self.container_height = container_height
        self.style: FlexStyle = style if style is not None else FlexStyle()

    # ── 公共 API ─────────────────────────────────────────

    def calculate(self, children: list[LayoutBox]) -> list[LayoutBox]:
        """计算所有子元素的布局位置。

        核心算法流程：
          1. 解析容器样式（flexDirection / gap / padding / margin）
          2. 沿主轴分配空间（flexGrow / flexShrink / flexBasis）
          3. 沿主轴排列（justifyContent）
          4. 沿交叉轴排列（alignItems）
          5. 处理 flexWrap 换行
          6. 处理 alignContent

        Args:
            children: 子元素的 LayoutBox 列表（content_width/height 已填充）。

        Returns:
            更新后的 LayoutBox 列表（x/y/width/height 已填充）。
            返回的是新列表，不修改传入的 children 原对象。
        """
        if not children:
            return []

        # 深拷贝子元素，避免修改原对象
        result = [copy.deepcopy(c) for c in children]

        # 解析容器样式
        direction: FlexDirection = self.style.get("flexDirection", "row")
        wrap: FlexWrap = self.style.get("flexWrap", "nowrap")
        justify: JustifyContent = self.style.get("justifyContent", "flex-start")
        align: AlignItems = self.style.get("alignItems", "stretch")
        align_content: AlignContent = self.style.get("alignContent", "stretch")

        # 解析 padding
        pt, pb, pl, pr = _resolve_padding(self.style)
        content_w = max(0, self.container_width - pl - pr)
        content_h = max(0, self.container_height - pt - pb)

        is_row = _is_row(direction)
        reverse = _is_reverse(direction)
        wrap_reverse = (wrap == "wrap-reverse")

        main_content_size = content_w if is_row else content_h
        cross_content_size = content_h if is_row else content_w

        # 解析 gap
        gap = self.style.get("gap", 0)
        row_gap = self.style.get("rowGap", gap)
        col_gap = self.style.get("columnGap", gap)
        main_gap = col_gap if is_row else row_gap
        cross_gap = row_gap if is_row else col_gap

        # 解析每个子元素的 margin（当前版本：统一使用容器 margin 配置）
        mt, mb, ml, mr = _resolve_margin(self.style)
        main_margin_leading = ml if is_row else mt
        main_margin_trailing = mr if is_row else mb
        cross_margin_leading = mt if is_row else ml
        cross_margin_trailing = mb if is_row else mr

        # flex 属性（当前版本：所有子元素统一）
        flex_grow = self.style.get("flexGrow", 0.0)
        flex_shrink = self.style.get("flexShrink", 1.0)
        flex_basis_raw = self.style.get("flexBasis", None)

        # 子元素尺寸约束（当前版本：所有子元素统一）
        min_w = self.style.get("minWidth")
        min_h = self.style.get("minHeight")
        max_w = self.style.get("maxWidth")
        max_h = self.style.get("maxHeight")
        width_raw = self.style.get("width")
        height_raw = self.style.get("height")

        # ── 步骤 1：将子元素分组为 flex 行 ──────────────
        lines = self._group_into_lines(
            result, main_content_size, main_gap,
            main_margin_leading, main_margin_trailing,
            flex_basis_raw, flex_grow, flex_shrink,
            min_w, min_h, max_w, max_h,
            width_raw, height_raw,
            direction, wrap,
        )

        # ── 步骤 2：对每行执行主轴/交叉轴计算 ──────────
        line_cross_sizes: list[int] = []

        for line in lines:
            # 2a. 确定每个子元素的 flex basis（主轴初始尺寸）
            for box in line:
                self._resolve_flex_basis(
                    box, direction, flex_basis_raw,
                    main_content_size, content_w,
                    width_raw, height_raw,
                )

            # 2b. 计算该行总已用空间
            total_used = sum(_get_main_size(b, direction) for b in line)
            margin_total = main_margin_leading + main_margin_trailing
            total_used += margin_total * len(line)
            total_used += max(0, len(line) - 1) * main_gap

            # 2c. 分配剩余空间（flexGrow）或收缩（flexShrink）
            remaining = main_content_size - total_used
            if remaining > 0 and flex_grow > 0:
                # 支持不同 flexGrow 值：所有子元素共享容器 flexGrow
                total_grow = flex_grow * len(line)
                if total_grow > 0:
                    grow_per_unit = remaining / total_grow
                    for box in line:
                        grow_share = int(flex_grow * grow_per_unit)
                        current_main = _get_main_size(box, direction)
                        _set_main_size(box, direction, current_main + grow_share)
                        remaining -= grow_share
            elif remaining < 0 and flex_shrink > 0:
                # 按 flexShrink * flexBasis 比例收缩
                total_shrink_weight = 0.0
                shrink_weights: list[float] = []
                for box in line:
                    basis = _get_main_size(box, direction)
                    weight = flex_shrink * basis
                    shrink_weights.append(weight)
                    total_shrink_weight += weight
                if total_shrink_weight > 0:
                    deficit = abs(remaining)
                    for i, box in enumerate(line):
                        shrink_share = int(deficit * shrink_weights[i] / total_shrink_weight)
                        current_main = _get_main_size(box, direction)
                        new_main = max(0, current_main - shrink_share)
                        _set_main_size(box, direction, new_main)

            # 2d. 应用主轴尺寸约束（min/max）
            for box in line:
                if is_row:
                    min_main = min_w
                    max_main = max_w
                else:
                    min_main = min_h
                    max_main = max_h
                current = _get_main_size(box, direction)
                _set_main_size(box, direction, _clamp(current, min_main, max_main))

            # 2e. 计算交叉轴尺寸
            for box in line:
                cross_size = _get_cross_size(box, direction)
                if cross_size == 0:
                    # 未设置 → 使用内容尺寸
                    cross_size = _get_content_cross(box, direction)
                if align == "stretch":
                    # stretch 暂不在此处处理，在定位时统一拉伸
                    pass
                if is_row:
                    cross_size = _clamp(cross_size, min_h, max_h)
                else:
                    cross_size = _clamp(cross_size, min_w, max_w)
                _set_cross_size(box, direction, cross_size)

            # 该行的交叉轴尺寸 = 最大子元素交叉轴尺寸
            line_cross = max(
                (_get_cross_size(b, direction) + cross_margin_leading + cross_margin_trailing
                 for b in line),
                default=0,
            )
            line_cross_sizes.append(line_cross)

            # 2f. 沿主轴排列（justifyContent）
            self._apply_justify_content(
                line, direction, reverse, main_content_size,
                main_gap, main_margin_leading, main_margin_trailing,
                justify,
            )

            # 2g. 沿交叉轴排列（alignItems）— 暂存交叉轴起始偏移，在 alignContent 阶段统一处理
            for box in line:
                cross_size = _get_cross_size(box, direction)
                line_cross_space = line_cross
                if align == "stretch":
                    cross_size = line_cross_space - cross_margin_leading - cross_margin_trailing
                    if is_row:
                        cross_size = _clamp(cross_size, min_h, max_h)
                    else:
                        cross_size = _clamp(cross_size, min_w, max_w)
                    _set_cross_size(box, direction, cross_size)
                # 交叉轴位置稍后统一设置

        # ── 步骤 3：多行交叉轴排版（alignContent） ──────
        self._apply_align_content(
            lines, line_cross_sizes, direction, align_content,
            align, cross_content_size, cross_gap,
            cross_margin_leading, cross_margin_trailing,
            wrap_reverse, is_row,
            min_w, min_h, max_w, max_h,
        )

        # ── 步骤 4：应用容器 padding 偏移 ──────────────
        for box in result:
            box.x += pl
            box.y += pt

        return result

    # ── 私有方法 ─────────────────────────────────────────

    def _group_into_lines(
        self,
        children: list[LayoutBox],
        main_content_size: int,
        main_gap: int,
        main_margin_leading: int,
        main_margin_trailing: int,
        flex_basis_raw: int | str | None,
        flex_grow: float,
        flex_shrink: float,
        min_w: int | None,
        min_h: int | None,
        max_w: int | None,
        max_h: int | None,
        width_raw: int | str | None,
        height_raw: int | str | None,
        direction: FlexDirection,
        wrap: FlexWrap,
    ) -> list[list[LayoutBox]]:
        """将子元素分组为 flex 行。

        根据 flexWrap 模式决定是否换行。nowrap 时所有子元素放在同一行。
        wrap 时，当累计主轴尺寸超过容器主轴尺寸时换行。

        Returns:
            二维列表，每个内层列表代表一个 flex 行。
        """
        if wrap == "nowrap":
            return [children]

        lines: list[list[LayoutBox]] = []
        current_line: list[LayoutBox] = []
        current_used = 0

        for box in children:
            # 计算该子元素在主轴上的预估尺寸
            main_size = self._estimate_main_size(
                box, direction, flex_basis_raw, main_content_size,
                width_raw, height_raw,
            )
            margin_total = main_margin_leading + main_margin_trailing
            item_total = main_size + margin_total

            # 首个元素不需要间隙
            gap_needed = main_gap if current_line else 0

            if current_line and current_used + gap_needed + item_total > main_content_size:
                # 需要换行
                lines.append(current_line)
                current_line = [box]
                current_used = item_total
            else:
                current_line.append(box)
                current_used += gap_needed + item_total

        if current_line:
            lines.append(current_line)

        # wrap-reverse 的行顺序反转在 _apply_align_content 中统一处理，
        # 此处保持自然顺序，避免双重反转。

        return lines

    def _estimate_main_size(
        self,
        box: LayoutBox,
        direction: FlexDirection,
        flex_basis_raw: int | str | None,
        main_content_size: int,
        width_raw: int | str | None,
        height_raw: int | str | None,
    ) -> int:
        """预估子元素在主轴上的尺寸。

        用于换行判断，不修改 box。
        """
        is_row = _is_row(direction)
        content_main = _get_content_main(box, direction)

        # 优先使用 flexBasis
        if flex_basis_raw is not None:
            resolved = _resolve_dimension(flex_basis_raw, main_content_size)
            if resolved is not None:
                return resolved

        # 其次使用 width/height
        dim_raw = width_raw if is_row else height_raw
        if dim_raw is not None:
            resolved = _resolve_dimension(dim_raw, main_content_size if is_row else self.container_height)
            if resolved is not None:
                return resolved

        # 回退到内容尺寸
        return max(content_main, 1)

    def _resolve_flex_basis(
        self,
        box: LayoutBox,
        direction: FlexDirection,
        flex_basis_raw: int | str | None,
        main_content_size: int,
        content_w: int,
        width_raw: int | str | None,
        height_raw: int | str | None,
    ) -> None:
        """解析子元素的 flex basis（主轴初始尺寸），设置到 box 上。"""
        is_row = _is_row(direction)
        content_main = _get_content_main(box, direction)

        # 优先级：flexBasis > width/height > content_size
        basis = content_main

        dim_raw = width_raw if is_row else height_raw
        if dim_raw is not None:
            resolved = _resolve_dimension(dim_raw, main_content_size if is_row else self.container_height)
            if resolved is not None:
                basis = resolved

        if flex_basis_raw is not None:
            resolved = _resolve_dimension(flex_basis_raw, main_content_size)
            if resolved is not None:
                basis = resolved

        _set_main_size(box, direction, max(basis, 0))

    def _apply_justify_content(
        self,
        line: list[LayoutBox],
        direction: FlexDirection,
        reverse: bool,
        main_content_size: int,
        main_gap: int,
        main_margin_leading: int,
        main_margin_trailing: int,
        justify: JustifyContent,
    ) -> None:
        """沿主轴排列子元素。

        根据 justifyContent 值计算每个子元素的主轴坐标。
        """
        if not line:
            return

        n = len(line)
        margin_per = main_margin_leading + main_margin_trailing
        total_children_main = sum(_get_main_size(b, direction) + margin_per for b in line)
        total_used = total_children_main + max(0, n - 1) * main_gap
        remaining = main_content_size - total_used

        if justify == "flex-start":
            gap_between = main_gap
            start_offset = 0
        elif justify == "flex-end":
            gap_between = main_gap
            start_offset = remaining
        elif justify == "center":
            gap_between = main_gap
            start_offset = remaining // 2
        elif justify == "space-between":
            if n > 1:
                gap_between = main_gap + remaining // (n - 1)
            else:
                gap_between = main_gap
            start_offset = 0
        elif justify == "space-around":
            space_per = remaining // n if n > 0 else 0
            start_offset = space_per // 2
            # 每个子元素两侧各 space_per/2，间隙 = space_per + main_gap
            gap_between = space_per + main_gap
        elif justify == "space-evenly":
            space_per = remaining // (n + 1) if n > 0 else 0
            start_offset = space_per
            gap_between = space_per + main_gap
        else:
            gap_between = main_gap
            start_offset = 0

        # 计算每个子元素位置（按自然顺序）
        positions: list[int] = []
        cursor = start_offset
        for i, box in enumerate(line):
            positions.append(cursor + main_margin_leading)
            child_main = _get_main_size(box, direction) + margin_per
            cursor += child_main
            if i < n - 1:
                cursor += gap_between

        # 如果主轴方向反转，将位置镜像到容器另一端
        if reverse:
            for i, box in enumerate(line):
                child_main = _get_main_size(box, direction)
                positions[i] = main_content_size - positions[i] - child_main

        # 设置位置（保持原始子元素顺序）
        for box, pos in zip(line, positions):
            _set_main_pos(box, direction, pos)

    def _apply_align_content(
        self,
        lines: list[list[LayoutBox]],
        line_cross_sizes: list[int],
        direction: FlexDirection,
        align_content: AlignContent,
        align: AlignItems,
        cross_content_size: int,
        cross_gap: int,
        cross_margin_leading: int,
        cross_margin_trailing: int,
        wrap_reverse: bool,
        is_row: bool,
        min_w: int | None,
        min_h: int | None,
        max_w: int | None,
        max_h: int | None,
    ) -> None:
        """处理多行在交叉轴上的排版。

        包含 alignContent（行间分布）和 alignItems（行内对齐）。
        """
        if not lines:
            return

        n = len(lines)
        total_cross = sum(line_cross_sizes)
        total_gaps = max(0, n - 1) * cross_gap
        total_used = total_cross + total_gaps
        remaining = cross_content_size - total_used

        # 计算每行交叉轴起始偏移
        if align_content == "flex-start":
            gap = cross_gap
            start_offset = 0
        elif align_content == "flex-end":
            gap = cross_gap
            start_offset = remaining
        elif align_content == "center":
            gap = cross_gap
            start_offset = remaining // 2
        elif align_content == "stretch":
            # 如果只有一行，stretch 行为同 flex-start
            if n == 1:
                gap = cross_gap
                line_cross_sizes[0] = max(line_cross_sizes[0], cross_content_size)
                start_offset = 0
            else:
                # 多行时：拉伸所有行平分剩余空间
                gap = cross_gap
                stretch_extra = remaining // n if n > 0 else 0
                for i in range(n):
                    line_cross_sizes[i] += stretch_extra
                start_offset = 0
        elif align_content == "space-between":
            if n > 1:
                gap = cross_gap + remaining // (n - 1)
            else:
                gap = cross_gap
            start_offset = 0
        elif align_content == "space-around":
            space_per = remaining // n if n > 0 else 0
            start_offset = space_per // 2
            gap = space_per + cross_gap
        elif align_content == "space-evenly":
            space_per = remaining // (n + 1) if n > 0 else 0
            start_offset = space_per
            gap = space_per + cross_gap
        else:
            gap = cross_gap
            start_offset = 0

        # 如果 wrap-reverse，反转行顺序
        line_order = list(range(n))
        if wrap_reverse:
            line_order = list(reversed(line_order))

        # 为每行设置交叉轴位置，并在行内应用 alignItems
        cursor = start_offset
        for idx in line_order:
            line = lines[idx]
            line_cross = line_cross_sizes[idx]

            for box in line:
                child_cross = _get_cross_size(box, direction)

                # alignItems 对齐
                if align == "flex-start":
                    cross_pos = cursor + cross_margin_leading
                elif align == "flex-end":
                    cross_pos = cursor + line_cross - cross_margin_trailing - child_cross
                elif align == "center":
                    available = line_cross - cross_margin_leading - cross_margin_trailing
                    cross_pos = cursor + cross_margin_leading + (available - child_cross) // 2
                elif align == "stretch":
                    # 拉伸到行高（减去 margin）
                    stretched = line_cross - cross_margin_leading - cross_margin_trailing
                    if is_row:
                        stretched = _clamp(stretched, min_h, max_h)
                    else:
                        stretched = _clamp(stretched, min_w, max_w)
                    _set_cross_size(box, direction, max(stretched, 0))
                    cross_pos = cursor + cross_margin_leading
                elif align == "baseline":
                    # 简化实现：baseline 视为 flex-start
                    cross_pos = cursor + cross_margin_leading
                else:
                    cross_pos = cursor

                _set_cross_pos(box, direction, cross_pos)

            cursor += line_cross + gap
