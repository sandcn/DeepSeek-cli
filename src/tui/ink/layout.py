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

from src._compat import dataclass
from dataclasses import field
from typing import Optional

from src.tui._screen import wcswidth_simple
from .fiber import Fiber, TAG_HOST
from .output import StyledRun, Line
from ._style_fp import style_fingerprint


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


def _flex_grow(fiber: Fiber) -> int:
    """解析 flexGrow（非数字兜底为 0，与 _resolve_width 一致——P2-2 修复）。

    ``int(...)`` 对非数字值（如字符串 ``"2"`` 之外的 ``None``/对象/畸形串）会抛
    ValueError/TypeError 直接中断渲染；同文件 width/height/padding/border/margin
    均有 try/except 兜底，唯独 flexGrow 缺失——补上。
    """
    g = fiber.props.get("flexGrow", 0)
    try:
        return max(0, int(g))
    except (TypeError, ValueError):
        return 0


def _flex_shrink(fiber: Fiber) -> int:
    """解析 flexShrink（非数字兜底为 0，与 _flex_grow 对称——方向2 U4）。

    与 flexGrow 对称：``int(...)`` 对非数字值（None/对象/畸形串）会抛
    ValueError/TypeError 直接中断渲染；同文件 width/height/padding/border/
    margin/flexGrow 均有 try/except 兜底——补上。
    """
    g = fiber.props.get("flexShrink", 0)
    try:
        return max(0, int(g))
    except (TypeError, ValueError):
        return 0


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
        from .helpers import (
            wrap_runs_by_width,
            truncate_runs_ellipsis,
            truncate_runs_start,
            truncate_runs_middle,
        )
        styled = fiber.props.get("styled")
        text = str(fiber.props.get("children", ""))
        style = fiber.props.get("style")
        # ★ textWrap 模式（方向B 步骤12 / 完善 ink）：
        #   "wrap"（默认，现行为）/ "truncate" / "truncate-end"（单行截断省略号，
        #   末尾省略号）/"truncate-start"（省略号在开头，保留尾部）/
        #   "truncate-middle"（保留头尾，中间省略号）——react-ink 完整语义。
        text_wrap = fiber.props.get("textWrap", "wrap")
        if explicit_w is not None:
            width = max(0, int(explicit_w))
        elif fill:
            width = avail_w
        else:
            # row 方向内容自适应宽度（快速路径前不构造 runs 列表——直接测自然宽）
            if styled is not None and styled:
                content_w = _runs_natural_width(styled)
            elif text:
                content_w = max((wcswidth_simple(line) for line in text.split("\n")), default=0)
            else:
                content_w = 0
            width = max(0, min(avail_w, content_w))
        # ★ 换行缓存（方向2 P1+P3）：结构 ``(ref, (width, text_wrap), style_fp, lines)``
        #   - cache[0] = ref：styled 列表引用（引用级快速路径）或 text 字符串
        #   - cache[1] = (width, text_wrap)
        #   - cache[2] = style_fp：稳定样式指纹（值驱动，BUG-T1——替代 id()）
        #   - cache[3] = lines：换行结果（跨帧复用）
        #   旧结构 ``(key, lines)`` 的 key 含 ``cache_text``（每帧先 join 再比较的
        #   完整拼接文本副本）——新结构不再持有该副本（P3 内存优化；ref 已被组件
        #   树引用，不额外占用）。P1 热路径优化：styled 静态历史（model 冻结行
        #   引用，如 committed_lines）同引用跨帧复用时直接复用 lines，免每帧
        #   O(chars) join + O(runs) 指纹计算（仅首次 miss 时计算）。
        cache = getattr(fiber, "_wrap_cache", None)
        cache_wt = (width, text_wrap)
        if (
            styled is not None
            and cache is not None
            and cache[0] is styled
            and cache[1] == cache_wt
        ):
            # 引用级快速路径：同 styled 引用 + (width, text_wrap) 不变 → 复用 lines
            lines = cache[3]
            runs = list(styled)  # 供下游 h 计算（行为与旧实现一致）
        else:
            if styled is not None:
                runs = list(styled)
                # BUG-T1：稳定样式指纹（值驱动），替代 id() 对象身份——
                #   id() 在对象 GC 后可能复用导致错误缓存命中/未命中
                #   注意：style 可为 None（无样式 run）→ 记 None（hashable 常量）
                style_fp = tuple(
                    style_fingerprint(r.style) if r.style is not None else None
                    for r in runs
                )
                ref = styled
            else:
                runs = [StyledRun(text, style)] if text else []
                style_fp = (style_fingerprint(style),) if style is not None else (None,)
                ref = text
            if (
                cache is not None
                and cache[0] == ref
                and cache[1] == cache_wt
                and cache[2] == style_fp
            ):
                lines = cache[3]
            else:
                if text_wrap in ("truncate", "truncate-end", "truncate-start", "truncate-middle"):
                    # 单行截断：内容超宽 → 截断 + 省略号（位置随模式）；未超宽 → 原样单行
                    if text_wrap == "truncate-start":
                        lines = [Line(truncate_runs_start(runs, width))]
                    elif text_wrap == "truncate-middle":
                        lines = [Line(truncate_runs_middle(runs, width))]
                    else:
                        lines = [Line(truncate_runs_ellipsis(runs, width))]
                else:
                    lines = wrap_runs_by_width(runs, width)
                fiber._wrap_cache = (ref, cache_wt, style_fp, lines)
        fiber._wrapped_lines = lines  # 供 paint 复用（免二次包裹）
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

    # flexShrink：显式高度不足（h < 内容高）且 children 含 shrink>0 时，按
    #   shrink 权重比例缩减子节点高度（每子至少保留 1 行，deficit 按 shrink
    #   权重分配）——与 flexGrow 余数分配对称（方向2 U4）。
    if h < content_h + 2 * (padding + border) and children:
        shrink_total = 0
        for child in children:
            shrink_total += _flex_shrink(child)
        deficit = (content_h + 2 * (padding + border)) - h
        if shrink_total > 0 and deficit > 0:
            per = deficit // shrink_total
            remainder = deficit % shrink_total
            cursor_y = inner_y
            for i, child in enumerate(children):
                cb = child.layout_box
                shrink = _flex_shrink(child)
                reduce = per * shrink + (1 if i < remainder else 0)
                if reduce > 0:
                    # 每子至少保留 1 行（钳制 ≥1）
                    cb.h = max(1, cb.h - reduce)
                # ★ 收缩修改子节点高度后重排 y 坐标（与 flexGrow 余数分配一致）
                cb.y = cursor_y
                child.layout_box = cb
                cursor_y += cb.h + margin

    # flexGrow：显式高度富余时按 flexGrow 比例分配（余数分配到前 n 个子节点）
    if h > content_h + 2 * (padding + border) and children:
        grow_total = 0
        for child in children:
            grow_total += _flex_grow(child)
        remaining = h - (content_h + 2 * (padding + border))
        if grow_total > 0 and remaining > 0:
            per = remaining // grow_total
            remainder = remaining % grow_total
            cursor_y = inner_y
            for i, child in enumerate(children):
                cb = child.layout_box
                grow = _flex_grow(child)
                extra = per * grow + (1 if i < remainder else 0)
                if extra > 0:
                    cb.h += extra
                # ★ P1-4：余数分配修改子节点高度后**重排 y 坐标**——_measure 按
                #   原高度分配 y（如 BOX(height=10)+两个 TEXT flexGrow 2/1 →
                #   text0.h=6 但 y=0、text1.h=4 但 y=1 垂直重叠）；写回
                #   cb.y = cursor_y 后光标再按新高度累加。
                cb.y = cursor_y
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
