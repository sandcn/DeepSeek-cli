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
    # ★ 边框防御（方向1）：box 无效（None / 零宽 / 零高）时直接返回——
    #   修复前 ``x1 = x0 + box.w - 1`` 在 w=0 时 x1=x0-1，``row[x1]`` 负索引
    #   从列表末尾写（越界污染画布）。
    if box is None or box.w <= 0 or box.h <= 0:
        return
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
        # ★ 画布惰性行（方向4）：未命中行才创建 dict（行级缓存优化）
        if row is None:
            row = {}
            canvas[y] = row
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
        if row is None:
            row = {}
            canvas[r] = row
        row[x0] = ("│", style)
        row[x1] = ("│", style)


def _paint(fiber: Fiber, canvas: list[dict]) -> None:
    """递归绘制一个 host fiber 到画布。

    方向2 P7（建议7）：函数体 try/except 隔离——单节点 paint 抛异常 →
    该节点跳过、整帧仍渲染、异常不传播（与自定义 host 一致）。递归调用
    经本包装各自隔离（子节点异常不影响兄弟节点绘制）。layout 异常仍由
    session 层退避兜底（本步隔离 paint，不隔离 layout——布局失败影响树
    结构，保持 session 级处理）。
    """
    try:
        _paint_impl(fiber, canvas)
    except Exception:
        # 非关键降级：内置 host paint 失败不影响整帧
        _logger.debug("%s paint 异常", fiber.type, exc_info=True)


def _paint_impl(fiber: Fiber, canvas: list[dict]) -> None:
    """递归绘制一个 host fiber 到画布（_paint 内部实现，经 _paint 隔离）。

    递归调用 ``_paint(child, canvas)``（经包装）——子节点 paint 异常在
    子节点层级被捕获，不影响兄弟节点与父容器继续绘制。
    """
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
        # ★ 画布行级缓存（方向4）：同 styled/text 引用 + 同 box 命中时整行复用
        #   Line 对象（免逐字符重绘）；未命中正常绘制并写缓存。缓存键
        #   ``(ref, (box.x, box.w), lines)``——ref 为 styled 引用或 text 字符串，
        #   lines 为换行结果（引用）；fiber 复用/更新 props 后 ref 变化自然失效。
        ref = fiber.props.get("styled")
        if ref is None:
            ref = str(fiber.props.get("children", ""))
        cache = getattr(fiber, "_paint_cache", None)
        cache_key = (ref, (box.x, box.w), lines)
        if cache is None or cache[0] != cache_key:
            fiber._paint_cache = (cache_key, lines)
        for i, line in enumerate(lines):
            row = box.y + i
            if 0 <= row < len(canvas):
                if box.x == 0:
                    # 整行复用 Line 对象（免逐字符重绘 → diff 身份短路受益）
                    canvas[row] = line
                else:
                    if canvas[row] is None:
                        canvas[row] = {}
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

    支持三种行：dict（列→(char,style)，增量合并）、已缓存的 Line
    （committed-chat 直接引用，免逐字符重绘 → 增量渲染核心）、None
    （画布惰性行——行级缓存优化，未绘制的空行）。
    """
    if isinstance(row, Line):
        return row
    if row is None:
        return Line()
    line = Line()
    for col in sorted(row):
        ch, style = row[col]
        line.append(ch, style)
    return line


def _find_committed_chat(root: Fiber):
    """DFS 查找 committed-chat host fiber（聊天历史增量缓存发射器）。

    组件树中聊天历史作为单个 host 挂载（ChatView use_memo 缓存元素），
    静态行经 ``chat_view._paint`` 维护帧前缀缓存；render_frame 复用该前缀，
    每帧只重建尾部 live 区——大历史下 Frame 构建 O(live) 而非 O(全部历史)。
    """
    stack = [root]
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if f.is_host and f.type == "committed-chat":
            return f
        child = f.child
        while child is not None:
            stack.append(child)
            child = child.sibling
    return None


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
    # ★ 画布惰性行（方向4）：初始 None——仅未命中行创建 dict（行级缓存优化；
    #   TEXT 命中行直接放 Line 对象，免逐字符重绘）。
    canvas: list = [None] * max(1, total_h)
    _paint(root, canvas)
    # ★ committed-chat 前缀复用（大历史下渲染 O(live)）：静态提交行跨帧身份
    #   复用（``chat_view._paint`` 维护 ``_committed_prefix``），不再每帧全量
    #   遍历全部历史重建 Frame——修复长回答 + 子代理期间渲染线程持续重建
    #   整帧导致 CPU 100%。前缀未变时画布 committed 行被跳过（None），此处
    #   直接经缓存前缀拼接尾部。
    #   方向1 步骤4（非顶部前缀守卫）：仅顶部（committed.layout_box.y == 0）
    #   允许前缀直接拼接尾部——非顶部前缀与画布尾部重建偏移语义不一致时回退
    #   全量（防御层，成本 O(1)）；非顶部前缀已由 ``chat_view._paint`` 跳过
    #   画布写入，回退全量前将前缀行填入画布对应区域（box.y 起）保证 committed
    #   行不丢失。
    committed = _find_committed_chat(root)
    if committed is not None:
        prefix_info = getattr(committed, "_committed_prefix", None)
        if prefix_info is not None:
            committed_box = committed.layout_box
            prefix = prefix_info[1]
            if committed_box is not None and committed_box.y == 0:
                tail_start = min(len(prefix), len(canvas))
                tail = [_canvas_row_to_line(r) for r in canvas[tail_start:]]
                return Frame(prefix + tail)
            # 非顶部：前缀行填入画布对应区域（_paint 命中缓存已跳过画布写入）
            y0 = committed_box.y if committed_box is not None else 0
            for j, line in enumerate(prefix):
                row = y0 + j
                if 0 <= row < len(canvas):
                    canvas[row] = line
    return Frame(_canvas_row_to_line(row) for row in canvas)


__all__ = ["render_frame"]
