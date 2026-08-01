"""布局 — flexbox 子集 + 文本换行。

布局模型（内容驱动 / 非全屏流动）：
  整个文档高度由内容推导（无视口 pin）——每个组件按其内容换行后
  累加得到高度；宽度由 ``width`` 属性或父容器宽度决定。

  树形布局采用后序遍历：先测量叶子（Text 换行行数 / Spacer 高度），
  再累加容器（BOX/STATIC/APP）子节点高度，为每个 host fiber 赋值
  ``LayoutBox(x, y, w, h)``（文档坐标系，0-based）。

支持的 flexbox 子集：
  - flexDirection: column（默认）| row
  - justifyContent: flex-start（默认）| center | flex-end（column 纵向）
  - alignItems: stretch（默认）| center | flex-end（横向对齐）
  - flexGrow / flexShrink: int
  - width / height / minHeight / maxHeight: int
  - padding / border / margin: int（均一值）
  - 文本换行/截断（用 wcswidth_simple）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.tui._screen import wcswidth_simple
from .fiber import Fiber, TAG_HOST
from .output import StyledRun, Line


# ═══════════════════════════════════════════════════════════
# LayoutBox — 布局结果
# ═══════════════════════════════════════════════════════════


@dataclass
class LayoutBox:
    """布局盒（文档坐标系，0-based）。

    Attributes:
        x: 左列偏移。
        y: 顶行偏移。
        w: 显示宽度。
        h: 显示高度（行数）。
    """

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


# ═══════════════════════════════════════════════════════════
# host 树遍历辅助
# ═══════════════════════════════════════════════════════════


def _runs_natural_width(runs: list) -> int:
    """styled runs 的自然内容宽度（单行拼接宽度）。"""
    total = 0
    for r in runs:
        total += wcswidth_simple(getattr(r, "text", str(r)))
    return total


def _skip_function(fiber: Fiber | None) -> Fiber | None:
    """沿 function 链下降，返回首个 host fiber（或 None）。"""
    f = fiber
    while f is not None and f.is_function:
        f = f.child
    return f


def layout_children(fiber: Fiber) -> list[Fiber]:
    """返回 fiber 的直接 host 子节点（跳过 function 链）。"""
    result: list[Fiber] = []
    child = fiber.child
    while child is not None:
        host = _skip_function(child)
        if host is not None:
            result.append(host)
        child = child.sibling
    return result


# ═══════════════════════════════════════════════════════════
# 文本换行
# ═══════════════════════════════════════════════════════════


def wrap_text_lines(text: str, width: int, style=None) -> list[Line]:
    """将文本按显示宽度换行为 Line 列表（CJK 安全）。"""
    if width <= 0:
        return [Line.of(text, style)] if text else []
    from .helpers import wrap_runs_by_width
    return wrap_runs_by_width([StyledRun(text, style)], width)


# ═══════════════════════════════════════════════════════════
# 布局算法
# ═══════════════════════════════════════════════════════════


def _resolve_width(fiber: Fiber, avail: int) -> int:
    w = fiber.props.get("width")
    if w is None:
        return avail
    try:
        return max(0, int(w))
    except (TypeError, ValueError):
        return avail


def _resolve_height(fiber: Fiber, content_h: int) -> int:
    """解析高度：显式 height 属性优先，否则内容推导（含 min/max 夹取）。"""
    h = content_h
    height = fiber.props.get("height")
    if height is not None:
        try:
            h = max(0, int(height))
        except (TypeError, ValueError):
            pass
    mn = fiber.props.get("minHeight")
    if mn is not None:
        try:
            h = max(int(mn), h)
        except (TypeError, ValueError):
            pass
    mx = fiber.props.get("maxHeight")
    if mx is not None:
        try:
            h = min(int(mx), h)
        except (TypeError, ValueError):
            pass
    return h


def _measure(fiber: Fiber, x: int, y: int, avail_w: int, fill: bool = True) -> LayoutBox:
    """递归测量并赋值 layout_box。返回该 fiber 的 LayoutBox。

    Args:
        fiber: host fiber。
        x, y: 父容器内偏移（文档坐标系）。
        avail_w: 可用宽度。
        fill: True=填充可用宽度（column 默认）；False=内容自适应宽度（row）。
    """
    ftype = fiber.type
    explicit_w = fiber.props.get("width")

    # ── 自定义 host（注册表） ──
    from .registry import get_host
    host = get_host(ftype)
    if host is not None:
        measure_fn = host[0]
        w, h = measure_fn(fiber, avail_w)
        box = LayoutBox(x, y, w, h)
        fiber.layout_box = box
        return box

    # ── 叶子：TEXT ──
    if ftype == "text":
        from .helpers import wrap_runs_by_width
        styled = fiber.props.get("styled")
        text = str(fiber.props.get("children", ""))
        style = fiber.props.get("style")
        if styled is not None:
            runs = list(styled)
        else:
            runs = [StyledRun(text, style)] if text else []
        if explicit_w is not None:
            width = max(0, int(explicit_w))
        elif fill:
            width = avail_w
        else:
            if runs:
                content_w = _runs_natural_width(runs)
            else:
                content_w = max((wcswidth_simple(line) for line in text.split("\n")), default=0)
            width = max(0, min(avail_w, content_w))
        lines = wrap_runs_by_width(runs, width)
        h = max(1, len(lines)) if (lines or runs or text) else 1
        box = LayoutBox(x, y, width, h)
        fiber.layout_box = box
        return box

    # ── 叶子：SPACER ──
    if ftype == "spacer":
        if explicit_w is not None:
            width = max(0, int(explicit_w))
        else:
            width = avail_w if fill else 1
        h = fiber.props.get("height", 1)
        try:
            h = max(0, int(h))
        except (TypeError, ValueError):
            h = 1
        box = LayoutBox(x, y, width, h)
        fiber.layout_box = box
        return box

    # ── 容器：BOX / STATIC / APP ──
    padding = fiber.props.get("padding", 0)
    try:
        padding = max(0, int(padding))
    except (TypeError, ValueError):
        padding = 0
    border = fiber.props.get("border", 0)
    try:
        border = max(0, int(border))
    except (TypeError, ValueError):
        border = 0
    margin = fiber.props.get("margin", 0)
    try:
        margin = max(0, int(margin))
    except (TypeError, ValueError):
        margin = 0

    inner_x = x + padding + border
    inner_y = y + padding + border
    children = layout_children(fiber)
    direction = fiber.props.get("flexDirection", "column")

    if direction == "row":
        # 子节点横向排列（内容自适应宽度），高度为最大子高
        row_inner_w = max(0, avail_w - 2 * (padding + border))
        cursor_x = inner_x
        row_h = 0
        for child in children:
            remaining = max(0, row_inner_w - (cursor_x - inner_x))
            cbox = _measure(child, cursor_x, inner_y, remaining, fill=False)
            cursor_x += cbox.w + margin
            row_h = max(row_h, cbox.h)
        if explicit_w is not None:
            width = max(0, int(explicit_w))
        else:
            content_w = cursor_x - inner_x
            width = max(0, min(avail_w, content_w + 2 * (padding + border)))
        total_h = row_h
    else:
        # 子节点纵向堆叠（填充宽度），高度为内容累加
        width = max(0, int(explicit_w)) if explicit_w is not None else avail_w
        inner_w = max(0, width - 2 * (padding + border))
        cursor_y = inner_y
        total_h = 0
        n = len(children)
        for i, child in enumerate(children):
            cbox = _measure(child, inner_x, cursor_y, inner_w, fill=True)
            cursor_y += cbox.h + margin
            total_h += cbox.h
            if i < n - 1:
                total_h += margin

    content_h = total_h if children else 0
    h = content_h + 2 * (padding + border)
    h = _resolve_height(fiber, h)

    # flexGrow：显式高度富余时按 flexGrow 比例分配
    if h > content_h + 2 * (padding + border) and children:
        grow_total = 0
        for child in children:
            grow_total += int(child.props.get("flexGrow", 0))
        remaining = h - (content_h + 2 * (padding + border))
        if grow_total > 0 and remaining > 0:
            per = remaining // grow_total
            cursor_y = inner_y
            for child in children:
                cb = child.layout_box
                grow = int(child.props.get("flexGrow", 0))
                extra = per * grow
                if extra > 0:
                    cb.h += extra
                    child.layout_box = cb
                cursor_y += cb.h + margin

    box = LayoutBox(x, y, width, h)
    fiber.layout_box = box
    return box


def layout_tree(root_fiber: Fiber, width: int) -> int:
    """布局整棵 host 树。

    Args:
        root_fiber: 根 fiber（ROOT 或 APP host）。
        width: 文档宽度（终端列宽）。

    Returns:
        文档总高度（行数）。
    """
    root = _skip_function(root_fiber) or root_fiber
    box = _measure(root, 0, 0, width)
    return box.h


__all__ = [
    "LayoutBox",
    "layout_tree",
    "layout_children",
    "wrap_text_lines",
]
