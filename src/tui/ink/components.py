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


def _border_style(props: dict, edge: str | None = None) -> Style:
    """解析边框样式（react-ink ``borderColor`` shorthand，完善 ink）。

    - ``borderStyle`` 传 Style 对象时原样返回（既有行为）。
    - 否则取 ``borderColor``（256 色号 int / 颜色名字符串）作 fg；
      缺省回退深青 23（既有行为）。

    单边（edge 非 None）：``borderTopColor``/``borderRightColor``/
    ``borderBottomColor``/``borderLeftColor`` 覆盖对应边（React Ink v6）；
    ``borderDimColor``/``border<Edge>DimColor`` 置 dim；``borderBackgroundColor``/
    ``border<Edge>BackgroundColor`` 置背景色。优先级：
    ``border<Edge>Color`` > ``borderColor`` > ``borderStyle``（Style 的 fg）> 23。
    """
    style = props.get("borderStyle")
    if isinstance(style, Style):
        base = style
    else:
        base = Style(fg=23)
    from .helpers import _parse_color
    if edge is None:
        border_color = props.get("borderColor")
        if border_color is not None:
            color = _parse_color(border_color)
            if color is not None:
                return Style(fg=color)
        return base
    # 单边：edge = "top"/"bottom"/"left"/"right"
    edge_color = props.get(f"border{edge.capitalize()}Color")
    color = edge_color if edge_color is not None else props.get("borderColor")
    dim_color = props.get(f"border{edge.capitalize()}DimColor")
    if dim_color is None:
        dim_color = props.get("borderDimColor")
    bg_color = props.get(f"border{edge.capitalize()}BackgroundColor")
    if bg_color is None:
        bg_color = props.get("borderBackgroundColor")
    fg = _parse_color(color) if color is not None else None
    bg = _parse_color(bg_color) if bg_color is not None else None
    if fg is not None:
        return Style(
            fg=fg,
            bg=bg,
            dim=bool(dim_color),
        )
    return Style(
        fg=base.fg,
        bg=bg if bg is not None else base.bg,
        dim=bool(dim_color) or base.dim,
    )


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

#: 自定义 borderStyle 对象缺省值（React Ink v6：``{topLeft, top, topRight,
#: left, bottomLeft, bottom, bottomRight, right}``——缺省项回退 "single"）。
_DEFAULT_BORDER_OBJECT: dict[str, str] = {
    "topLeft": "┌", "top": "─", "topRight": "┐",
    "left": "│",
    "bottomLeft": "└", "bottom": "─", "bottomRight": "┘",
    "right": "│",
}


def _border_chars(fiber: Fiber) -> tuple[str, str, str, str, str, str, str]:
    """解析 borderStyle 变体字符（缺省 single；未知值回退 single）。

    borderStyle 为 dict 时按自定义边框字符表解析（React Ink v6）——
    键 ``topLeft/top/topRight/left/bottomLeft/bottom/right``，
    缺省项回退 single。返回 ``(tl, tr, bl, br, hline, vline_l, vline_r)``
    ——vline_l/vline_r 左右独立（自定义对象 left/right 可不同字符）。
    """
    name = fiber.props.get("borderStyle")
    if isinstance(name, dict):
        return (
            str(name.get("topLeft", _DEFAULT_BORDER_OBJECT["topLeft"])),
            str(name.get("topRight", _DEFAULT_BORDER_OBJECT["topRight"])),
            str(name.get("bottomLeft", _DEFAULT_BORDER_OBJECT["bottomLeft"])),
            str(name.get("bottomRight", _DEFAULT_BORDER_OBJECT["bottomRight"])),
            str(name.get("top", _DEFAULT_BORDER_OBJECT["top"])),
            str(name.get("left", _DEFAULT_BORDER_OBJECT["left"])),
            str(name.get("right", _DEFAULT_BORDER_OBJECT["right"])),
        )
    if not isinstance(name, str):
        return _BORDER_CHARS["single"] + (_BORDER_CHARS["single"][5],)
    base = _BORDER_CHARS.get(name, _BORDER_CHARS["single"])
    return base + (base[5],)


def _line_as_dict(line: Line) -> dict:
    """将 Line 转为列键字典（``{display_col: (ch, style)}``，CJK 安全）。

    列键为**显示宽度**（``wcswidth_simple``），与画布行键语义一致——
    CJK 宽字符占 2 列则键递增 2（修复前逐字符 ``col += 1`` 导致宽字符
    后续内容错位重叠）。

    ★ 性能（2026-08-05）：纯可打印 ASCII run 走批量快路径——宽度 == 字符数
    （``isascii()`` + ``isprintable()`` C 实现单趟扫描），免逐字符
    ``wcswidth_simple`` 调用（渲染热路径画布转换以 ASCII 文本为主）。
    """
    d: dict = {}
    col = 0
    for run in line.runs:
        t = run.text
        if t.isascii() and t.isprintable():
            st = run.style
            for ch in t:
                d[col] = (ch, st)
                col += 1
        else:
            st = run.style
            for ch in t:
                d[col] = (ch, st)
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
    # ★ 性能（2026-08-05）：纯可打印 ASCII run 走批量快路径（宽度 == 字符数），
    #   免逐字符 ``wcswidth_simple`` 调用——画布合并热路径（TEXT 行合并、
    #   StaticLines 非 x==0 路径）以 ASCII 文本为主。
    for run in line.runs:
        t = run.text
        st = run.style
        if t.isascii() and t.isprintable():
            for ch in t:
                slice_[col] = (ch, st)
                col += 1
        else:
            for ch in t:
                slice_[col] = (ch, st)
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


def _slice_run_text(text: str, start_w: int, end_w: int) -> str:
    """按显示宽度切片文本（``[start_w, end_w)``，CJK 安全）。

    逐字符累积显示宽度；字符区间与目标区间有交时保留。宽字符横跨区间
    边界时整体保留（避免半个字符，可能略超边界——视觉正确优先）。

    Args:
        text: 原文本。
        start_w: 起始显示列（含）。
        end_w: 结束显示列（不含）。

    Returns:
        切片后的文本。
    """
    if start_w <= 0 and end_w >= 10**9:
        return text
    chars: list[str] = []
    col = 0
    for ch in text:
        w = wcswidth_simple(ch)
        if col < end_w and col + w > start_w:
            chars.append(ch)
        col += w
        if col >= end_w and chars and col - w >= end_w:
            break
    return "".join(chars)


def _slice_line(line: Line, start_col: int, end_col: int) -> Line:
    """返回保留 ``[start_col, end_col)`` 显示列的新 Line（列裁剪）。

    逐 run 求与目标区间的交集；交集为空/越界 run 跳过；宽字符横跨区间
    边界时整体保留（``_slice_run_text`` 语义）。用于 overflow 水平裁剪。

    Args:
        line: 原 Line。
        start_col: 起始显示列（含）。
        end_col: 结束显示列（不含）。

    Returns:
        裁剪后的新 Line（可能为空）。
    """
    out = Line()
    col = 0
    for run in line.runs:
        run_end = col + getattr(run, "width", 0)
        s = max(col, start_col)
        e = min(run_end, end_col)
        if s < e:
            text = _slice_run_text(run.text, s - col, e - col)
            if text:
                out.append(text, run.style)
        col = run_end
        if col >= end_col:
            break
    return out


def _paint_border(fiber: Fiber, canvas: list[dict], border: int, clip=None) -> None:
    """绘制 box 边框（border>=1 时画单线框）。

    React Ink v6 完整边框支持：
      - 各边独立颜色：``borderTopColor``/``borderRightColor``/
        ``borderBottomColor``/``borderLeftColor``（回退 ``borderColor``）；
      - ``borderDimColor``/``border<Edge>DimColor``：dim 边框；
      - ``borderBackgroundColor``/``border<Edge>BackgroundColor``：背景色；
      - 各边显隐：``borderTop``/``borderRight``/``borderBottom``/``borderLeft``
        （bool，默认 True）；
      - ``borderStyle`` 自定义对象（``{topLeft, top, topRight, left,
        bottomLeft, bottom, bottomRight, right}``）。

    clip 非 None 时按裁剪区域限制边框绘制（overflow 裁剪——父容器 hidden
    时边框超出部分被裁剪）。
    """
    box = fiber.layout_box
    # ★ 边框防御（方向1）：box 无效（None / 零宽 / 零高）时直接返回——
    #   修复前 ``x1 = x0 + box.w - 1`` 在 w=0 时 x1=x0-1，``row[x1]`` 负索引
    #   从列表末尾写（越界污染画布）。
    if box is None or box.w <= 0 or box.h <= 0:
        return
    props = fiber.props
    tl, tr, bl, br, hline, vline_l, vline_r = _border_chars(fiber)
    top_style = _border_style(props, "top")
    bottom_style = _border_style(props, "bottom")
    left_style = _border_style(props, "left")
    right_style = _border_style(props, "right")
    show_top = props.get("borderTop", True)
    show_bottom = props.get("borderBottom", True)
    show_left = props.get("borderLeft", True)
    show_right = props.get("borderRight", True)
    x0, y0 = box.x, box.y
    x1 = x0 + box.w - 1
    y1 = y0 + box.h - 1
    # overflow 裁剪范围（None=不裁剪）
    clip_x0 = clip_y0 = clip_x1 = clip_y1 = None
    if clip is not None and clip[2] > 0 and clip[3] > 0:
        clip_x0, clip_y0, cw, ch = clip
        clip_x1, clip_y1 = clip_x0 + cw, clip_y0 + ch
    if y0 < 0 or y0 >= len(canvas):
        return

    def _prepare_row(y) -> dict | None:
        """获取/创建画布行（含裁剪行范围检查）；越界返回 None。"""
        if y < 0 or y >= len(canvas):
            return None
        if clip_y0 is not None and (y < clip_y0 or y >= clip_y1):
            return None
        row = canvas[y]
        if isinstance(row, Line):
            row = _line_as_dict(row)
            canvas[y] = row
        elif row is None:
            row = {}
            canvas[y] = row
        return row

    # 顶边（含左上/右上角）
    # ★ 性能（2026-08-05）：无裁剪时整段构建 Line（角 + hline 填充 + 角）一次
    #   ``_merge_line`` 合并——替代逐字符 ``row[c]=(ch, style)`` 循环（每帧
    #   边框容器少 N 次 Python 循环 + if 分支；_merge_line 内部 dict 批量
    #   update 走 C 实现）。裁剪区间（overflow hidden 交集）罕见，保持逐字符
    #   语义（角/填充按实际可见区间）。hx1<hx0（裁剪空交集）时跳过顶边——
    #   不能提前 return（底边/左右边仍需绘制）。
    if show_top:
        row = _prepare_row(y0)
        if row is not None:
            hx0 = max(x0, clip_x0 if clip_x0 is not None else x0)
            hx1 = min(x1, clip_x1 - 1 if clip_x1 is not None else x1)
            if hx1 >= hx0:
                if hx0 == x0 and hx1 == x1:
                    # 完整顶边：tl + hline*(w-2) + tr（同色 top_style）；w==1 只写角
                    if x1 == x0:
                        _line = Line.of(tl, top_style)
                    else:
                        _line = Line.of(tl + hline * (x1 - x0 - 1) + tr, top_style)
                    canvas[y0] = _merge_line(row, x0, _line)
                else:
                    # 裁剪区间：角/填充按可见范围（与原逐字符语义一致）
                    _text = ""
                    for c in range(hx0, hx1 + 1):
                        if c == x0:
                            _text += tl
                        elif c == x1:
                            _text += tr
                        else:
                            _text += hline
                    canvas[y0] = _merge_line(row, hx0, Line.of(_text, top_style))
    # 底边（含左下/右下角）
    if show_bottom and y1 != y0:
        row = _prepare_row(y1)
        if row is not None:
            hx0 = max(x0, clip_x0 if clip_x0 is not None else x0)
            hx1 = min(x1, clip_x1 - 1 if clip_x1 is not None else x1)
            if hx1 >= hx0:
                if hx0 == x0 and hx1 == x1:
                    # 完整底边：bl + hline*(w-2) + br（同色 bottom_style）；w==1 只写角
                    if x1 == x0:
                        _line = Line.of(bl, bottom_style)
                    else:
                        _line = Line.of(bl + hline * (x1 - x0 - 1) + br, bottom_style)
                    canvas[y1] = _merge_line(row, x0, _line)
                else:
                    _text = ""
                    for c in range(hx0, hx1 + 1):
                        if c == x0:
                            _text += bl
                        elif c == x1:
                            _text += br
                        else:
                            _text += hline
                    canvas[y1] = _merge_line(row, hx0, Line.of(_text, bottom_style))
    # 左右边（不含顶/底）
    for r in range(y0 + 1, y1):
        row = _prepare_row(r)
        if row is None:
            continue
        if show_left and (clip_x0 is None or (x0 >= clip_x0 and x0 < clip_x1)):
            row[x0] = (vline_l, left_style)
        if show_right and (clip_x1 is None or (x1 >= clip_x0 and x1 < clip_x1)):
            row[x1] = (vline_r, right_style)


def _paint_box_background(box, canvas: list[dict], style: Style) -> None:
    """填充 Box 背景色到画布区域（完善 react ink v6 ``<Box backgroundColor>``）。

    以空格字符 + 背景样式填充 box 区域内所有单元格；随后子节点 TEXT 绘制
    会覆盖对应列（文本优先）。画布行初始为 None/Line 时先归一化为 dict。

    Args:
        box: 容器布局盒。
        canvas: 画布。
        style: 背景样式（``Style(bg=...)``）。
    """
    if box is None or box.w <= 0 or box.h <= 0:
        return
    for r in range(box.y, box.y + box.h):
        if r < 0 or r >= len(canvas):
            continue
        row = canvas[r]
        if isinstance(row, Line):
            row = _line_as_dict(row)
            canvas[r] = row
        elif row is None:
            row = {}
            canvas[r] = row
        for c in range(max(0, box.x), box.x + box.w):
            # 只填充空格位（已有内容不覆盖——本函数在子节点绘制前调用，
            # 但兄弟节点/边框可能已写；空格字符保证无文本时背景可见）
            if c not in row:
                row[c] = (" ", style)


def _merge_inherit_bg(style: Style | None, inherit_bg: Style | None) -> Style | None:
    """将父容器继承的背景色合并到子 TEXT 样式（完善 react ink v6）。

    React Ink 语义：``<Box backgroundColor>`` 的背景色被子 ``<Text>`` 继承
    （子 Text 未指定自身 backgroundColor 时）。返回合并后的样式——style 为
    None 时返回 inherit_bg（整段继承）；inherit_bg 为 None 或 style 已有 bg
    时不合并（子 Text 自身 bg 优先）。

    Args:
        style: 子 TEXT 解析后的样式（可能 None）。
        inherit_bg: 父容器背景样式（可能 None）。

    Returns:
        合并后的样式（可能 None）。
    """
    if style is None:
        return inherit_bg
    if inherit_bg is None or inherit_bg.bg is None or style.bg is not None:
        return style
    return Style(
        fg=style.fg,
        bg=inherit_bg.bg,
        bold=style.bold,
        italic=style.italic,
        dim=style.dim,
        underline=style.underline,
        strikethrough=style.strikethrough,
        inverse=style.inverse,
    )


def _apply_bg_to_line(line: Line, bg) -> Line:
    """克隆 Line 并为每个 run 合并背景色（不污染 layout 缓存）。

    用于 paint 阶段 Box 背景继承——layout 缓存 lines 复用路径下，克隆行
    应用背景色（``bg`` 为 256 色号 int）。已有 bg 的 run 原样保留（子 Text
    自身背景优先）。

    Args:
        line: 原 Line（共享缓存，不可修改）。
        bg: 背景色号 int。

    Returns:
        克隆并合并背景后的新 Line。
    """
    out = Line()
    bg_style = Style(bg=bg)
    for run in line.runs:
        st = getattr(run, "style", None)
        if st is not None and st.bg is not None:
            out.append_run(run)
        elif st is not None:
            out.append(
                run.text,
                Style(
                    fg=st.fg, bg=bg, bold=st.bold, italic=st.italic, dim=st.dim,
                    underline=st.underline, strikethrough=st.strikethrough,
                    inverse=st.inverse,
                ),
            )
        else:
            out.append(run.text, bg_style)
    return out


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
        # ★ 性能（2026-08-05）：连续段内非末尾字符用**相邻键差**推导宽度——
        #   画布写入不变量 ``col += wcswidth_simple(ch)`` 保证连续键差 = 前序
        #   字符宽度；差 1 必然 ASCII 宽 1（宽字符差 2、gap 差 >2 均不可能是 1）
        #   → 免 ``wcswidth_simple`` 调用（聊天文本以 ASCII 为主，内层热路径
        #   大量连续 ASCII 字符）。差 >=2（宽字符/gap）或段末尾（无下一键可
        #   推导）回退 ``wcswidth_simple``（单字符路径：ASCII O(1) / 缓存 O(1)）。
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
            if j + 1 < n and keys[j + 1] == c2 + 1:
                cw = 1  # 连续 ASCII 快路径（相邻键差 1）
            else:
                cw = wcswidth_simple(ch2)
            prev = c2 + cw
            j += 1
        if buf:
            line.append(buf, style)
        i = j
    return line


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
