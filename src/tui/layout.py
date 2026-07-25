"""布局控件 — Vertical/Horizontal/Padding/Border。

提供声明式布局控件体系，基于 Widget 基类和 RenderBuffer 实现。
所有布局控件均可嵌套组合，构建复杂界面。

2026-07-17 重构：从工厂函数闭包模式（``__new__`` 动态匿名子类）
改为真正的 Widget 子类，解决 isinstance 失效、类型注解困难、
__repr__ 异常等问题。调用方式完全向后兼容。

设计模式：
  - 组合 (Composite): 布局控件递归组合子控件的渲染结果
  - 策略 (Strategy): 对齐方式（left/center/right, top/center/bottom）
  - 装饰器 (Decorator): Padding/Border 装饰单个子控件

使用示例:
    from src.tui.widget_base import Widget
    from src.tui.layout import Vertical, Horizontal, Padding, Border
    from src.tui.render_buffer import RenderBuffer

    class Label(Widget):
        def __init__(self, text: str, **kwargs):
            super().__init__(**kwargs)
            self._text = text
        def render(self, buffer):
            buffer.write(0, 0, self._text)

    # 垂直排列
    v = Vertical([Label("A"), Label("B")], props={"spacing": 1})
    buf = RenderBuffer(10, 5)
    v.mount()
    v.render(buf)
    print(buf.render())
"""

from __future__ import annotations

import logging

from .widget_base import Widget
from .render_buffer import RenderBuffer
from .core.text_utils import build_title_border

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "Vertical",
    "Horizontal",
    "Padding",
    "Border",
    "Grid",
    "Center",
]


# ──────────────────────────────────────────────────────────
# 工具函数 — 对齐逻辑提取（避免三个布局类重复实现）
# ──────────────────────────────────────────────────────────


def _align_horizontal(
    buffer: RenderBuffer,
    x: int,
    y: int,
    child_str: str,
    align: str,
    width: int,
) -> None:
    """水平对齐写入 — 将 child_str 按对齐方式写入 buffer 的指定位置。

    Args:
        buffer: 目标 RenderBuffer。
        x: 起始 x 坐标。
        y: 起始 y 坐标。
        child_str: 要写入的字符串（可能含换行）。
        align: "left"/"center"/"right"。
        width: 对齐参考宽度。
    """
    if not child_str:
        return
    lines = child_str.split("\n")
    for i, line in enumerate(lines):
        row = y + i
        stripped = line.rstrip()
        if not stripped:
            continue
        if align == "center":
            offset = max(0, (width - len(stripped)) // 2)
        elif align == "right":
            offset = max(0, width - len(stripped))
        else:  # left
            offset = 0
        buffer.write(x + offset, row, stripped)


def _align_vertical(child_h: int, container_h: int, valign: str) -> int:
    """计算垂直偏移量。

    Args:
        child_h: 子控件高度。
        container_h: 容器高度。
        valign: "top"/"center"/"bottom"。

    Returns:
        垂直偏移量（y 方向偏移）。
    """
    if valign == "center":
        return max(0, (container_h - child_h) // 2)
    elif valign == "bottom":
        return max(0, container_h - child_h)
    else:  # top
        return 0


# ═══════════════════════════════════════════════════════════
# LayoutWidget — 布局控件基类
# ═══════════════════════════════════════════════════════════


class LayoutWidget(Widget):
    """布局控件基类。

    封装子控件的存储和标记，消除 6 个布局类中重复的
    _children_source 赋值和 _renders_children 标记。

    Args:
        children: 子控件列表（或 Grid 的二维列表）。
        renders_children: 是否自行管理子控件渲染，默认 True。
        **kwargs: 传递给 Widget.__init__ 的 props/key 等参数。
    """

    def __init__(
        self,
        children: list[Widget] | list[list[Widget]],
        renders_children: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._children_source = children
        self._renders_children = renders_children


# ═══════════════════════════════════════════════════════════
# Vertical — 垂直布局
# ═══════════════════════════════════════════════════════════


class Vertical(LayoutWidget):
    """垂直布局控件。

    将多个子控件从上到下垂直排列。
    支持间距（spacing）和水平对齐（align）。

    Args:
        children: 子控件列表。
        spacing: 子控件之间的间距（行数），默认 0。
        align: 水平对齐方式，"left" / "center" / "right"，默认 "left"。
    """

    def __init__(
        self,
        children: list,
        spacing: int = 0,
        align: str = "left",
        max_height: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(list(children), renders_children=True, props={
            "spacing": spacing,
            "align": align,
            "max_height": max_height,
        }, key=key)

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """垂直排列渲染所有子控件。

        每个子控件先渲染到临时缓冲区，再按垂直位置合并到父缓冲区。
        支持 left/center/right 水平对齐。
        支持通过 max_height 限制最大高度（超出截断）。
        """
        sp = self._props.get("spacing", 0)
        al = self._props.get("align", "left")
        max_height = self._props.get("max_height")
        children = self._children if self._children else self._children_source
        # 有效高度受 buffer.height 和 max_height 共同约束
        effective_h = buffer.height
        if max_height is not None:
            effective_h = min(effective_h, max_height)
        y = 0
        for child in children:
            if y >= effective_h:
                break
            # 创建临时缓冲区渲染子控件
            tmp = RenderBuffer(buffer.width, effective_h - y)
            Widget._render_child_safe(child, tmp, "Vertical")
            # 获取渲染内容
            child_str = tmp.render()
            child_lines = child_str.split("\n") if child_str else [""]
            child_h = max(1, len(child_lines))
            # 确保不超出父缓冲区
            child_h = min(child_h, effective_h - y)
            # 将渲染内容写入父缓冲区（支持对齐）
            for i, line in enumerate(child_lines):
                row = y + i
                if row >= effective_h or i >= child_h:
                    break
                stripped = line.rstrip()
                if not stripped:
                    continue
                # 内联对齐计算（单行，无需经过 _align_horizontal 的多行拆分）
                if al == "center":
                    x = max(0, (buffer.width - len(stripped)) // 2)
                elif al == "right":
                    x = max(0, buffer.width - len(stripped))
                else:
                    x = 0
                buffer.write(x, row, stripped)
            y += child_h + sp

    def __repr__(self) -> str:
        children_len = len(self._children) if self._children else len(self._children_source)
        return f"Vertical({children_len} children)"


# ═══════════════════════════════════════════════════════════
# Horizontal — 水平布局
# ═══════════════════════════════════════════════════════════


class Horizontal(LayoutWidget):
    """水平布局控件。

    将多个子控件从左到右水平排列。
    支持间距（spacing）和垂直对齐（align）。

    Args:
        children: 子控件列表。
        spacing: 子控件之间的间距（列数），默认 1。
        align: 垂直对齐方式，"top" / "center" / "bottom"，默认 "top"。
    """

    def __init__(
        self,
        children: list,
        spacing: int = 1,
        align: str = "top",
        max_width: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(list(children), renders_children=True, props={
            "spacing": spacing,
            "align": align,
            "max_width": max_width,
        }, key=key)

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """水平排列渲染所有子控件。

        每个子控件先渲染到临时缓冲区，再按水平位置合并到父缓冲区。
        支持 top/center/bottom 垂直对齐。
        支持通过 max_width 限制最大宽度（超出截断）。
        """
        sp = self._props.get("spacing", 1)
        al = self._props.get("align", "top")
        max_width = self._props.get("max_width")
        children = self._children if self._children else self._children_source
        # 有效宽度受 buffer.width 和 max_width 共同约束
        effective_w = buffer.width
        if max_width is not None:
            effective_w = min(effective_w, max_width)
        x = 0
        max_h = buffer.height
        for child in children:
            if x >= effective_w:
                break
            # 为子控件创建临时缓冲区
            child_buf = RenderBuffer(effective_w - x, max_h)
            Widget._render_child_safe(child, child_buf, "Horizontal")
            child_str = child_buf.render()
            child_lines = child_str.split("\n") if child_str else [""]
            child_w = max((len(l) for l in child_lines), default=1)
            child_h = len(child_lines)
            # 计算垂直偏移
            y_offset = _align_vertical(child_h, max_h, al)
            # 合并到父缓冲区
            for i, line in enumerate(child_lines):
                dst_y = y_offset + i
                if 0 <= dst_y < buffer.height:
                    buffer.write(x, dst_y, line)
            x += child_w + sp

    def __repr__(self) -> str:
        children_len = len(self._children) if self._children else len(self._children_source)
        return f"Horizontal({children_len} children)"


# ═══════════════════════════════════════════════════════════
# Padding — 内边距布局
# ═══════════════════════════════════════════════════════════


class Padding(LayoutWidget):
    """内边距控件。

    在子控件周围添加空白边距。

    Args:
        child: 子控件。
        left: 左边距（列数），默认 1。
        right: 右边距（列数），默认 1。
        top: 上边距（行数），默认 0。
        bottom: 下边距（行数），默认 0。
        padding: 统一边距（四边相等），优先级低于独立参数。
    """

    def __init__(
        self,
        child: Widget,
        left: int | None = None,
        right: int | None = None,
        top: int | None = None,
        bottom: int | None = None,
        padding: int = 1,
        key: str | None = None,
    ) -> None:
        # 确定各方向边距
        p_left = left if left is not None else padding
        p_right = right if right is not None else padding
        p_top = top if top is not None else 0
        p_bottom = bottom if bottom is not None else 0
        super().__init__([child], renders_children=True, props={
            "left": p_left, "right": p_right,
            "top": p_top, "bottom": p_bottom,
        }, key=key)

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表（始终为单元素列表）。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """在子控件周围添加空白边距。

        子控件渲染到内部区域（扣除边距后的区域），
        通过临时缓冲区合并。
        """
        pl = self._props.get("left", 1)
        pr = self._props.get("right", 1)
        pt = self._props.get("top", 0)
        pb = self._props.get("bottom", 0)
        inner_w = max(0, buffer.width - pl - pr)
        inner_h = max(0, buffer.height - pt - pb)
        if inner_w <= 0 or inner_h <= 0:
            return
        child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
        if child is not None:
            # 渲染到临时缓冲区，然后写入父缓冲区正确位置
            tmp = RenderBuffer(inner_w, inner_h)
            Widget._render_child_safe(child, tmp, "Padding")
            # 合并回父缓冲区
            buffer.merge(tmp, pl, pt)

    def __repr__(self) -> str:
        p = self._props
        return (
            f"Padding(l={p['left']} r={p['right']} "
            f"t={p['top']} b={p['bottom']})"
        )


# ═══════════════════════════════════════════════════════════
# Border — 边框布局
# ═══════════════════════════════════════════════════════════


class Border(LayoutWidget):
    """边框控件。

    在子控件周围绘制边框。
    复用 ``components/_box.py`` 的 ``BoxStyle`` 枚举和 ``_BOX_CHARS`` 字符集。
    窄屏时自动降级为无边框。

    Args:
        child: 子控件。
        style: 边框样式（BoxStyle 枚举值或字符串），默认 "rounded"。
        title: 标题文本（可选），默认 ""。
        title_align: 标题对齐方式，"left" / "center" / "right"，默认 "left"。
        border_color: 边框色号（256 色），默认 None（使用默认色）。
    """

    def __init__(
        self,
        child: Widget,
        style: str = "rounded",
        title: str = "",
        title_align: str = "left",
        border_color: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__([child], renders_children=True, props={
            "style": style,
            "title": title,
            "title_align": title_align,
            "border_color": border_color,
        }, key=key)

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表（始终为单元素列表）。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """在子控件周围绘制边框。

        边框占用 1 行上、1 行下、1 列左、1 列右。
        窄屏时自动降级为无边框（直接渲染子控件）。
        """
        if buffer.is_empty():
            return
        style = self._props.get("style", "rounded")
        title = self._props.get("title", "")
        title_align = self._props.get("title_align", "left")
        bc = self._props.get("border_color")

        # 窄屏降级：无边框，直接渲染子控件（Widget 体系，非 TuiComponent）
        if self._is_narrow():
            self._render_narrow(buffer)
            return

        # 获取边框字符集
        chars = self._get_box_chars(style)
        if chars is None:
            child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
            if child is not None:
                Widget._render_child_safe(child, buffer, "Border(chars_none)")
            return

        # 边框占用 1 行上、1 行下、1 列左、1 列右
        inner_w = max(0, buffer.width - 2)
        inner_h = max(0, buffer.height - 2)
        if inner_w <= 0 or inner_h <= 0:
            return

        # 构建 ANSI 颜色前缀/后缀
        pre = f"\033[38;5;{bc}m" if bc is not None else ""
        suf = "\033[0m" if bc is not None else ""

        tl, tr, bl, br = chars["tl"], chars["tr"], chars["bl"], chars["br"]
        h, v = chars["h"], chars["v"]

        # 顶部边框（含标题）
        top_line = self._build_top_border(
            tl, tr, h, buffer.width, title, title_align, pre, suf,
        )
        buffer.write(0, 0, top_line)

        # 底部边框
        bottom = f"{pre}{bl}{h * (buffer.width - 2)}{br}{suf}"
        buffer.write(0, buffer.height - 1, bottom)

        # 左右边框
        for row in range(inner_h):
            buf_row = row + 1
            if buf_row < buffer.height:
                buffer.write(0, buf_row, f"{pre}{v}{suf}")
                buffer.write(buffer.width - 1, buf_row, f"{pre}{v}{suf}")

        # 子控件渲染到内部区域
        child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
        if child is not None:
            # 渲染到临时缓冲区再合并回父缓冲区
            tmp = RenderBuffer(inner_w, inner_h)
            Widget._render_child_safe(child, tmp, "Border")
            buffer.merge(tmp, 1, 1)

    def _build_top_border(
        self, tl: str, tr: str, h: str,
        box_w: int, title: str, align: str,
        pre: str, suf: str,
    ) -> str:
        """构建顶部边框行。

        Args:
            tl: 左上角字符。
            tr: 右上角字符。
            h: 水平边框字符。
            box_w: 边框总宽度。
            title: 标题文本。
            align: 标题对齐方式。
            pre: ANSI 颜色前缀。
            suf: ANSI 颜色后缀。

        Returns:
            格式化后的顶部边框字符串。
        """
        return build_title_border(tl, tr, h, box_w, title, align, pre=pre, suf=suf)

    @staticmethod
    def _is_narrow() -> bool:
        """检测当前是否为窄屏模式。"""
        from .terminal.narrow import is_narrow
        return is_narrow()

    def _render_narrow(self, buffer: RenderBuffer) -> None:
        """窄屏降级：无边框，直接渲染子控件。"""
        child = self._children[0] if self._children else (
            self._children_source[0] if self._children_source else None
        )
        if child is not None:
            Widget._render_child_safe(child, buffer, "Border(narrow)")

    @staticmethod
    def _get_box_chars(style_name: str) -> dict | None:
        """获取边框字符集。

        Args:
            style_name: 边框样式名。

        Returns:
            边框字符字典，包含 tl/tr/bl/br/h/v 键。获取失败时返回 None。
        """
        try:
            from .components._box import BoxStyle as _BS, _BOX_CHARS as _BC
            # 将字符串转为 BoxStyle 枚举
            for bs in _BS:
                if bs.value == style_name:
                    return _BC.get(bs)
            # 兜底使用 ROUNDED
            return _BC.get(_BS.ROUNDED)
        except (ImportError, AttributeError):
            # 降级：基本 ASCII 边框
            return {
                "tl": "+", "tr": "+", "bl": "+", "br": "+",
                "h": "-", "v": "|",
            }

    def __repr__(self) -> str:
        return f"Border(style={self._props.get('style')})"


# ═══════════════════════════════════════════════════════════
# Grid — 网格布局
# ═══════════════════════════════════════════════════════════


class Grid(LayoutWidget):
    """网格布局控件。

    将子控件按二维网格排列。每个子控件占据一个网格单元格，
    所有单元格宽度一致（等分容器宽度），高度自适应。

    Args:
        children: 二维子控件列表，每行一个 list[Widget]。
        cols: 列数。默认 None 时根据子控件自动计算（取最大行宽）。
        spacing: 单元格间距（字符数），默认 1。
        align: 水平对齐方式，"left" / "center" / "right"，默认 "left"。
        valign: 垂直对齐方式，"top" / "center" / "bottom"，默认 "top"。
    """

    def __init__(
        self,
        children: list[list[Widget]] | None = None,
        cols: int | None = None,
        spacing: int = 1,
        align: str = "left",
        valign: str = "top",
        key: str | None = None,
    ) -> None:
        super().__init__(children or [], renders_children=True, props={
            "cols": cols, "spacing": spacing,
            "align": align, "valign": valign,
        }, key=key)
        self._flat_children: list[Widget] = []
        for row in self._children_source:
            self._flat_children.extend(row)

    def compose(self) -> list[Widget]:
        """返回扁平化后的子控件列表。"""
        return self._flat_children

    def render(self, buffer: RenderBuffer) -> None:
        """网格排列渲染所有子控件。"""
        children = self._children_source
        if not children:
            return
        sp = self._props.get("spacing", 1)
        al = self._props.get("align", "left")
        val = self._props.get("valign", "top")

        # 确定列数
        max_cols = max(len(row) for row in children) if children else 0
        cols = self._props.get("cols") or max_cols

        if cols <= 0:
            return

        # 计算每列宽度（等分容器宽度，减去间距）
        total_spacing = sp * (cols - 1) if cols > 1 else 0
        col_width = max(1, (buffer.width - total_spacing) // cols) if cols > 0 else buffer.width

        y = 0
        for row_idx, row in enumerate(children):
            # 先渲染行中所有单元格到临时 buffer，确定行高
            row_heights: list[int] = []
            row_buffers: list[RenderBuffer] = []

            for cell in row:
                cell_buf = RenderBuffer(col_width, buffer.height - y)
                Widget._render_child_safe(cell, cell_buf, "Grid")
                cell_str = cell_buf.render()
                cell_h = max(1, len(cell_str.split("\n")) if cell_str else 1)
                row_heights.append(cell_h)
                row_buffers.append(cell_buf)

            # 该行所有单元格的最大高度
            row_height = max(row_heights) if row_heights else 1

            # 按水平位置合并到父缓冲区
            x = 0
            for col_idx in range(cols):
                if x >= buffer.width:
                    break
                if col_idx < len(row_buffers):
                    cb = row_buffers[col_idx]
                    ch = row_heights[col_idx]
                    # 垂直对齐
                    y_offset = _align_vertical(ch, row_height, val)
                    # 水平对齐
                    if al in ("center", "right"):
                        tmp_str = cb.render()
                        if tmp_str:
                            _align_horizontal(buffer, x, y + y_offset, tmp_str, al, col_width)
                    else:
                        buffer.merge(cb, x, y + y_offset)
                x += col_width + sp

            y += row_height
            if y >= buffer.height:
                break

    def __repr__(self) -> str:
        flat = self._flat_children if self._flat_children else []
        return f"Grid({len(flat)} cells)"


# ═══════════════════════════════════════════════════════════
# Center — 居中对齐容器
# ═══════════════════════════════════════════════════════════


class Center(LayoutWidget):
    """居中对齐容器控件。

    将子控件在容器中水平和垂直居中。

    Args:
        child: 子控件。
        axis: 居中对齐轴，"both" / "horizontal" / "vertical"，默认 "both"。
    """

    def __init__(
        self,
        child: Widget,
        axis: str = "both",
        key: str | None = None,
    ) -> None:
        super().__init__([child], renders_children=True, props={"axis": axis}, key=key)

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表（始终为单元素列表）。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """将子控件居中渲染。"""
        if buffer.is_empty():
            return
        child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
        if child is None:
            return
        axis = self._props.get("axis", "both")

        # 渲染子控件到临时 buffer
        tmp = RenderBuffer(buffer.width, buffer.height)
        Widget._render_child_safe(child, tmp, "Center")
        child_str = tmp.render()
        if not child_str.strip():
            return
        child_lines = child_str.split("\n")
        child_w = max((len(l) for l in child_lines), default=0)
        child_h = len(child_lines)

        # 计算偏移
        x_offset = 0
        y_offset = 0

        if axis in ("both", "horizontal"):
            x_offset = max(0, (buffer.width - child_w) // 2)
        if axis in ("both", "vertical"):
            y_offset = max(0, (buffer.height - child_h) // 2)

        # 合并到父缓冲区
        for i, line in enumerate(child_lines):
            dst_y = y_offset + i
            if 0 <= dst_y < buffer.height:
                buffer.write(x_offset, dst_y, line)

    def __repr__(self) -> str:
        return f"Center(axis={self._props.get('axis')})"



