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

from src.tui.core.style import Style
from src.tui._screen import wcswidth_simple
from .fiber import Fiber
from .layout import layout_tree, layout_children, wrap_text_lines, _skip_function
from .output import Frame, Line

_logger = logging.getLogger(__name__)


def _border_style(props: dict) -> Style:
    """解析边框样式（react-ink ``borderColor`` shorthand，完善 ink）。

    - ``borderStyle`` 传 Style 对象时原样返回（既有行为）。
    - 否则取 ``borderColor``（256 色号 int / 颜色名字符串）作 fg；
      缺省回退深青 23（既有行为）。
    """
    style = props.get("borderStyle")
    if isinstance(style, Style):
        return style
    border_color = props.get("borderColor")
    if border_color is not None:
        from .helpers import _parse_color
        color = _parse_color(border_color)
        if color is not None:
            return Style(fg=color)
    return Style(fg=23)


#: borderStyle 变体字符表（完善 react ink）：单线/双线/圆角/粗体。
#: 键 = props["borderStyle"] 字符串；缺省 "single"。
_BORDER_CHARS: dict[str, tuple[str, str, str, str, str, str]] = {
    "single": ("┌", "┐", "└", "┘", "─", "│"),
    "double": ("╔", "╗", "╚", "╝", "═", "║"),
    "round": ("╭", "╮", "╰", "╯", "─", "│"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃"),
}


def _border_chars(fiber: Fiber) -> tuple[str, str, str, str, str, str]:
    """解析 borderStyle 变体字符（缺省 single；未知值回退 single）。"""
    name = fiber.props.get("borderStyle")
    if not isinstance(name, str):
        return _BORDER_CHARS["single"]
    return _BORDER_CHARS.get(name, _BORDER_CHARS["single"])


def _line_as_dict(line: Line) -> dict:
    """将 Line 转为列键字典（``{display_col: (ch, style)}``，CJK 安全）。

    列键为**显示宽度**（``wcswidth_simple``），与画布行键语义一致——
    CJK 宽字符占 2 列则键递增 2（修复前逐字符 ``col += 1`` 导致宽字符
    后续内容错位重叠）。
    """
    d: dict = {}
    col = 0
    for run in line.runs:
        for ch in run.text:
            d[col] = (ch, run.style)
            col += wcswidth_simple(ch)
    return d


def _ensure_row_dict(row) -> dict:
    """将画布行归一化为 dict（Line/None → dict，dict 原样返回）。

    画布行可能为三种形态：None（惰性空行）、Line（box.x==0 快路径写入的
    Line 对象）、dict（增量合并）。后续 dict 操作（``row[col]=...`` /
    ``row.update(...)``）前必须先归一化——修复前对 Line 直接做 dict 操作
    抛 AttributeError/TypeError，被 _paint 隔离吞掉导致内容丢失。
    """
    if isinstance(row, Line):
        return _line_as_dict(row)
    if row is None:
        return {}
    return row


def _merge_line(row, x: int, line: Line) -> dict:
    """将 Line 合并到画布行（从第 x 列开始），返回合并后的行。

    性能快路径：构造 ``{col: (ch, style)}`` 片段，与目标行键集无交时批量
    ``row.update(slice_)``；重叠时回退逐字符覆盖（语义一致）。目标行可能
    为 Line/None/dict 任意形态——先 ``_ensure_row_dict`` 归一化再合并。

    返回合并后的 dict 行（调用方写回 canvas[row]）——修复前返回 None，
    Line→dict 转换结果无法写回画布，目标行保持 Line 引用导致后续兄弟节点
    继续对 Line 做 dict 操作失败（row-of-texts 仅首项绘制）。
    """
    if not line.runs:
        return _ensure_row_dict(row)
    slice_: dict[int, tuple[str, Style | None]] = {}
    col = x
    for run in line.runs:
        for ch in run.text:
            slice_[col] = (ch, run.style)
            col += wcswidth_simple(ch)
    row = _ensure_row_dict(row)
    if slice_.keys().isdisjoint(row):
        row.update(slice_)
    else:
        for c, v in slice_.items():
            row[c] = v
    return row


def _paint_border(fiber: Fiber, canvas: list[dict], border: int) -> None:
    """绘制 box 边框（border>=1 时画单线框）。"""
    box = fiber.layout_box
    # ★ 边框防御（方向1）：box 无效（None / 零宽 / 零高）时直接返回——
    #   修复前 ``x1 = x0 + box.w - 1`` 在 w=0 时 x1=x0-1，``row[x1]`` 负索引
    #   从列表末尾写（越界污染画布）。
    if box is None or box.w <= 0 or box.h <= 0:
        return
    style = _border_style(fiber.props)
    tl, tr, bl, br, hline, vline = _border_chars(fiber)
    x0, y0 = box.x, box.y
    x1 = x0 + box.w - 1
    y1 = y0 + box.h - 1
    if y0 < 0 or y0 >= len(canvas):
        return
    # 顶边 / 底边
    for row_idx, (y, corner_l, corner_r) in enumerate(
        ((y0, tl, tr), (y1, bl, br))
    ):
        if y < 0 or y >= len(canvas):
            continue
        row = canvas[y]
        # ★ 画布惰性行（方向4）：未命中行才创建 dict（行级缓存优化）；
        #   已存在 Line（box.x==0 快路径写入）先归一化为 dict——修复前对
        #   Line 直接 ``row[x0]=...`` 抛 TypeError（Line 不支持 item 赋值），
        #   边框被 _paint 隔离吞掉 → 边框缺失。
        if isinstance(row, Line):
            row = _line_as_dict(row)
            canvas[y] = row
        elif row is None:
            row = {}
            canvas[y] = row
        if y0 == y1 and row_idx == 1:
            continue
        row[x0] = (corner_l, style)
        row[x1] = (corner_r, style)
        for c in range(x0 + 1, x1):
            row[c] = (hline, style)
    # 左右边（不含顶/底）
    for r in range(y0 + 1, y1):
        if r < 0 or r >= len(canvas):
            continue
        row = canvas[r]
        if isinstance(row, Line):
            row = _line_as_dict(row)
            canvas[r] = row
        elif row is None:
            row = {}
            canvas[r] = row
        row[x0] = (vline, style)
        row[x1] = (vline, style)


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
    # display: none（完善 react ink）——隐藏组件不绘制（_measure 已返回零盒）。
    if fiber.props.get("display") == "none":
        return

    if ftype == "text":
        # ★ 复用 layout 阶段缓存的换行结果（免二次包裹）
        wrapped = getattr(fiber, "_wrapped_lines", None)
        if wrapped is not None:
            lines = wrapped
        else:
            from .helpers import (
                wrap_runs_by_width,
                resolve_text_style,
                apply_text_transform,
            )
            styled = fiber.props.get("styled")
            transform = fiber.props.get("transform")
            text = apply_text_transform(
                str(fiber.props.get("children", "")), transform,
            )
            style = resolve_text_style(fiber.props)
            if styled is not None:
                lines = wrap_runs_by_width(list(styled), box.w)
            else:
                lines = wrap_text_lines(text, box.w, style)
        # 行级复用：canvas 行直接写入 Line 对象（box.x==0 快路径），diff 阶段
        # 身份短路（同 Line 对象恒相等）跳过——跨帧零重建。不再维护
        # ``_paint_cache``（死缓存：只写不读，实际复用来自 _wrapped_lines
        # 引用与 canvas 行 Line 身份，方向3 移除）。
        for i, line in enumerate(lines):
            row = box.y + i
            if 0 <= row < len(canvas):
                if box.x == 0:
                    # 整行复用 Line 对象（免逐字符重绘 → diff 身份短路受益）
                    if canvas[row] is None:
                        canvas[row] = line
                    else:
                        # 行已有内容（前序 x==0 兄弟 / 边框等）→ 归一并合并
                        # （不替换——修复前直接替换丢失已有内容）
                        canvas[row] = _merge_line(canvas[row], 0, line)
                else:
                    canvas[row] = _merge_line(canvas[row], box.x, line)
        return

    if ftype == "spacer":
        return  # 空行已由画布预置

    if ftype == "fragment":
        # 透明分组容器：直接绘制子节点（无独立 box——layout_children 已将
        # fragment 扁平化；本分支为防御，覆盖 fragment 被直接调度的路径）
        for child in layout_children(fiber):
            _paint(child, canvas)
        return

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

    列键为显示宽度（含 CJK 宽字符），转换为 Line 时**先补空格再写字符**
    ——修复前 ``sorted(row)`` 直接逐键拼接，跳过空列（如 justifyContent
    center/flex-end、alignItems 偏移、padding 留白、行内缩进），行首/
    行中间的空格全部丢失 → 水平定位失效。宽字符占位按**显示宽度**推进
    （``prev = col + wcswidth_simple(ch)``）——修复前 ``prev = col + 1``
    对 CJK 字符（占 2 列）推进不足，后续键 > prev 产生多余空格
    （``中文`` 被渲染为 ``中 文``）。

    方向4（性能）：一次排序 + 段级累积——连续同 style 字符先累积到
    ``run_text``（行宽有界 ≤80 列，str += 段长可接受），最后一次性构造
    Line（免逐字符 ``Line.append`` 的段合并检查）。列间隙以空格段补齐。
    """
    if isinstance(row, Line):
        return row
    if row is None:
        return Line()
    line = Line()
    prev = 0
    keys = sorted(row)
    n = len(keys)
    i = 0
    while i < n:
        col = keys[i]
        ch, style = row[col]
        if col > prev:
            line.append(" " * (col - prev))
            prev = col  # 空格段宽 = 空格数
        # ★ 批量 append（方向4 性能）：累积同 style 连续字符段，段级一次性
        #   Line.append（免逐字符 append 的段合并检查 + StyledRun 重建——
        #   基准 ~2x 提速）。段长受行宽约束（≤终端列数），str += 拼接可接受。
        j = i
        buf = ""
        while j < n:
            c2 = keys[j]
            ch2, st2 = row[c2]
            if c2 != prev or st2 != style:
                break
            buf += ch2
            prev = c2 + wcswidth_simple(ch2)
            j += 1
        line.append(buf, style)
        i = j
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
            # 非顶部：前缀行填入画布对应区域（_paint 命中缓存已跳过画布写入）。
            # ★ 方向3（性能）：改为「顶部画布行 + 前缀 + 尾部画布行」直接拼接——
            #   修复前先把前缀行逐行拷贝回画布再全量 ``_canvas_row_to_line``
            #   （大历史下每帧 O(全部行) 转换，即使前缀行已是 Line 也要遍历）。
            #   本实现：canvas[0:y0]（TopHeader 等非 committed 顶部行）逐行转换；
            #   前缀直接复用（Line 对象身份不变 → diff 身份短路）；尾部
            #   canvas[y0+len(prefix):]（live 区）逐行转换。防御：len(prefix)
            #   可能超 canvas 尾部范围（reflow 期间布局陈旧）→ 按 fit 截断
            #   （与旧实现「超出画布的前缀行丢弃」行为一致）。
            y0 = committed_box.y if committed_box is not None else 0
            fit = min(len(prefix), max(0, len(canvas) - y0))
            header = [_canvas_row_to_line(r) for r in canvas[:y0]]
            tail = [_canvas_row_to_line(r) for r in canvas[y0 + fit:]]
            return Frame(header + prefix[:fit] + tail)
    return Frame(_canvas_row_to_line(row) for row in canvas)


__all__ = ["render_frame"]
