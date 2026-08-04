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
from .layout import layout_tree, wrap_text_lines, _skip_function
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


#: borderStyle 变体字符表（完善 react ink）：单线/双线/圆角/粗体/经典/虚线/
#: 单双混合（singleDouble/doubleSingle）。键 = props["borderStyle"] 字符串；
#: 缺省 "single"。classic 为 ASCII 经典边框（``+---``/``|``）；dashed 为虚线
#: 边框（``┄``/``┆``，视觉更轻）。
#: singleDouble：顶/底双线、左右单线（``╓ ╖ ╙ ╜ ═ ║``）；doubleSingle：
#: 顶/底单线、左右双线（``╒ ╕ ╘ ╛ ─ ╞`` 类）——react-ink 完整变体。
_BORDER_CHARS: dict[str, tuple[str, str, str, str, str, str]] = {
    "single": ("┌", "┐", "└", "┘", "─", "│"),
    "double": ("╔", "╗", "╚", "╝", "═", "║"),
    "round": ("╭", "╮", "╰", "╯", "─", "│"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃"),
    "classic": ("+", "+", "+", "+", "-", "|"),
    "dashed": ("┌", "┐", "└", "┘", "┄", "┆"),
    "singleDouble": ("╓", "╖", "╙", "╜", "═", "│"),
    "doubleSingle": ("╒", "╕", "╘", "╛", "─", "║"),
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


def _overlaps_wide_second_col(row: dict, slice_: dict) -> bool:
    """检测 slice_ 是否存在「新字符落在既有宽字符第二列」的冲突（E2）。

    条件：任一 ``c in slice_`` 满足——``c > 0`` 且 ``(c-1) in row`` 且
    ``row[c-1]`` 为宽字符（显示宽度 2）且 ``(c-1) not in slice_``（slice_ 自身
    同时含首列+第二列时视为正常覆盖，不冲突——宽字符整体替换走逐键覆盖）。

    供 ``_merge_line`` 快路径判定：disjoint 命中（无普通键冲突）时仍可能
    存在「新字符落在宽字符第二列」——批量 update 会让新字符被
    ``_canvas_row_to_line`` 的 ``col < prev`` 跳过（静默丢失，E2）。

    Args:
        row: 目标画布行（dict 形态，col → (ch, style)）。
        slice_: 待合并片段（col → (ch, style)）。

    Returns:
        True — 存在宽字符第二列冲突，须走逐键覆盖分支。
    """
    for c in slice_:
        if c <= 0:
            continue
        left = row.get(c - 1)
        if left is not None and wcswidth_simple(left[0]) == 2 and (c - 1) not in slice_:
            return True
    return False


def _merge_line(row, x: int, line: Line) -> dict:
    """将 Line 合并到画布行（从第 x 列开始），返回合并后的行。

    性能快路径：构造 ``{col: (ch, style)}`` 片段，与目标行键集无交时批量
    ``row.update(slice_)``；重叠时回退逐字符覆盖（语义一致）。目标行可能
    为 Line/None/dict 任意形态——先 ``_ensure_row_dict`` 归一化再合并。

    ★ E2（宽字符第二列覆盖）：快路径在普通键无交（disjoint）之外还须检查
    宽字符第二列冲突（``_overlaps_wide_second_col``）——新字符落在既有宽字符
    第二列时，批量 update 后 ``_canvas_row_to_line`` 的 ``col < prev`` 跳过该
    键（新字符静默丢失，如 row={0:('中'),2:('a')} + 覆盖键 1 → 渲染 "中a"、
    "X" 丢失）。冲突时走逐键覆盖：**新字符获胜、旧宽字符整体消失**（视觉
    语义：宽字符被覆盖为新字符，不再静默丢失；被替换字符为空格时同样整体
    替换——新写入内容优先）。

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
    # ★ P2（review）：空行（row={}）场景跳过宽字符第二列扫描（常见合并热路径
    #   零额外开销）——``not row`` 短路后不调用 ``_overlaps_wide_second_col``。
    if slice_.keys().isdisjoint(row) and (not row or not _overlaps_wide_second_col(row, slice_)):
        row.update(slice_)
    else:
        for c, v in slice_.items():
            # ★ E2（宽字符第二列覆盖）：新字符落在既有宽字符第二列——替换
            #   宽字符整体（新字符不再静默丢失）。语义：宽字符被新字符覆盖
            #   （如 ``中`` 第二列被 ``X`` 覆盖 → 渲染 ``X``，不残留 ``中``）。
            if (
                c > 0
                and (c - 1) in row
                and (c - 1) not in slice_
                and wcswidth_simple(row[c - 1][0]) == 2
            ):
                row[c - 1] = v
                row.pop(c, None)
                continue
            # ★ BUG-61（review 方向）：宽字符残留清理——被覆盖位置为宽字符
            #   首列（旧占 c+1 列，仅首列键）时同步清除 c+1 键（残留第二列
            #   字形）；新写入字符为宽字符（占 c+1 列）时清除 c+1 旧内容
            #   （slice_ 未覆盖该键——宽字符只写首列键）。修复前覆盖宽字符
            #   首列后行含孤立第二列字形（渲染出 ``a``+残留字形）。
            old = row.get(c)
            if old is not None and wcswidth_simple(old[0]) == 2 and (c + 1) not in slice_:
                row.pop(c + 1, None)
            row[c] = v
            if wcswidth_simple(v[0]) == 2 and (c + 1) not in slice_:
                row.pop(c + 1, None)
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
        _paint_children(fiber, canvas)
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
        _paint_border(fiber, canvas, border)
    _paint_children(fiber, canvas)


def _paint_children(fiber: Fiber, canvas: list) -> None:
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
                _paint_children(host, canvas)
            else:
                _paint(host, canvas)
        child = child.sibling


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
        # ★ 方向8（宽字符重叠键死循环修复）：键列已被前序宽字符（CJK/emoji，
        #   宽 2 覆盖相邻列）占用时（``col < prev``）跳过该键——修复前
        #   ``col > prev`` 为 False 且内层 ``c2 != prev`` 立即 break → ``i = j``
        #   不变 → **无限循环**（画布行含宽字符 + 重叠键时整帧渲染挂起）。
        if col < prev:
            i += 1
            continue
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
            if c2 < prev:
                # 键已被前序宽字符覆盖（宽字符的第二列）→ 跳过该键
                j += 1
                continue
            if c2 != prev:
                break
            ch2, st2 = row[c2]
            if st2 != style:
                break
            buf += ch2
            prev = c2 + wcswidth_simple(ch2)
            j += 1
        if buf:
            line.append(buf, style)
        i = j
    return line


def _find_committed_chat(root: Fiber):
    """DFS 查找 committed-chat host fiber（聊天历史增量缓存发射器）。

    组件树中聊天历史作为单个 host 挂载（ChatView use_memo 缓存元素），
    静态行经 ``chat_view._paint`` 维护帧前缀缓存；render_frame 复用该前缀，
    每帧只重建尾部 live 区——大历史下 Frame 构建 O(live) 而非 O(全部历史)。

    方向4（性能）：查找结果缓存于 root fiber（``_committed_chat_cache``）——
    reconciler 按 key 复用 fiber，committed-chat 在树中位置跨帧稳定，无需每帧
    DFS 全树搜索（大历史树 ~1500 fiber 时 DFS 为可感知开销）。缓存失效条件：
    缓存的 fiber 被删除（deleted）或 type 不再匹配（如 committed_lines 清空
    后 ChatView 不再挂载 committed-chat）→ 重新 DFS。Cache miss 后写回缓存。

    ★ 性能（PERF-15）：**未挂载快速路径**——committed-chat 通常**不存在**
    （纯 TEXT 组件树 / 无聊天历史的场景），且其存在性跨帧稳定（ChatView
    ``use_memo`` 依赖 ``model.committed_lines``，空列表时不挂载）。修复前
    ``_find_committed_chat`` 对**每帧**都做全树 DFS（找到才写缓存；未找到时
    ``del`` 缓存——**下一帧又 DFS**），纯 TEXT 大组件树（1000+ fiber）每帧
    DFS 开销可感知（~12ms/帧）。修复：fiber 上缓存 ``_committed_chat_present``
    标志（reconciler 每帧调和时统计是否存在 committed-chat host，layout_tree
    前的整树遍历天然提供该信息）；标志为 False 时 O(1) 返回 None，零 DFS。
    """
    # ★ PERF-15：未挂载快速路径——标志由 reconciler._measure 统计（layout_tree
    #   整树遍历时置位；见 layout.py _measure 容器分支注释）。无 committed-chat
    #   的组件树每帧零 DFS。
    if not getattr(root, "_committed_chat_present", False):
        return None
    cached = getattr(root, "_committed_chat_cache", None)
    if (
        cached is not None
        and cached.is_host
        and cached.type == "committed-chat"
        and not getattr(cached, "deleted", False)
    ):
        return cached
    stack = [root]
    found = None
    while stack:
        f = stack.pop()
        if getattr(f, "deleted", False):
            continue
        if f.is_host and f.type == "committed-chat":
            found = f
            break
        child = f.child
        while child is not None:
            stack.append(child)
            child = child.sibling
    if found is not None:
        root._committed_chat_cache = found
    else:
        # 未找到 → 清空缓存（committed-chat 已卸载）
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
