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
from collections import deque

from .fiber import Fiber
from .layout import layout_tree, wrap_text_lines, _skip_function
from .output import Frame, Line
from ._layout_transform import _translate_subtree_x, _translate_subtree_y
from ._paint_canvas import (
    _merge_line,
    _slice_line_with_offset,
    _span_at,
    _canvas_row_to_line,
    _ensure_row_dict,
    _entry3,
    _line_as_dict,
)
from src._text_width import grapheme_clusters
from src.tui._width import wcswidth_simple
from ._paint_border import (
    _paint_border,
    _paint_box_background,
    _merge_inherit_bg,
    _apply_bg_to_line,
    _shared_style,
)
# ★ 性能（渲染热路径）：host 注册表查询提升为模块级导入——修复前
#   ``_paint_impl`` 在函数体内 ``from .registry import get_host``，容器绘制
#   每帧执行上百次模块查找。``registry`` 仅依赖标准库（无循环导入）。
from .registry import get_host
# ★ 性能（2026-10-23）：helpers / truncate_line 提升为模块级导入——修复前
#   TEXT 绘制与容器背景分支在函数体内 import（每个未命中换行缓存的 TEXT /
#   每个容器每帧一次 import 语句），大组件树下是可感知的固定开销。
#   helpers 依赖 output/_runs_utils/_style_utils，不反向依赖 components，无环。
from .helpers import (
    _parse_color,
    apply_text_transform,
    resolve_text_style,
    truncate_line,
    wrap_runs_by_width,
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
    """绘制一个 host fiber 子树到画布（迭代入口）。

    ★ 2026-10-23（深嵌套修复）：递归 ``_paint → _paint_impl → _paint_children
    → _paint`` 改为 ``_paint_tree`` 显式栈迭代——深层组件树不再触发
    ``RecursionError``。异常隔离提升到节点级：单节点异常记录结构化日志 +
    绘制可见占位（不再静默缺失），兄弟/父容器继续绘制。

    Args:
        fiber: host fiber。
        canvas: 画布（行列表）。
        clip: 裁剪区域 (x, y, w, h)；None 表示不裁剪。
        inherit_bg: 继承的背景样式（None=无；Box backgroundColor 传递）。
    """
    _paint_tree(fiber, canvas, clip, inherit_bg)


def _box_repr(box) -> str:
    """布局盒调试表示（结构化日志用）。"""
    if box is None:
        return "None"
    return f"({getattr(box, 'x', 0)},{getattr(box, 'y', 0)},{getattr(box, 'w', 0)},{getattr(box, 'h', 0)})"


def _paint_placeholder(fiber: Fiber, canvas: list, clip, text: str) -> None:
    """在节点布局盒首行绘制可见占位（异常/深度截断）。"""
    box = fiber.layout_box
    if box is None or int(getattr(box, "w", 0) or 0) <= 0:
        return
    row = int(getattr(box, "y", 0) or 0)
    if clip is not None:
        _cx, cy, cw, ch = clip
        if cw <= 0 or ch <= 0 or row < cy or row >= cy + ch:
            return
    if not (0 <= row < len(canvas)):
        return
    width = max(1, int(getattr(box, "w", 0) or 0))
    line = Line()
    line.append(text[:width], _placeholder_style())
    x = max(0, int(getattr(box, "x", 0) or 0))
    canvas[row] = _merge_line(canvas[row], x, line)


def _placeholder_style():
    """占位文本样式（红色粗体；延迟 import 避免模块顶部新增依赖）。"""
    from src.tui.core.style import Style

    return Style(fg=196, bold=True)


def _paint_tree(
    fiber: Fiber, canvas: list[dict], clip=None, inherit_bg=None, *,
    _skip_float_root: bool = True,
) -> None:
    """迭代绘制子树（显式栈，O(节点数)、无递归深度限制）。

    ``_skip_float_root=False`` 供浮窗合成层使用：允许绘制**根节点自身**
    是浮窗 host 的窗口（嵌套浮窗仍跳过）。
    """
    stack: list[tuple[Fiber, object, object]] = [(fiber, clip, inherit_bg)]
    first = True
    while stack:
        node, node_clip, node_bg = stack.pop()
        # ★ 浮窗只经合成层绘制（2026-10-21 修复）：整树绘制阶段一律跳过浮窗
        #   host。``is not None``（不是真值判定）：空串 id 也是合法浮窗标记。
        if node.props.get("dataFloatWindow") is not None:
            if not (first and not _skip_float_root):
                first = False
                continue
        first = False
        # ★ 2026-10-23（深嵌套修复）：超限子树显示可见占位（_measure 返回零盒）。
        if getattr(node, "_depth_overflow", False):
            _paint_placeholder(node, canvas, node_clip, "[depth truncated]")
            continue
        try:
            child_tasks = _paint_impl(node, canvas, node_clip, node_bg) or []
        except Exception as exc:
            # ★ 2026-10-23（异常可观测性）：保留降级（单节点失败不影响整帧），
            #   但不再静默——结构化日志 + 可见占位。
            node._paint_error = f"{type(exc).__name__}: {exc}"
            _logger.error(
                "%s paint 异常（已显示占位）",
                getattr(node, "type", "?"),
                exc_info=True,
                extra={
                    "ink_fiber_type": getattr(node, "type", "?"),
                    "ink_fiber_key": getattr(node, "key", ""),
                    "ink_box": _box_repr(node.layout_box),
                    "ink_error": node._paint_error,
                },
            )
            _paint_placeholder(
                node, canvas, node_clip, f"[render error: {type(exc).__name__}]",
            )
            continue
        if child_tasks:
            stack.extend(reversed(child_tasks))


def _paint_impl(fiber: Fiber, canvas: list[dict], clip=None, inherit_bg=None) -> list:
    """绘制单个 host fiber（不递归），返回待绘制的子任务列表。

    子任务为 ``(host_fiber, clip, inherit_bg)``——由 ``_paint_tree`` 按文档
    顺序迭代处理（逆序压栈）。异常向上抛给 ``_paint_tree`` 统一记录/占位。
    """
    box = fiber.layout_box
    if box is None:
        return []
    ftype = fiber.type
    # display: none（完善 react ink）——隐藏组件不绘制（_measure 已返回零盒）。
    if fiber.props.get("display") == "none":
        return []

    if ftype == "text":
        # ★ BUG-17（review 方向）：零宽/零高 TEXT 不绘制——``_measure`` 中
        #   ``width==0 and not fill`` 返回 h=0（"零宽非 fill 子节点不占位"），
        #   但 ``_wrapped_lines`` 非空（``wrap_runs_by_width(runs, 0)`` 返回
        #   单行）→ 修复前文本照常绘制到画布（row 剩余宽度 0 的子节点文本
        #   溢出容器，如宽 3 的 row 内 "abc"+"def" 渲染出 "abcdef"）。
        if box.w <= 0 or box.h <= 0:
            return []
        # ★ 复用 layout 阶段缓存的换行结果（免二次包裹）
        wrapped = fiber._wrapped_lines
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
            styled = fiber.props.get("styled")
            if styled is not None:
                # ★ P3-9（review 方向）：styled 非 None 时按 runs 直接换行，
                #   不计算 text/style（修复前 apply_text_transform/
                #   resolve_text_style 结果在 styled 分支未使用——冗余计算，
                #   每帧浪费一次字符串变换与样式解析）。
                text_wrap = fiber.props.get("textWrap")
                if text_wrap is None:
                    text_wrap = fiber.props.get("wrap", "wrap")
                lines = wrap_runs_by_width(list(styled), box.w, hard=(text_wrap == "hard"))
            else:
                # ★ Box 背景继承（完善 react ink v6）：子 TEXT 未指定自身
                #   backgroundColor 时继承父 Box 背景色（_merge_inherit_bg）。
                transform = fiber.props.get("transform")
                text = apply_text_transform(
                    str(fiber.props.get("children", "")), transform,
                )
                style = resolve_text_style(fiber.props)
                text_wrap = fiber.props.get("textWrap")
                if text_wrap is None:
                    text_wrap = fiber.props.get("wrap", "wrap")
                lines = wrap_text_lines(
                    text, box.w, _merge_inherit_bg(style, inherit_bg),
                    hard=(text_wrap == "hard"),
                )
        # ★ P0 修复：绘制必须服从布局盒——此前 Text 把 ``_wrapped_lines``
        #   全量写入画布，固定高度/flexShrink/flexBasis 使 box.h/box.w 缩小后，
        #   正文仍按旧换行结果绘制，覆盖父边框/后续兄弟。垂直裁剪到 box.h，
        #   水平裁剪到 box.w（正常布局行宽已 <= box.w，只有尺寸被外部覆盖时
        #   才触发重建）。
        if len(lines) > box.h:
            lines = lines[:box.h]
        if box.w > 0 and any(line.width > box.w for line in lines):
            lines = [
                truncate_line(line, box.w) if line.width > box.w else line
                for line in lines
            ]
        # 行级复用：canvas 行直接写入 Line 对象（box.x==0 快路径），diff 阶段
        # 身份短路（同 Line 对象恒相等）跳过——跨帧零重建。不再维护
        # ``_paint_cache``（死缓存：只写不读，实际复用来自 _wrapped_lines
        # 引用与 canvas 行 Line 身份，方向3 移除）。
        # ★ overflow 裁剪（完善 react ink v6）：clip 非 None 时按裁剪区域
        #   限制行/列范围（垂直：行号在 [cy, cy+ch) 外跳过；水平：line 与
        #   [cx, cx+cw) 求交并切片）。
        if clip is not None:
            cx, cy, cw, ch = clip
            # ★ P3（review）：裁剪行列数为常量条件，提到循环外一次判断
            #   （修复前每行重复判断与行无关的 ``cw <= 0 or ch <= 0``）。
            if cw <= 0 or ch <= 0:
                return []
            for i, line in enumerate(lines):
                row = box.y + i
                if row < cy or row >= cy + ch:
                    continue
                if box.x >= cx + cw:
                    continue
                s = max(box.x, cx)
                e = min(box.x + line.width, cx + cw)
                if s >= e:
                    continue
                if s != box.x or e != box.x + line.width:
                    # ★ P2（review 修复）：宽字符横跨左裁剪边界时整体保留（见
                    #   ``_slice_run_text``）——修复前仍按 ``s`` 绘制，导致该字符
                    #   及其后内容右移 1 列（尾部越界覆盖相邻单元格）。现取
                    #   切片返回的绘制偏移（0 / -1），宽字符落到真实列。
                    line, slice_offset = _slice_line_with_offset(
                        line, s - box.x, e - box.x,
                    )
                    draw_x = s + slice_offset
                else:
                    draw_x = s
                if 0 <= row < len(canvas):
                    canvas[row] = _merge_line(canvas[row], draw_x, line)
            return []
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
        return []

    if ftype == "spacer":
        return []  # 空行已由画布预置

    if ftype == "fragment":
        # 透明分组容器：直接绘制子节点（无独立 box——layout_children 已将
        # fragment 扁平化；本分支为防御，覆盖 fragment 被直接调度的路径）
        return _child_tasks(fiber, clip, inherit_bg)

    # ── 自定义 host（注册表） ──
    host = get_host(ftype)
    if host is not None:
        paint_fn = host[1]
        # ★ 2026-10-23（异常可观测性）：不再在此吞掉异常——抛给 _paint_tree
        #   统一记录结构化日志并绘制可见占位（修复前静默跳过该节点内容）。
        paint_fn(fiber, canvas)
        return []

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
    bg_prop = fiber.props.get("backgroundColor")
    bg_style = None
    if bg_prop is not None:
        bg_color = _parse_color(bg_prop)
        if bg_color is not None:
            # ★ 2026-10-23：共享样式实例（同值跨帧同一对象）——画布行 base
            #   快照比较/行级比较命中身份短路（见 _paint_border._shared_style）。
            bg_style = _shared_style(
                None, bg_color, False, False, False, False, False, False,
            )
            _paint_box_background(box, canvas, bg_style, clip)
    child_bg = bg_style if bg_style is not None else inherit_bg
    # overflow 裁剪（完善 react ink v6）：容器有 overflow hidden 时压入
    # 裁剪区域，子节点绘制受限（_resolve_clip 处理父裁剪合并）。
    new_clip = _resolve_clip(fiber, box, clip)
    return _child_tasks(fiber, new_clip, child_bg)


def _child_tasks(fiber: Fiber, clip=None, inherit_bg=None) -> list:
    """收集直接 host 子任务（跳过 function 链、扁平化 fragment，迭代无递归）。

    返回 ``[(host_fiber, clip, inherit_bg), ...]``，保持文档顺序；由
    ``_paint_tree`` 逆序压栈后按序绘制。
    """
    tasks: list[tuple[Fiber, object, object]] = []
    pending: deque[Fiber] = deque()
    child = fiber.child
    while child is not None:
        pending.append(child)
        child = child.sibling
    while pending:
        node = pending.popleft()
        host = _skip_function(node)
        if host is None:
            continue
        if host.is_host and host.type == "fragment":
            c = host.child
            while c is not None:
                pending.append(c)
                c = c.sibling
        else:
            tasks.append((host, clip, inherit_bg))
    return tasks


def _paint_children(fiber: Fiber, canvas: list, clip=None, inherit_bg=None) -> None:
    """绘制 fiber 的直接 host 子节点（兼容入口，内部走 ``_paint_tree``）。"""
    for task in _child_tasks(fiber, clip, inherit_bg):
        _paint_tree(*task)


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
    if not root._committed_chat_present:
        return None
    cached = root._committed_chat_cache
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
        root._committed_chat_cache = None
    return found


def _to_line(row, width: int) -> Line:
    """画布行转 Line（行宽不变量 E-OVERFLOW-GUARD：超宽行截断到 width）。

    布局层异常（嵌套容器内容超宽/宽字符硬塞等导致行宽超文档宽）时，行级
    截断保证**行宽恒 <= width**——行级 diff 模型依赖该不变量（超宽行会
    破坏 diff/光标定位）。截断重建 Line 对象（身份短路失效），仅异常行
    触发（正常布局行宽 <= width，原样返回零开销）。

    ★ P3（review）：由 ``render_frame`` 内闭包提升为模块级函数——修复前
    每次 ``render_frame`` 都新建闭包对象（每帧一次分配）。
    """
    line = _canvas_row_to_line(row)
    if line.width > width:
        return truncate_line(line, width)
    return line


def _to_lines(
    rows, width: int, start: int = 0,
    prev_cache: dict | None = None, new_cache: dict | None = None,
) -> list:
    """批量画布行转 Line，并复用上一帧内容完全相同的行（2026-10-23）。

    ★ 性能动机：画布行若为 ``dict``（边框/背景/自定义 host 增量合并的产物），
    ``_canvas_row_to_line`` 每帧都要排序键 + 逐字符重建 Line——即使内容一字
    未变。重建出的新 Line 又使行级 diff 的身份短路失效，未变化行退化为
    run 内容比较。本函数以「行索引 + 行值相等 + 渲染宽度相同」为条件复用
    上一帧的 Line：

      - 内容相等（``prev_row == row`` 或同一 Line 对象）→ 两帧渲染结果
        逐字符/样式相同，直接复用旧 Line（跨帧同一对象 →
        ``first_diff_line`` O(1) 命中）；
      - 宽度不同（resize / 布局变化）→ 必须重新转换并按新宽度截断；
      - 缓存只记录**非 Line 行**（dict/None）：Line 行跨帧多为同一对象，
        身份短路已足够；若每行都缓存，大文档下缓存规模会变成 O(文档行数)
        （每帧数千元组），得不偿失。缓存每帧新建（调用方传入空
        ``new_cache``），旧缓存自然丢弃 → 自动修剪，不会随历史帧无限增长。

    为什么可以安全复用：``_canvas_row_to_line`` 是纯函数（输入行值 → 输出
    Line），Line 在帧构建完成后只读（``Line.append`` 是唯一修改入口，
    渲染管线不修改已产出的帧行）；``dict`` 行在本函数返回后不再被改写。

    Args:
        rows: 画布行序列（None / Line / dict）。
        width: 文档宽度（超宽行截断）。
        start: ``rows[0]`` 对应的文档行号（缓存键）。
        prev_cache: 上一帧缓存（行号 → ``(row, width, line)``）。
        new_cache: 本帧缓存输出（行号 → ``(row, width, line)``）。

    Returns:
        ``list[Line]``，与 ``rows`` 等长。
    """
    out: list = []
    idx = start
    for row in rows:
        if isinstance(row, Line):
            # Line 行不写入缓存（见 docstring）：直接复用当前对象——大文档
            # （数千行 Text）下避免每行一次缓存字典查找。
            # ★ 2026-10-23（行宽不变量 E-OVERFLOW-GUARD 补全）：修复前 Line
            #   行**直通不校验宽度**——自定义 host（StaticLines/LiveLines）
            #   若因数据与布局瞬时不一致绘制了超宽行，帧行宽 > 终端宽度，
            #   终端自动换行使整屏下移一行（上屏内容会短暂落到下屏分割线
            #   行），下一帧重写才恢复（用户报障「流式输出偶发写到下屏最顶
            #   分割线、闪一下就好」）。超宽才截断（正常行保持身份复用，
            #   零额外开销）。
            if width > 0 and row.width > width:
                row = truncate_line(row, width)
            out.append(row)
            idx += 1
            continue
        prev = prev_cache.get(idx) if prev_cache is not None else None
        if prev is not None and prev[1] == width:
            prev_row = prev[0]
            # 同对象或值相等（字典行/边框行每帧重建、内容未变）→ 复用上一帧
            # Line；行级 diff 因此命中身份短路。
            if prev_row is row or prev_row == row:
                line = prev[2]
                if new_cache is not None:
                    new_cache[idx] = (row, width, line)
                out.append(line)
                idx += 1
                continue
        line = _to_line(row, width)
        if new_cache is not None:
            new_cache[idx] = (row, width, line)
        out.append(line)
        idx += 1
    return out


def _fill_prefix_rows(canvas: list, prefix: list, y0: int, width: int) -> None:
    """把缓存前缀行填回画布（前缀长度与布局高度不一致时的回退路径）。

    ``render_frame`` 的 committed 前缀拼接假设「缓存前缀行数 == 该 host 的
    布局高度」——此时 ``_paint`` 走前缀快路径**不写画布**，帧 = 顶部画布行 +
    前缀 + 尾部画布行。一旦两者不一致（布局高度与前缀长度瞬时不同步），直接
    拼接会让尾部整体错位：上屏内容整体下/上移一行，下屏顶部（状态栏上方
    分割线）会短暂显示上屏内容，下一帧才恢复（2026-10-23 用户报障：流式
    输出偶发写到下屏最顶的分割线、闪一下就好）。

    本函数在前缀不可用时把前缀行按 ``y0`` 起填回画布（超宽行按 E-OVERFLOW-
    GUARD 截断），随后 ``render_frame`` 走**全量画布路径**——帧行数与布局严格
    一致，内容零丢失。

    Args:
        canvas: 画布（行列表；可含 None / dict / Line）。
        prefix: 缓存前缀行（``list[Line]``）。
        y0: 前缀首行的文档行号（0-based）。
        width: 文档宽度（<=0 时不做宽度守卫）。
    """
    start = max(0, int(y0 or 0))
    limit = len(canvas)
    for i, line in enumerate(prefix):
        row = start + i
        if row >= limit:
            break
        ln = line
        if width > 0 and ln.width > width:
            ln = truncate_line(ln, width)
        if canvas[row] is None:
            canvas[row] = ln
        else:
            canvas[row] = _merge_line(canvas[row], 0, ln)


def _has_floating_layer(root: Fiber) -> bool:
    """组件树中是否存在浮窗（``_has_float_present`` 标志，沿 return 链查找）。

    ``_measure`` 在 ROOT fiber 上置位；``render_frame`` 的调用方可能传入
    ROOT 或 host 根——向上查找保证两种入口一致。
    """
    node: Fiber | None = root
    while node is not None:
        if getattr(node, "_has_float_present", False):
            return True
        node = node.return_
    return False


def _apply_float_positions(root: Fiber, width: int,
                           viewport_height: int = 0,
                           *, doc_height: int | None = None) -> None:
    """布局完成后修正浮窗坐标（幂等；异常只记 debug，不影响主界面）。

    ★ 2026-10-21（公共渲染 API 缺浮窗定位修复）：此前只有 InkSession 的渲染
    帧调用 ``apply_floating_layout``——``renderToString`` / ``render()`` 独立
    会话渲染 App 树时浮窗从不定位，停在根内容区左上角（压住标题栏），隐藏
    规则也不生效。现由 ``render_frame`` 统一调用，所有帧构建入口一致。
    ``apply_floating_layout`` 的目标坐标只由 props 与文档几何决定，重复调用
    位移恒为 0（幂等），调用方额外再调一次也安全。

    ``doc_height`` 透传文档总行数覆盖值（流式补白后重定位用，见
    ``render_frame``）。
    """
    if not _has_floating_layer(root):
        return
    from ._floating import apply_floating_layout

    try:
        apply_floating_layout(
            root, width, viewport_height=viewport_height,
            doc_height=doc_height,
        )
    except Exception:  # pragma: no cover - 防御：单帧失败不影响主界面
        _logger.debug("浮窗定位异常（本帧保持原坐标）", exc_info=True)


def _run_text_width(text: str) -> int:
    """文本显示宽度（ASCII 可打印快路径：宽度 == 字符数）。"""
    if text.isascii() and text.isprintable():
        return len(text)
    return wcswidth_simple(text)


def _leading_zero_width_prefix(text: str) -> int:
    """返回 ``text`` 开头连续零宽字形簇的**字符数**（无则 0）。

    零宽字形簇（组合重音、变体选择符、ZWJ 等）不占显示列，ANSI 样式切换会
    把它们拆到新 run 的开头——切片时必须单独保留（见 ``_append_line_range``）。
    首字符为 ASCII 时直接返回 0（组合标记不在 ASCII 范围，免逐簇扫描）。
    """
    if not text or text[0].isascii():
        return 0
    length = 0
    for cluster in grapheme_clusters(text):
        if wcswidth_simple(cluster) != 0:
            break
        length += len(cluster)
    return length


def _append_line_range(out: Line, line: Line, start: int, end: int) -> None:
    """把 ``line`` 的 ``[start, end)`` 显示列**严格**追加到 ``out``（O(runs)）。

    性能关键（2026-10-21 浮窗合成重写）：只按 run 级切片（``_slice_run_text_strict``
    + ASCII 快路径），**不**把整行转成列键字典、也不重建整行 Line——修复前
    ``_merge_window_row`` 对每个窗口行做一次 ``_line_as_dict(base_line)``
    （整行逐列建 dict）+ ``_canvas_row_to_line``（整行重建 + 逐字符测宽），
    120 列终端 + 7 行浮窗实测 ~1.6ms/帧（占合成层 2/3），行宽越宽越慢。

    宽字符横跨 ``start`` / ``end`` 边界时**整字丢弃**：被丢弃的列在 ``out``
    中以空格留白（调用方据此对齐），避免半个宽字符占用相邻片段首列导致
    其后内容整体错位。

    Args:
        out: 目标 Line（原地追加）。
        line: 源行。
        start: 起始显示列（含）。
        end: 结束显示列（不含）。
    """
    from ._paint_canvas import _slice_run_text_strict

    col = 0
    written = 0  # 已追加内容相对 start 的列数
    for run in line.runs:
        run_end = col + run.width
        text = run.text
        # 前导零宽字形簇（组合标记 / ZWJ 等，ANSI 样式切换会把它拆到新 run
        # 开头）不占列、依附**前一个**字符：只有 run 起点 = 已追加内容的终止列
        # （``start + written``）时才真正紧跟前一个**被保留**的字符 → 输出；
        # 否则（边界宽字符被裁掉 / 段首与前段不相邻）丢弃——避免它错附到
        # 别的字符上。无论保留与否都从 run 文本中摘掉，防止后续切片再次带上。
        if text and end > start and col <= end:
            lead = _leading_zero_width_prefix(text)
            if lead:
                if start + written == col:
                    out.append(text[:lead], run.style, run.link)
                text = text[lead:]
        if run_end <= start:
            col = run_end
            continue
        if col >= end:
            break
        s = max(start, col) - col
        e = min(end, run_end) - col
        text, offset = _slice_run_text_strict(text, s, e)
        if text:
            target = max(start, col) + offset - start
            if target > written:
                # 边界宽字符被丢弃 → 按真实列补空格（对齐文档列）
                out.append(" " * (target - written))
                written = target
            out.append(text, run.style, run.link)
            written += _run_text_width(text)
        col = run_end


def _merge_window_row(base_line, rowdata, win_x: int, win_w: int,
                      width: int, opaque: bool):
    """把浮窗一行合成到底层帧行，返回新 Line（无内容返回 None）。

    合并语义（2026-10-21 重写，行为保持）：
      - **不透明窗口**（``backgroundColor`` 非 None）：矩形整段占用——矩形内
        底层字符全部清除，再由窗口单元格覆盖（背景铺满整段）；
      - **透明窗口**（``backgroundColor is None``）：只占用**实际绘制了字符
        的列**——空白列保留底层原内容（``background=None`` 语义）；
      - 宽字符与占用列重叠时整字清除（终端无法只覆盖宽字符的某一列）。代价
        是窗口外与窗口相邻的那一列会变成空白（终端能力限制，见
        ``app.floating`` docstring）。

    **性能（2026-10-21 用户需求：优化有浮动窗口时的渲染性能）**：只处理
    窗口覆盖的列区间 ``[win_x, win_x + win_w)``——左/右段经
    ``_append_line_range`` 按 run 级严格切片直接追加（不建整行 dict、不逐列
    测宽）；中段宽度 ≤ 窗口宽（典型 30~40 列），dict 化/回填只发生在这段。
    修复前每个窗口行都要对**整行**做 ``_line_as_dict`` + 逐列
    ``wcswidth_simple`` + ``_canvas_row_to_line`` 全量重建，代价随终端宽度
    线性增长（120 列终端实测合成层 2.3ms/帧，其中合并 1.6ms）。

    Args:
        base_line: 底层帧行（Line）。
        rowdata: 浮窗画布行（列键为**窗口局部列**——画布已平移使窗口位于
            原点；元素为 ``(ch, style, link)`` 或 2 元组）。
        win_x: 窗口左边界（文档列）。
        win_w: 窗口宽度（列）。
        width: 文档宽度（合并结果按此钳制，维护行宽不变量）。
        opaque: 窗口是否不透明。

    Returns:
        合并后的 Line；窗口在本行没有任何单元格时返回 None（该行不变）。
    """
    cells: dict = {}
    rowdict = _ensure_row_dict(rowdata)
    for col, value in rowdict.items():
        try:
            local = int(col)
        except (TypeError, ValueError):
            continue
        if local < 0 or local >= win_w:
            continue
        screen_col = win_x + local
        if screen_col < 0 or screen_col >= width:
            continue  # 出屏列丢弃（终端写不到，也与行宽不变量冲突）
        cells[screen_col] = value
    if not cells:
        return None

    # 中段（窗口覆盖的可见列）——窗口部分出屏时按可见区间裁剪
    mid0 = max(0, win_x)
    mid1 = min(width, win_x + win_w)
    if mid1 <= mid0:
        return None

    # ── 段边界（透明窗口按字符整字对齐）──────────────────────────────
    # 透明窗口只占用「实际绘制了字符」的列：底层宽字符横跨矩形边界但**未**
    # 与任何窗口字符重叠时必须完整保留（修复前按矩形边界硬切 → 该宽字符
    # 整字消失，底层内容丢失）。因此把中段边界外扩到跨越边界的字符起点/终点；
    # 该字符若与窗口字符重叠，仍会在下方的占用列清理中被整字删除。
    seg0 = mid0
    seg1 = mid1
    if not opaque:
        span = _span_at(base_line, mid0)
        if span is not None and span[0] < mid0:
            seg0 = span[0]
        span = _span_at(base_line, mid1)
        if span is not None and span[0] < mid1:
            seg1 = span[0] + span[1]
    # 窗口字符为宽字符且起点在矩形右边界前一列时，其第二列越过 mid1——
    # 右段必须从该字符之后开始（否则底层残余字符与宽字符抢占同一列）。
    cut1 = seg1
    for col, value in cells.items():
        ch = _entry3(value)[0]
        cw = wcswidth_simple(ch) or 1
        if col < cut1 < col + cw:
            cut1 = col + cw
    cut1 = min(width, cut1)
    region_w = cut1 - seg0

    if opaque:
        middict: dict = {}
    else:
        # 透明：只取中段底层内容（≤ 窗口宽 + 边界字符），逐列判断是否与窗口
        # 字符重叠
        base_mid = Line()
        _append_line_range(base_mid, base_line, seg0, seg1)
        middict = _line_as_dict(base_mid)
        occupied: set = set()
        for col, value in cells.items():
            ch = _entry3(value)[0]
            cw = wcswidth_simple(ch) or 1
            occupied.update(range(col - seg0, col - seg0 + cw))
        for bcol in list(middict):
            ch = _entry3(middict[bcol])[0]
            bw = wcswidth_simple(ch) or 1
            if any((bcol + i) in occupied for i in range(bw)):
                del middict[bcol]

    for col in sorted(cells):
        value = cells[col]
        ch = _entry3(value)[0]
        local = col - seg0
        if (wcswidth_simple(ch) or 1) > 1:
            # 宽字符只写首列键；第二列若有底层残留必须清掉（否则行宽推导
            # 把宽字符按 1 列处理，后续内容错位）。
            middict.pop(local + 1, None)
        middict[local] = value
    mid_line = _canvas_row_to_line(middict)

    merged = Line()
    _append_line_range(merged, base_line, 0, seg0)
    if merged.width < seg0:
        # 左边界宽字符被整字丢弃 → 留白到窗口起点
        merged.append(" " * (seg0 - merged.width))
    # 右段是否真有内容：cut1 处的字符若为跨越边界的宽字符则从**其结束列**
    # 起算（该字符已被窗口占用/裁掉）；结束列等于行宽时说明右段为空——
    # 此时补齐空格只会给行尾加无意义空白（改变 Line.width，无视觉收益）。
    span = _span_at(base_line, cut1)
    right_start = cut1
    if span is not None and span[0] < cut1:
        right_start = span[0] + span[1]
    has_right = span is not None and right_start < base_line.width
    for run in mid_line.runs:
        merged.append(run.text, run.style, run.link)
    if has_right and mid_line.width < region_w:
        # 中段未被窗口单元格铺满（窗口透明/内容较短）→ 在**右侧**补齐到中段
        # 结束列，保证右段仍落在文档列 cut1 上（跨界宽字符的留白由
        # ``_append_line_range`` 在右段内部补齐）。
        merged.append(" " * (region_w - mid_line.width))
    if has_right:
        _append_line_range(merged, base_line, cut1, 10**9)

    if merged.width > width:
        # ★ 行宽不变量兜底（E-OVERFLOW-GUARD）：窗口贴右边界时有宽字符跨越
        #   终端右边界（终端不可见的半列）→ 截断，保证 diff/光标定位正确。
        merged = truncate_line(merged, width)
    return merged


def _overlay_floating(root: Fiber, frame: Frame, width: int) -> Frame:
    """把浮窗层合成到最终帧（覆盖 committed 前缀复用区域）。

    ★ 为什么在 render_frame 末尾重新合成（2026-10-21 用户需求）：committed
    历史前缀路径直接复用缓存前缀行（``prefix[:fit]``）并**丢弃**画布中对应
    区域的绘制——浮窗若覆盖到 committed 区（短文档 / 小历史时常见），首轮
    ``_paint`` 的浮窗内容会被前缀拼接覆盖，只显示窗口下半部分。本函数在
    帧拼装完成后，把浮窗子树重新绘制并逐行合并——窗口完整、关闭后前缀行
    原样恢复（下一帧无浮窗 / 无前缀复用 → 直接返回原 frame）。

    性能：画布只按**窗口可见矩形**分配（``[None] * visible_h``）——绘制前把
    浮窗子树临时平移到原点、绘制完立即平移回来，因此绘制/合并都是 O(浮窗
    可见面积)，与帧行数无关（修复前每窗每帧分配 ``[None] * 帧行数``，长文档
    下是 O(文档) 的额外分配）。与 committed 前缀区间相交时**只让被覆盖的
    行退出稳定前缀**（保留前缀起点到首个被覆盖行之间的复用区间，
    ``stable_prefix_len`` 收缩），避免整段前缀每帧重新逐行比较。

    ★ 输出历史（2026-10-21 修复）：返回的 Frame 携带 ``base_lines``（合并前
    的底层行）——``renderer._emit_content_lines`` 用它取内容行，浮窗覆盖
    committed 区时 scrollback 不再被窗口边框污染。
    """
    if not _has_floating_layer(root):
        return frame
    # ★ 每帧只做一次浮窗收集（2026-10-21 性能优化）：``render_frame`` 内的
    #   ``_apply_float_positions`` 刚刚完成同一遍 DFS（``apply_floating_layout``
    #   的 ``_collect_float_fibers(include_hidden=True)``）并把结果写回
    #   ``root._float_frame_windows``——这里直接复用，修复前每帧对整棵组件树
    #   做**两遍**完全相同的前序 DFS（浮窗开启时每帧白白多扫上千 fiber）。
    #   缓存由 ``render_frame`` 在入口置 None 保证不跨帧陈旧；独立调用方
    #   （测试/外部直接调本函数）未经过 render_frame 时回退现场收集。
    windows = getattr(root, "_float_frame_windows", None)
    if windows is None:
        from ._floating import _collect_float_fibers

        windows = _collect_float_fibers(root)
    if not windows:
        return frame
    base_lines = frame.lines
    lines = list(base_lines)
    touched: list[int] = []
    # ★ 逐窗口按**矩形列范围**合并（2026-10-21 用户反馈修复：同一行只显示
    #   浮窗内容、左侧/右侧原内容丢失）。只把窗口自己的单元格合并到对应列，
    #   窗口外原内容原样保留（见 ``_merge_window_row``）。
    for fiber in windows:
        # ★ 隐藏窗口不绘制（2026-10-21）：复用的每帧收集结果含隐藏窗口
        #   （``include_hidden=True``），这里按布局阶段写入的 ``_float_hidden``
        #   过滤——与原 ``_collect_float_fibers()`` 默认口径一致。
        if getattr(fiber, "_float_hidden", False):
            continue
        box = fiber.layout_box
        if box is None or box.w <= 0 or box.h <= 0:
            continue
        win_x = int(box.x)
        win_y = int(box.y)
        win_w = int(box.w)
        # ★ 可见高度/源行（2026-10-21 尺寸约束）：布局阶段按 max_height /
        #   底部框 / 可视区裁剪，这里只绘制可见带——超出部分不画、不合并。
        win_h = int(getattr(fiber, "_float_visible_h", box.h) or 0)
        src_row = int(getattr(fiber, "_float_src_row", 0) or 0)
        if win_h <= 0:
            continue
        if win_x >= width or win_x + win_w <= 0:
            continue  # 完全在屏幕右侧外
        # ★ O(窗口面积)：临时平移到原点画到 h 行小画布，再平移回去（异常也
        #   恢复坐标——``layout_box`` 必须保持文档坐标供下一帧继续使用）。
        canvas: list = [None] * win_h
        try:
            if win_x:
                _translate_subtree_x(fiber, -win_x)
            if win_y + src_row:
                _translate_subtree_y(fiber, -(win_y + src_row))
            try:
                # ★ 直接绘制窗口自身（``_skip_float_root=False``）：主树绘制
                #   会跳过浮窗 host，这里**要**绘制窗口自身；窗口内容里的
                #   嵌套浮窗仍跳过（_paint_tree 对非根节点一律跳过）。
                _paint_tree(fiber, canvas, _skip_float_root=False)
            finally:
                if win_y + src_row:
                    _translate_subtree_y(fiber, win_y + src_row)
                if win_x:
                    _translate_subtree_x(fiber, win_x)
        except Exception:
            _logger.warning(
                "浮窗合成绘制异常 id=%r",
                fiber.props.get("dataFloatWindow"), exc_info=True,
            )
            continue
        opaque = fiber.props.get("backgroundColor") is not None
        for i in range(win_h):
            row = win_y + i
            if row < 0 or row >= len(lines):
                continue
            rowdata = canvas[i]
            if not rowdata:
                continue
            merged = _merge_window_row(
                lines[row], rowdata, win_x, win_w, width, opaque,
            )
            if merged is None:
                continue
            lines[row] = merged
            touched.append(row)
    if not touched:
        return frame
    prefix = frame._stable_prefix
    offset = frame._stable_prefix_offset
    plen = frame._stable_prefix_len
    if prefix is not None:
        start, end = offset, offset + plen
        covered = [row for row in touched if start <= row < end]
        if covered:
            # 只保留「前缀起点 → 首个被覆盖行」这一段仍然有效的复用区间；
            # 被覆盖行及其之后退出身份短路（逐行比较，正确性优先）。
            first = min(covered)
            plen = first - start
            if plen <= 0:
                prefix = None
                offset = 0
                plen = 0
    return Frame(
        lines,
        stable_prefix=prefix,
        stable_prefix_offset=offset,
        stable_prefix_len=plen,
        base_lines=base_lines,
    )


def render_frame(root: Fiber, width: int, viewport_height: int = 0,
                 *, stream_pad=None) -> Frame:
    """渲染布局好的 host 树为整帧 Frame。

    浮窗定位（``apply_floating_layout``）在本函数内统一执行——所有帧构建
    入口（InkSession / renderToString / render()）因此共用同一套定位语义。

    ``stream_pad``（可选，2026-10-23「/loop 后流式抖动」修复）：流式高度
    锁定的**构建期补白**钩子。布局与浮窗定位之后、绘制之前调用
    ``stream_pad(root, total_h) -> pad``；返回 pad>0 时由钩子自行把下屏
    （``dataFloatAnchor="bottom_area"``）与底部锚定浮窗的布局盒下移 pad
    行，本函数只把文档总高同步增加 pad——补白行因此落在下屏之前，内容、
    浮窗、状态栏/输入区三者在屏幕上都不移动。补白生效后还会按**含补白的
    总高**重新执行一次浮窗定位（2026-10-23 浮窗抖动修复：否则窗口可见
    高度/顶部裁剪仍按补白前高度解析，顶部边框/标题行会在补白帧消失）。
    默认 None 时零行为变化（独立 ``renderToString`` / ``render()`` / 测试
    路径不补白）。

    Args:
        root: 根 fiber（ROOT 或 APP host）。
        width: 文档宽度（终端列宽）。
        viewport_height: 终端可见高度（行）；> 0 时浮窗被钳进可视区
            （顶部锚点移入可视区、底部锚点裁剪顶部），渲染器只重写可见行。
            0 = 整帧输出（headless），不钳制。
        stream_pad: 可选补白钩子 ``(root_fiber, total_h) -> pad``。异常时
            按 0 处理（渲染不因补白失败而中断）。

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
    # ★ 浮窗收集缓存复位（2026-10-21 性能优化）：本帧的浮窗列表由紧随其后的
    #   ``_apply_float_positions`` → ``apply_floating_layout`` 写入，
    #   ``_overlay_floating`` 复用——避免同一帧对整棵组件树做两遍 DFS。
    #   先置 None：定位被跳过/异常时合成层回退现场收集（不读到上一帧的列表）。
    root._float_frame_windows = None
    # ★ 浮窗定位（2026-10-21 统一入口）：必须在布局之后、绘制之前——所有
    #   帧构建路径（InkSession / renderToString / render()）都经此处，
    #   修复「公共渲染 API 渲染 App 时浮窗不定位、粘在左上角」。幂等，调用方
    #   重复调用无副作用。
    _apply_float_positions(root, width, viewport_height)
    # ★ 2026-10-23（流式高度锁定·构建期补白）：在绘制前求补白行数——钩子
    #   负责平移下屏/浮窗布局盒，此处只同步文档总高，使画布多出 pad 行且
    #   浮窗合成行号与新布局一致。失败按 0（不补白）处理，渲染照常进行。
    if stream_pad is not None:
        try:
            pad = int(stream_pad(root, total_h) or 0)
        except Exception:
            _logger.debug("stream_pad 补白钩子异常（按不补白处理）", exc_info=True)
            pad = 0
        if pad > 0:
            total_h += pad
            # ★ 2026-10-23（补白帧浮窗抖动修复）：补白行改变了文档总高——
            #   浮窗的文档坐标 / 可见高度 / 源行全部依赖总高（底部框锚点按
            #   ``doc_height - bottom_h`` 推算、顶部裁剪按可用行数计算）。
            #   补白前那次定位用的是补白前高度，会让窗口顶部多裁一行：
            #   屏幕表现为补白帧浮窗顶部边框/标题行消失、下一帧又出现
            #   （用户报障「流式输出时浮动窗口抖动」）。此处按**含补白的
            #   总高**重新解析一次（幂等；仅在补白生效时执行，额外开销只
            #   落在需要补白的帧上）。
            _apply_float_positions(
                root, width, viewport_height, doc_height=total_h,
            )
    # ★ 画布惰性行（方向4）：初始 None——仅未命中行创建 dict（行级缓存优化；
    #   TEXT 命中行直接放 Line 对象，免逐字符重绘）。
    canvas: list = [None] * max(1, total_h)
    _paint(root, canvas)
    # ★ 跨帧 Line 复用缓存（2026-10-23 画布性能）：字典画布行（边框/背景/
    #   自定义 host 合并产物）在内容与渲染宽度都未变时直接复用上一帧 Line——
    #   既省掉排序 + 逐字符重建，又让行级 diff 命中身份短路（O(1)）。
    #   ``line_cache`` 每帧新建（旧缓存由 prev 读取后自然丢弃）→ 自动修剪；
    #   行值相等校验保证不会复用陈旧内容（resize 宽度变化亦在键内）。
    prev_line_cache = root._canvas_line_cache
    line_cache: dict = {}

    def _finish(frame: Frame) -> Frame:
        """收口：写回本帧 Line 缓存并执行浮窗合成。"""
        root._canvas_line_cache = line_cache
        return _overlay_floating(root, frame, width)

    # ★ committed-chat 前缀复用（大历史下渲染 O(live)）：静态提交行跨帧身份
    #   复用（``widgets.staticlines._paint`` 维护 ``_committed_prefix``），不再
    #   每帧全量遍历全部历史重建 Frame——修复长回答 + 子代理期间渲染线程持续
    #   重建整帧导致 CPU 100%。前缀未变时画布 committed 行被跳过（None），此处
    #   直接经缓存前缀拼接尾部。
    #   方向1 步骤4（非顶部前缀守卫）：仅顶部（committed.layout_box.y == 0）
    #   允许前缀直接拼接尾部——非顶部前缀与画布尾部重建偏移语义不一致时回退
    #   全量（防御层，成本 O(1)）；非顶部前缀已由 ``chat_view._paint`` 跳过
    #   画布写入，回退全量前将前缀行填入画布对应区域（box.y 起）保证 committed
    #   行不丢失。
    committed = _find_committed_chat(root)
    if committed is not None:
        prefix_info = committed._committed_prefix
        committed_box = committed.layout_box
        # ★ 2026-10-23（前缀/布局一致性守卫）：缓存前缀长度必须等于该 host
        #   的布局高度，否则前缀拼接会让尾部行整体错位一行（上屏内容短暂
        #   落到下屏顶部分割线行，下一帧才恢复）。不一致时把前缀行填回画布、
        #   放弃前缀复用，改走全量画布路径（行数与布局严格一致，内容零丢失）。
        if (
            prefix_info is not None
            and committed_box is not None
            and int(getattr(committed_box, "h", 0) or 0) != len(prefix_info[1])
        ):
            _fill_prefix_rows(
                canvas, prefix_info[1], int(getattr(committed_box, "y", 0) or 0),
                width,
            )
            prefix_info = None
        if prefix_info is not None:
            prefix_src = prefix_info[1]
            prefix = prefix_src
            # ★ 行宽守卫（E-COMMITTED-OVERFLOW 防御）：前缀含超宽行
            #   （reflow_committed 未执行/失败——终端宽度变化后 committed_lines
            #   按旧宽度 wrap）时截断超宽行（E-OVERFLOW-GUARD 语义），正常行
            #   保持身份短路。截断仅对超宽行执行（缓存重建时标记 all_ok=False；
            #   reflow 修复后缓存重建 all_ok=True 恢复零开销路径）。
            prefix_ok = prefix_info[2] if len(prefix_info) > 2 else True
            if not prefix_ok:
                # ★ 性能（超宽前缀截断缓存 + 增量扩展）：截断结果挂 committed
                #   fiber 缓存——修复前**每帧**对全部前缀行重截断（大历史每帧
                #   O(全前缀字符)：Line 重建 + 逐字符测宽），且重建列表每帧新
                #   对象 → 帧间身份不等 → ``first_diff_line`` 的 stable_prefix
                #   跳过失效（每帧全前缀比较）。
                #   缓存键用**前缀列表对象本身**（``is`` 比较，非 id——id 复用
                #   风险归零：缓存持强引用使旧列表不被回收，其地址不可能被新
                #   列表复用）；附带长度只增校验（前缀原地 extend 的增量提交
                #   场景）与布局宽度校验（resize 后必须按新宽度重截断）。
                #   命中时**只截断新增行**（每帧新增 N 行 → 只处理 N 行）；
                #   对象更换/长度回退/宽度变化 → 全量重建。
                #   缓存的截断列表对象跨帧稳定 → stable_prefix 区间跳过生效。
                truncated_cache = committed._truncated_prefix_cache
                if (
                    truncated_cache is not None
                    and truncated_cache[0] is prefix_src
                    and truncated_cache[3] == width
                    and truncated_cache[1] <= len(prefix_src)
                ):
                    prefix = truncated_cache[2]
                    for ln in prefix_src[truncated_cache[1]:]:
                        prefix.append(
                            truncate_line(ln, width) if ln.width > width else ln
                        )
                else:
                    prefix = [
                        truncate_line(ln, width) if ln.width > width else ln
                        for ln in prefix_src
                    ]
                committed._truncated_prefix_cache = (
                    prefix_src, len(prefix_src), prefix, width,
                )
            if committed_box is not None and committed_box.y == 0 and prefix_ok:
                # ★ P3 修复（review 方向）：与 fallback 路径一致加 fit 截断——
                #   reflow 期间前缀可能超画布（布局陈旧），修复前 ``prefix +
                #   tail`` 直接拼接把超长前缀行写入 Frame（破坏行宽不变量
                #   E-OVERFLOW-GUARD）。fit = 前缀实际覆盖行数（≤画布行数）。
                fit = min(len(prefix), max(0, len(canvas)))
                tail = _to_lines(
                    canvas[fit:], width, start=fit,
                    prev_cache=prev_line_cache, new_cache=line_cache,
                )
                # ★ 稳定前缀（PERF-7）：prefix 为复用列表对象（``_committed_prefix``
                # 缓存命中），标记 stable_prefix 使 ``first_diff_line`` 跳过前缀
                # 区间（避免大文档每帧全量逐行 is 比较）。防御：fit 可能 <
                # len(prefix)，stable_prefix_len 用实际覆盖 fit。
                return _finish(
                    Frame(
                        prefix[:fit] + tail,
                        stable_prefix=prefix,
                        stable_prefix_offset=0,
                        stable_prefix_len=fit,
                    ),
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
            header = _to_lines(
                canvas[:y0], width, start=0,
                prev_cache=prev_line_cache, new_cache=line_cache,
            )
            tail = _to_lines(
                canvas[y0 + fit:], width, start=y0 + fit,
                prev_cache=prev_line_cache, new_cache=line_cache,
            )
            # ★ 稳定前缀（PERF-7）：prefix 为复用列表对象（缓存命中），其
            #   ``[:fit]`` 部分在 Frame.lines 的 [y0, y0+fit) 区间——标记
            #   stable_prefix 使 ``first_diff_line`` 跳过该区间（前缀区间外
            #   的 header/tail 行仍逐行比较）。防御：fit 可能 < len(prefix)
            #   （reflow 布局陈旧），stable_prefix_len 用实际覆盖 fit。
            return _finish(
                Frame(
                    header + prefix[:fit] + tail,
                    stable_prefix=prefix,
                    stable_prefix_offset=y0,
                    stable_prefix_len=fit,
                ),
            )
    # ★ 无 committed 前缀复用：画布不含浮窗（``_paint`` 一律跳过浮窗 host），
    #   仍需经合成层绘制窗口——2026-10-21 统一：浮窗**只**在合成层绘制，
    #   与树中位置无关（不依赖「浮窗是根的最后子节点」这一隐式契约），
    #   同时保证 ``Frame.base_lines`` 是干净的内容行（输出历史用）。
    return _finish(
        Frame(
            _to_lines(
                canvas, width, start=0,
                prev_cache=prev_line_cache, new_cache=line_cache,
            ),
        ),
    )


__all__ = ["render_frame"]
