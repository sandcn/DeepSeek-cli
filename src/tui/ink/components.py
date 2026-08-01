"""host 组件渲染函数 — 将布局好的 host 树绘制为 Frame。

渲染流程：
  1. ``layout_tree`` 为每个 host fiber 赋值 LayoutBox。
  2. 建立行画布（dict 列 → (char, style)），自顶向下绘制每个 host 节点。
  3. 画布行转为 Line 列表 → Frame。

绘制规则：
  - TEXT：按布局宽度换行，绘制到 box 区域（含 x 偏移）。
  - SPACER：空行（画布预置为空）。
  - BOX/STATIC/APP：绘制边框（border>0）后递归绘制子节点。

``render_frame(root, width)`` 为对外入口（session 渲染时调用）。
"""

from __future__ import annotations

from typing import Any

from src.tui.core.style import Style
from .fiber import Fiber
from .layout import layout_tree, layout_children, wrap_text_lines
from .output import Frame, Line


def _border_style(props: dict) -> Style:
    style = props.get("borderStyle")
    if isinstance(style, Style):
        return style
    return Style(fg=23)


def _merge_line(row: dict[int, tuple[str, Style | None]], x: int, line: Line) -> None:
    """将 Line 合并到画布行（从第 x 列开始）。"""
    col = x
    for run in line.runs:
        for ch in run.text:
            row[col] = (ch, run.style)
            col += 1


def _paint_border(fiber: Fiber, canvas: list[dict], border: int) -> None:
    """绘制 box 边框（border>=1 时画单线框）。"""
    box = fiber.layout_box
    style = _border_style(fiber.props)
    x0, y0 = box.x, box.y
    x1 = x0 + box.w - 1
    y1 = y0 + box.h - 1
    if y0 < 0 or y0 >= len(canvas):
        return
    # 顶边 / 底边
    for row_idx, (y, corner_l, corner_r) in enumerate(
        ((y0, "┌", "┐"), (y1, "└", "┘"))
    ):
        if y < 0 or y >= len(canvas):
            continue
        row = canvas[y]
        if y0 == y1 and row_idx == 1:
            continue
        row[x0] = (corner_l, style)
        row[x1] = (corner_r, style)
        for c in range(x0 + 1, x1):
            row[c] = ("─", style)
    # 左右边（不含顶/底）
    for r in range(y0 + 1, y1):
        if r < 0 or r >= len(canvas):
            continue
        row = canvas[r]
        row[x0] = ("│", style)
        row[x1] = ("│", style)


def _paint(fiber: Fiber, canvas: list[dict]) -> None:
    """递归绘制一个 host fiber 到画布。"""
    box = fiber.layout_box
    if box is None:
        return
    ftype = fiber.type

    if ftype == "text":
        styled = fiber.props.get("styled")
        text = str(fiber.props.get("children", ""))
        style = fiber.props.get("style")
        if styled is not None:
            from .helpers import wrap_runs_by_width
            lines = wrap_runs_by_width(list(styled), box.w)
        else:
            lines = wrap_text_lines(text, box.w, style)
        for i, line in enumerate(lines):
            row = box.y + i
            if 0 <= row < len(canvas):
                _merge_line(canvas[row], box.x, line)
        return

    if ftype == "spacer":
        return  # 空行已由画布预置

    # ── 自定义 host（注册表） ──
    from .registry import get_host
    host = get_host(ftype)
    if host is not None:
        paint_fn = host[1]
        try:
            paint_fn(fiber, canvas)
        except Exception:
            pass
        return

    # 容器：BOX / STATIC / APP
    border = fiber.props.get("border", 0)
    try:
        border = max(0, int(border))
    except (TypeError, ValueError):
        border = 0
    if border:
        _paint_border(fiber, canvas, border)
    for child in layout_children(fiber):
        _paint(child, canvas)


def _canvas_row_to_line(row: dict[int, tuple[str, Style | None]]) -> Line:
    """画布行（列→字符）转为 Line（按列排序，合并相邻同样式）。"""
    line = Line()
    for col in sorted(row):
        ch, style = row[col]
        line.append(ch, style)
    return line


def render_frame(root: Fiber, width: int) -> Frame:
    """渲染布局好的 host 树为整帧 Frame。

    Args:
        root: 根 fiber（ROOT 或 APP host）。
        width: 文档宽度（终端列宽）。

    Returns:
        完整文档的 Frame。
    """
    total_h = layout_tree(root, width)
    canvas: list[dict] = [{} for _ in range(max(1, total_h))]
    _paint(root, canvas)
    return Frame(_canvas_row_to_line(row) for row in canvas)


__all__ = ["render_frame"]
