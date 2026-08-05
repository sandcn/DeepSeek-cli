"""host 组件渲染主模块 — 布局好的 host 树绘制为 Frame。

模块边界（2026-08-05 架构优化）：原单一 components.py（1017 行）按职责拆分
为独立模块，本文件保留主绘制（``_paint``/``_paint_impl``/``_paint_children``
/``_resolve_clip``）+ 帧构建（``render_frame``/``_find_committed_chat``），
并 re-export 全部符号（旧导入路径 ``from src.tui.ink.components import ...``
保持不变，测试/外部调用面兼容）：

  - ``_paint_canvas.py`` — 画布行操作（Line↔dict 转换/合并/裁剪，CJK 安全）
  - ``_paint_border.py`` — 边框字符/样式解析 + 边框/背景画布绘制

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
from .fiber import Fiber
from .layout import layout_tree, wrap_text_lines, _skip_function
from .output import Frame, Line
from ._paint_canvas import (
    _merge_line,
    _slice_line,
    _canvas_row_to_line,
)
from ._paint_border import (
    _paint_border,
    _paint_box_background,
    _merge_inherit_bg,
    _apply_bg_to_line,
)

_logger = logging.getLogger(__name__)


def _resolve_clip(fiber: Fiber, box, clip) -> tuple | None:
    """解析容器 overflow 裁剪区域（完善 react ink v6）。

    ``overflow``/``overflowX``/``overflowY`` 取值 ``visible``（默认，不裁剪）
    /``hidden``（内容超出容器 box 时裁剪）。裁剪区域 = 当前裁剪区域（或
    全范围）与容器 box 在 hidden 方向的交集。返回 ``(x, y, w, h)``；空交集
    返回 ``(0, 0, 0, 0)``（全部裁剪哨兵——None 表示不裁剪，需区分）。

    Args:
        fiber: 容器 host fiber。
        box: 容器布局盒。
        clip: 当前裁剪区域（None=不裁剪）。

    Returns:
        (x, y, w, h) 裁剪区域；None 表示不裁剪（无 overflow hidden 且无父裁剪）。
    """
    ov = fiber.props.get("overflow", "visible")
    ovx = fiber.props.get("overflowX", ov)
    ovy = fiber.props.get("overflowY", ov)
    if ovx != "hidden" and ovy != "hidden":
        return clip
    if clip is None:
        px0, py0, pw, ph = 0, 0, 10**9, 10**9
    else:
        px0, py0, pw, ph = clip
    if ovx == "hidden":
        nx0 = max(px0, box.x)
        nx1 = min(px0 + pw, box.x + box.w)
    else:
        nx0 = px0
        nx1 = px0 + pw
    if ovy == "hidden":
        ny0 = max(py0, box.y)
        ny1 = min(py0 + ph, box.y + box.h)
    else:
        ny0 = py0
        ny1 = py0 + ph
    if nx1 <= nx0 or ny1 <= ny0:
        return (0, 0, 0, 0)
    return (nx0, ny0, nx1 - nx0, ny1 - ny0)


def _paint(fiber: Fiber, canvas: list[dict], clip=None, inherit_bg=None) -> None:
    """递归绘制一个 host fiber 到画布。

    方向2 P7（建议7）：函数体 try/except 隔离——单节点 paint 抛异常 →
    该节点跳过、整帧仍渲染、异常不传播（与自定义 host 一致）。递归调用
    经本包装各自隔离（子节点异常不影响兄弟节点绘制）。layout 异常仍由
    session 层退避兜底（本步隔离 paint，不隔离 layout——布局失败影响树
    结构，保持 session 级处理）。

    Args:
        fiber: host fiber。
        canvas: 画布（行列表）。
        clip: 裁剪区域 (x, y, w, h)；None 表示不裁剪。
        inherit_bg: 继承的背景样式（None=无；Box backgroundColor 传递）。
    """
    try:
        _paint_impl(fiber, canvas, clip, inherit_bg)
    except Exception:
        # 非关键降级：内置 host paint 失败不影响整帧
        _logger.debug("%s paint 异常", fiber.type, exc_info=True)


def _paint_impl(fiber: Fiber, canvas: list[dict], clip=None, inherit_bg=None) -> None:
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
        # ★ BUG-17（review 方向）：零宽/零高 TEXT 不绘制——``_measure`` 中
        #   ``width==0 and not fill`` 返回 h=0（"零宽非 fill 子节点不占位"），
        #   但 ``_wrapped_lines`` 非空（``wrap_runs_by_width(runs, 0)`` 返回
        #   单行）→ 修复前文本照常绘制到画布（row 剩余宽度 0 的子节点文本
        #   溢出容器，如宽 3 的 row 内 "abc"+"def" 渲染出 "abcdef"）。
        if box.w <= 0 or box.h <= 0:
            return
        # ★ 复用 layout 阶段缓存的换行结果（免二次包裹）
        wrapped = getattr(fiber, "_wrapped_lines", None)
        if wrapped is not None:
            lines = wrapped
            # ★ Box 背景继承（完善 react ink v6）——paint 阶段：layout 缓存
            #   的 lines 复用路径未含背景（布局只关心宽高，不解析 style 继承）。
            #   带 inherit_bg 且 lines 首 run 未应用该背景时克隆合并（不污染
            #   共享缓存）。正常 UI（无 Box 背景）inherit_bg 恒 None，零开销。
            if inherit_bg is not None and inherit_bg.bg is not None:
                _first = lines[0].runs[0] if lines and lines[0].runs else None
                if _first is None or getattr(_first, "style", None) is None or _first.style.bg != inherit_bg.bg:
                    lines = [_apply_bg_to_line(ln, inherit_bg.bg) for ln in lines]
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
                text_wrap = fiber.props.get("textWrap")
                if text_wrap is None:
                    text_wrap = fiber.props.get("wrap", "wrap")
                lines = wrap_runs_by_width(list(styled), box.w, hard=(text_wrap == "hard"))
            else:
                # ★ Box 背景继承（完善 react ink v6）：子 TEXT 未指定自身
                #   backgroundColor 时继承父 Box 背景色（_merge_inherit_bg）。
                text_wrap = fiber.props.get("textWrap")
                if text_wrap is None:
                    text_wrap = fiber.props.get("wrap", "wrap")
                lines = wrap_text_lines(
                    text, box.w, _merge_inherit_bg(style, inherit_bg),
                    hard=(text_wrap == "hard"),
                )
        # 行级复用：canvas 行直接写入 Line 对象（box.x==0 快路径），diff 阶段
        # 身份短路（同 Line 对象恒相等）跳过——跨帧零重建。不再维护
        # ``_paint_cache``（死缓存：只写不读，实际复用来自 _wrapped_lines
        # 引用与 canvas 行 Line 身份，方向3 移除）。
        # ★ overflow 裁剪（完善 react ink v6）：clip 非 None 时按裁剪区域
        #   限制行/列范围（垂直：行号在 [cy, cy+ch) 外跳过；水平：line 与
        #   [cx, cx+cw) 求交并切片）。
        if clip is not None:
            cx, cy, cw, ch = clip
            for i, line in enumerate(lines):
                row = box.y + i
                if cw <= 0 or ch <= 0:
                    continue
                if row < cy or row >= cy + ch:
                    continue
                if box.x >= cx + cw:
                    continue
                s = max(box.x, cx)
                e = min(box.x + line.width, cx + cw)
                if s >= e:
                    continue
                if s != box.x or e != box.x + line.width:
                    line = _slice_line(line, s - box.x, e - box.x)
                draw_x = s
                if 0 <= row < len(canvas):
                    canvas[row] = _merge_line(canvas[row], draw_x, line)
            return
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
        _paint_children(fiber, canvas, clip, inherit_bg)
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
    except (TypeError, ValueError, OverflowError):
        border = 0
    if border:
        _paint_border(fiber, canvas, border, clip)
    # Box 级 backgroundColor（完善 react ink v6）：填充整个 box 区域，
    # 子 TEXT 未指定自身背景色时继承（inherit_bg 传递）。
    from .helpers import _parse_color
    bg_prop = fiber.props.get("backgroundColor")
    bg_style = None
    if bg_prop is not None:
        bg_color = _parse_color(bg_prop)
        if bg_color is not None:
            bg_style = Style(bg=bg_color)
            _paint_box_background(box, canvas, bg_style)
    child_bg = bg_style if bg_style is not None else inherit_bg
    # overflow 裁剪（完善 react ink v6）：容器有 overflow hidden 时压入
    # 裁剪区域，子节点绘制受限（_resolve_clip 处理父裁剪合并）。
    new_clip = _resolve_clip(fiber, box, clip)
    _paint_children(fiber, canvas, new_clip, child_bg)


def _paint_children(fiber: Fiber, canvas: list, clip=None, inherit_bg=None) -> None:
    """绘制 fiber 的直接 host 子节点（跳过 function 链、扁平化 fragment）。

    方向4 性能优化：与 ``layout_children`` 遍历语义一致（跳过 function 链 +
    fragment 递归展开），但**不构建中间列表**——容器绘制时直接沿 child/sibling
    链递归调用 ``_paint``（大组件树如 ChatView 1000+ 子 TEXT 每帧少构建一次
    layout_children 结果列表，避免 O(n) 列表分配）。
    """
    child = fiber.child
    while child is not None:
        host = _skip_function(child)
        if host is not None:
            if host.is_host and host.type == "fragment":
                _paint_children(host, canvas, clip, inherit_bg)
            else:
                _paint(host, canvas, clip, inherit_bg)
        child = child.sibling


def _find_committed_chat(root: Fiber):
    """DFS 查找静态行批量渲染 host fiber（StaticLines / committed-chat）。

    组件树中聊天历史作为单个 host 挂载（ChatView use_memo 缓存元素），
    静态行经 ``staticlines._paint`` 维护帧前缀缓存；render_frame 复用该前缀，
    每帧只重建尾部 live 区——大历史下 Frame 构建 O(live) 而非 O(全部历史)。

    方向4（性能）：查找结果缓存于 root fiber（``_committed_chat_cache``）——
    reconciler 按 key 复用 fiber，静态行 host 在树中位置跨帧稳定，无需每帧
    DFS 全树搜索（大历史树 ~1500 fiber 时 DFS 为可感知开销）。缓存失效条件：
    缓存的 fiber 被删除（deleted）或 type 不再匹配（如 committed_lines 清空
    后 ChatView 不再挂载）→ 重新 DFS。Cache miss 后写回缓存。

    ★ 性能（PERF-15）：**未挂载快速路径**——静态行 host 通常**不存在**
    （纯 TEXT 组件树 / 无聊天历史的场景），且其存在性跨帧稳定（ChatView
    ``use_memo`` 依赖 ``model.committed_lines``，空列表时不挂载）。修复前
    ``_find_committed_chat`` 对**每帧**都做全树 DFS（找到才写缓存；未找到时
    ``del`` 缓存——**下一帧又 DFS**），纯 TEXT 大组件树（1000+ fiber）每帧
    DFS 开销可感知（~12ms/帧）。修复：fiber 上缓存 ``_committed_chat_present``
    标志（reconciler 每帧调和时统计是否存在静态行 host，layout_tree
    前的整树遍历天然提供该信息）；标志为 False 时 O(1) 返回 None，零 DFS。
    """
    # ★ PERF-15：未挂载快速路径——标志由 reconciler._measure 统计（layout_tree
    #   整树遍历时置位；见 layout.py _measure 容器分支注释）。无静态行 host
    #   的组件树每帧零 DFS。
    if not getattr(root, "_committed_chat_present", False):
        return None
    cached = getattr(root, "_committed_chat_cache", None)
    if (
        cached is not None
        and cached.is_host
        and cached.type == "static-lines"
        and not getattr(cached, "deleted", False)
    ):
        return cached
    stack = [root]
    found = None
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if f.is_host and f.type == "static-lines":
            found = f
            break
        child = f.child
        while child is not None:
            stack.append(child)
            child = child.sibling
    if found is not None:
        root._committed_chat_cache = found
    else:
        # 未找到 → 清空缓存（静态行 host 已卸载）
        if hasattr(root, "_committed_chat_cache"):
            del root._committed_chat_cache
    return found


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

    def _to_line(row) -> Line:
        """画布行转 Line（行宽不变量 E-OVERFLOW-GUARD：超宽行截断到 width）。

        布局层异常（嵌套容器内容超宽/宽字符硬塞等导致行宽超文档宽）时，行级
        截断保证**行宽恒 <= width**——行级 diff 模型依赖该不变量（超宽行会
        破坏 diff/光标定位）。截断重建 Line 对象（身份短路失效），仅异常行
        触发（正常布局行宽 <= width，原样返回零开销）。
        """
        line = _canvas_row_to_line(row)
        if line.width > width:
            from .helpers import truncate_line
            return truncate_line(line, width)
        return line

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
            # ★ 行宽守卫（E-COMMITTED-OVERFLOW 防御）：前缀含超宽行
            #   （reflow_committed 未执行/失败——终端宽度变化后 committed_lines
            #   按旧宽度 wrap）时截断超宽行（E-OVERFLOW-GUARD 语义），正常行
            #   保持身份短路。截断仅对超宽行执行（缓存重建时标记 all_ok=False；
            #   reflow 修复后缓存重建 all_ok=True 恢复零开销路径）。
            prefix_ok = prefix_info[2] if len(prefix_info) > 2 else True
            if not prefix_ok:
                from .helpers import truncate_line
                prefix = [
                    truncate_line(ln, width) if ln.width > width else ln
                    for ln in prefix
                ]
            if committed_box is not None and committed_box.y == 0 and prefix_ok:
                tail_start = min(len(prefix), len(canvas))
                tail = [_to_line(r) for r in canvas[tail_start:]]
                # ★ 稳定前缀（PERF-7）：prefix 为复用列表对象（``_committed_prefix``
                # 缓存命中），标记 stable_prefix 使 ``first_diff_line`` 跳过前缀
                # 区间（避免大文档每帧全量逐行 is 比较）。
                return Frame(
                    prefix + tail,
                    stable_prefix=prefix,
                    stable_prefix_offset=0,
                    stable_prefix_len=len(prefix),
                )
            # 非顶部 / 前缀含超宽行：前缀行填入画布对应区域（_paint 命中缓存
            # 已跳过画布写入；超宽行已截断，正常行身份保持）。
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
            header = [_to_line(r) for r in canvas[:y0]]
            tail = [_to_line(r) for r in canvas[y0 + fit:]]
            # ★ 稳定前缀（PERF-7）：prefix 为复用列表对象（缓存命中），其
            #   ``[:fit]`` 部分在 Frame.lines 的 [y0, y0+fit) 区间——标记
            #   stable_prefix 使 ``first_diff_line`` 跳过该区间（前缀区间外
            #   的 header/tail 行仍逐行比较）。防御：fit 可能 < len(prefix)
            #   （reflow 布局陈旧），stable_prefix_len 用实际覆盖 fit。
            return Frame(
                header + prefix[:fit] + tail,
                stable_prefix=prefix,
                stable_prefix_offset=y0,
                stable_prefix_len=fit,
            )
    return Frame(_to_line(row) for row in canvas)


__all__ = ["render_frame"]
