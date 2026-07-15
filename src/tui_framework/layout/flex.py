"""Flex 弹性布局容器。

智能路由层 — 根据 ``direction`` 参数委托 VBox（column）或 HBox（row）。
支持子元素 flex_weight 权重分配和 wrap 换行。
"""

from __future__ import annotations

from typing import List, Optional

from .container import LayoutContainer
from ..core.ansi_utils import visual_width
from ..widgets.base import Widget
from .vbox import VBox
from .hbox import HBox, HAlign


class FlexDirection:
    """弹性布局方向常量。"""
    ROW: str = "row"
    """水平排列（委托 HBox）。"""
    COLUMN: str = "column"
    """垂直排列（委托 VBox）。"""


# ── Flex 包装器 ──────────────────────────────────────────

class FlexChild:
    """弹性子元素包装器。

    在将子元素添加到 Flex 容器时自动包装，附加 flex_weight 属性。

    Args:
        widget: 被包装的控件实例。
        flex_weight: 弹性权重（剩余空间分配比例），默认 1。
    """

    __slots__ = ("widget", "flex_weight")

    def __init__(self, widget: Widget, flex_weight: float = 1.0) -> None:
        self.widget: Widget = widget
        self.flex_weight: float = max(0.0, flex_weight)

    @property
    def visible(self) -> bool:
        """代理到 widget.visible。"""
        return self.widget.visible


class Flex(LayoutContainer):
    """弹性布局容器 — 智能路由层。

    Flex 根据 ``direction`` 参数将布局委托给 VBox（column）或 HBox（row）。
    子元素通过 ``flex_weight`` 声明弹性权重，剩余空间按权重等比分配。

    ## 方向

    - ``"row"``（默认）: 水平弹性布局，委托 HBox。
    - ``"column"``: 垂直弹性布局，委托 VBox。

    ## flex_weight

    每个子元素可通过 ``add_child(widget, flex_weight=...)`` 指定权重。
    默认权重为 1.0。权重为 0 表示不参与弹性分配（固定尺寸）。

    剩余空间分配公式：
        ``子元素额外空间 = 剩余空间 × (子元素权重 / 总权重)``

    ## wrap 换行

    当 ``wrap=True`` 且方向为 ``"row"`` 时，若子元素总宽度超出可用宽度，
    自动换行（类似 CSS flex-wrap: wrap）。换行后每行独立计算高度。

    ## 使用示例

    ```python
    flex = Flex(direction="row")
    flex.add_child(button1, flex_weight=2)  # 占 2/3 剩余空间
    flex.add_child(button2, flex_weight=1)  # 占 1/3 剩余空间
    print(flex.render())
    ```
    """

    def __init__(
        self,
        direction: str = FlexDirection.ROW,
        spacing: int = 0,
        padding: tuple[int, int, int, int] = (0, 0, 0, 0),
        wrap: bool = False,
        align: str = HAlign.TOP,
        max_width: int = 80,
    ) -> None:
        super().__init__(spacing=spacing, padding=padding)
        self._direction: str = direction
        if self._direction not in (FlexDirection.ROW, FlexDirection.COLUMN):
            raise ValueError(
                f"无效的 direction: {self._direction!r}，可选: row/column"
            )
        self._wrap: bool = wrap
        self._align: str = align
        self._max_width: int = max_width
        # 弹性子元素包装器列表
        self._flex_children: List[FlexChild] = []

    # ── 属性 ─────────────────────────────────────────

    @property
    def direction(self) -> str:
        """布局方向。"""
        return self._direction

    @direction.setter
    def direction(self, value: str) -> None:
        if value not in (FlexDirection.ROW, FlexDirection.COLUMN):
            raise ValueError(f"无效的 direction: {value!r}，可选: row/column")
        self._direction = value

    @property
    def wrap(self) -> bool:
        """是否自动换行。"""
        return self._wrap

    @wrap.setter
    def wrap(self, value: bool) -> None:
        self._wrap = value

    @property
    def align(self) -> str:
        """垂直对齐模式（仅 row 方向生效）。"""
        return self._align

    @align.setter
    def align(self, value: str) -> None:
        self._align = value

    @property
    def max_width(self) -> int:
        """渲染最大宽度。"""
        return self._max_width

    @max_width.setter
    def max_width(self, value: int) -> None:
        self._max_width = value

    # ── 子元素管理 ───────────────────────────────────

    def add_child(
        self,
        widget: Widget,
        flex_weight: float = 1.0,
    ) -> None:
        """添加弹性子元素。

        Args:
            widget: 要添加的控件实例。
            flex_weight: 弹性权重（默认 1.0），0 表示固定尺寸。

        Raises:
            TypeError: widget 不是 Widget 实例。
            RecursionError: 嵌套深度超限。
        """
        super().add_child(widget)
        self._flex_children.append(FlexChild(widget, flex_weight))

    def remove_child(self, widget: Widget) -> None:
        """移除子元素（同时移除弹性包装器）。

        Args:
            widget: 要移除的控件实例。
        """
        super().remove_child(widget)
        self._flex_children = [
            fc for fc in self._flex_children if fc.widget is not widget
        ]

    def clear_children(self) -> None:
        """清空所有子元素。"""
        super().clear_children()
        self._flex_children.clear()

    # ── 尺寸计算 ─────────────────────────────────────

    def get_content_width(self, max_width: int = 80) -> int:
        """委托给对应方向的布局容器计算宽度。"""
        delegate = self._build_delegate()
        return delegate.get_content_width(max_width)

    def get_content_height(self, max_height: int = 24) -> int:
        """委托给对应方向的布局容器计算高度。"""
        delegate = self._build_delegate()
        return delegate.get_content_height(max_height)

    # ── 渲染 ─────────────────────────────────────────

    def render(self) -> str:
        """委托给对应方向的布局容器渲染。

        - ``direction="row"`` → 委托 HBox（支持 wrap 时循环构建多行 HBox）。
        - ``direction="column"`` → 委托 VBox。

        Returns:
            ANSI 渲染文本。
        """
        if self._direction == FlexDirection.COLUMN:
            return self._render_column()
        else:
            return self._render_row()

    # ── 内部方法 ─────────────────────────────────────

    def _build_delegate(self) -> LayoutContainer:
        """构建委托布局容器实例。"""
        if self._direction == FlexDirection.COLUMN:
            delegate = VBox(spacing=self._spacing)
        else:
            delegate = HBox(spacing=self._spacing, align=self._align)
        # 复制子元素到委托容器
        for fc in self._flex_children:
            if fc.widget.visible:
                delegate._children.append(fc.widget)
        return delegate

    def _render_column(self) -> str:
        """垂直弹性布局 — 委托 VBox。"""
        delegate = self._build_delegate()
        return delegate.render()

    def _render_row(self) -> str:
        """水平弹性布局 — 委托 HBox。

        若 wrap=True，子元素超出 max_width 时自动换行。
        """
        if not self._wrap:
            delegate = self._build_delegate()
            return delegate.render()

        # wrap 模式：按宽度分组换行
        return self._render_wrapped()

    def _render_wrapped(self) -> str:
        """wrap 模式渲染 — 子元素按宽度分组，每组一行 HBox。"""
        visible_fcs = [fc for fc in self._flex_children if fc.widget.visible]
        if not visible_fcs:
            return ""

        # 计算每个子元素的宽度
        child_widths: List[int] = []
        for fc in visible_fcs:
            w = fc.widget
            if isinstance(w, LayoutContainer):
                cw = w.get_content_width(self._max_width)
            else:
                rendered = w.render()
                cw = max((visual_width(line) for line in rendered.split('\n')), default=0) if rendered else 0
            child_widths.append(cw)

        # 分组
        rows: List[List[FlexChild]] = []
        current_row: List[FlexChild] = []
        current_width = 0

        for i, fc in enumerate(visible_fcs):
            w = child_widths[i]
            spacing_needed = self._spacing if current_row else 0
            if current_row and current_width + spacing_needed + w > self._max_width:
                rows.append(current_row)
                current_row = [fc]
                current_width = w
            else:
                current_row.append(fc)
                current_width += spacing_needed + w

        if current_row:
            rows.append(current_row)

        # 逐行渲染
        result_lines: List[str] = []
        for row_idx, row in enumerate(rows):
            if row_idx > 0:
                result_lines.append("")  # 行间空行
            hbox = HBox(spacing=self._spacing, align=self._align)
            for fc in row:
                hbox._children.append(fc.widget)
            rendered = hbox.render()
            if rendered:
                result_lines.extend(rendered.split('\n'))

        # 应用内边距
        result_lines = self._apply_padding(result_lines)
        return '\n'.join(result_lines)
