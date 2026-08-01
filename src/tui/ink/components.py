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

import logging
from typing import Any

from src.tui.core.style import Style
from .fiber import Fiber
from .layout import layout_tree, layout_children, wrap_text_lines, _skip_function
from .output import Frame, Line

_logger = logging.getLogger(__name__)


def _border_style(props: dict) -> Style:
    style = props.get("borderStyle")
    if isinstance(style, Style):
        return style
    return Style(fg=23)


def _merge_line(row: dict[int, tuple[str, Style | None]], x: int, line: Line) -> None:
    """将 Line 合并到画布行（从第 x 列开始）。

    性能快路径：构造 ``{col: (ch, style)}`` 片段，与目标行键集无交时批量
    ``row.update(slice_)``；重叠时回退逐字符覆盖（语义一致）。
    """
    if not line.runs:
        return
    slice_: dict[int, tuple[str, Style | None]] = {}
    col = x
    for run in line.runs:
        for ch in run.text:
            slice_[col] = (ch, run.style)
            col += 1
    if slice_.keys().isdisjoint(row):
        row.update(slice_)
    else:
        for c, v in slice_.items():
            row[c] = v


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
        # ★ 复用 layout 阶段缓存的换行结果（免二次包裹）
        wrapped = getattr(fiber, "_wrapped_lines", None)
        if wrapped is not None:
            lines = wrapped
        else:
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
            # 非关键降级：host 绘制失败不影响整帧
            _logger.debug("custom host %s paint 异常", ftype, exc_info=True)
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


def _canvas_row_to_line(row) -> Line:
    """画布行转 Line。

    支持两种行：dict（列→(char,style)，增量合并）或已缓存的 Line
    （committed-chat 直接引用，免逐字符重绘 → 增量渲染核心）。
    """
    if isinstance(row, Line):
        return row
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
    # ★ 复用 reconciler 已布局的高度（免二次 layout_tree）
    host_root = _skip_function(root) or root
    box = host_root.layout_box
    if box is not None and box.w == width:
        total_h = box.h
    else:
        total_h = layout_tree(root, width)
    canvas: list[dict] = [{} for _ in range(max(1, total_h))]
    _paint(root, canvas)
    return Frame(_canvas_row_to_line(row) for row in canvas)


__all__ = ["render_frame"]
