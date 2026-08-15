"""边框与背景绘制 — border 变体/样式解析 + 画布绘制。

模块边界（2026-08-05 架构优化）：从 ``ink/components.py`` 拆分——边框字符
解析（``_border_style``/``_BORDER_CHARS``/``_border_chars``）与边框/背景
画布绘制（``_paint_border``/``_paint_box_background``/``_merge_inherit_bg``/
``_apply_bg_to_line``）独立成模块，供主绘制（``_paint_impl``）与面板控件
（``widgets._panel``）共享。依赖 ``_paint_canvas``（行合并/归一化）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from .fiber import Fiber
from .output import Line
from ._paint_canvas import (
    _line_as_dict,
    _merge_line,
)


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
                # ★ P3 修复（review 方向）：重建 Style(fg=color) 丢失 base 的
                #   bg/dim/bold/italic/underline/strikethrough/inverse——
                #   borderStyle 传 Style 对象（含字型/背景）或
                #   borderDimColor/borderBackgroundColor 已并入 base 时须保留。
                return Style(
                    fg=color, bg=base.bg, dim=base.dim,
                    bold=base.bold, italic=base.italic,
                    underline=base.underline,
                    strikethrough=base.strikethrough,
                    inverse=base.inverse,
                )
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
    # ★ P3 修复（review 方向）：两个分支均保留 base 的字型属性
    #   （bold/italic/underline/strikethrough/inverse——修复前重建
    #   Style(fg=..., bg=..., dim=...) 丢失非 fg/bg/dim 字段）。
    if fg is not None:
        return Style(
            fg=fg,
            bg=bg if bg is not None else base.bg,
            # ★ P2-1 修复（review 方向）：fg 分支也保留 base.dim——修复前
            #   ``dim=bool(dim_color)`` 与 fg None 分支
            #   ``bool(dim_color) or base.dim`` 不一致：borderStyle 传 Style
            #   对象（base.dim=True）+ borderColor 指定 fg 时丢失 base.dim。
            dim=bool(dim_color) or base.dim,
            bold=base.bold, italic=base.italic,
            underline=base.underline,
            strikethrough=base.strikethrough,
            inverse=base.inverse,
        )
    return Style(
        fg=base.fg,
        bg=bg if bg is not None else base.bg,
        dim=bool(dim_color) or base.dim,
        bold=base.bold, italic=base.italic,
        underline=base.underline,
        strikethrough=base.strikethrough,
        inverse=base.inverse,
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
    # ★ P3-2 修复（review 方向）：负坐标防御——写画布前钳制 x0/x1 >= 0
    #   （修复前 box.x 为负时顶/底边 ``_merge_line`` 从负列写入、左右边
    #   ``row[x0]``/``row[x1]`` 负索引从列表末尾写，污染画布）。box 完全
    #   在屏幕左侧外（x1 < 0）时无可见部分，直接返回。
    if x1 < 0:
        return
    x0 = max(x0, 0)
    x1 = max(x1, 0)
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


__all__ = [
    "_border_style",
    "_BORDER_CHARS",
    "_DEFAULT_BORDER_OBJECT",
    "_border_chars",
    "_paint_border",
    "_paint_box_background",
    "_merge_inherit_bg",
    "_apply_bg_to_line",
]
