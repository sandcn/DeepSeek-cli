"""display — React Ink 风格展示控件（Spinner / ProgressBar / Table / Badge / Divider）。

纯展示控件（无输入路由），输出由 BOX/TEXT 元素树构建：
  - Spinner      — 旋转加载动画（时间基帧推进，threading.Timer 驱动重渲染）
  - ProgressBar  — 进度条（percent 0-1 / 0-100 自适应 + 自定义左右标记）
  - Table        — 对齐表格（可选表头/边框/列宽自动计算）
  - Badge        — 背景色块徽章（前景自动对比）
  - Divider      — 分隔线（可选中间标题）

依赖约束：仅依赖 element / output / core.style / _screen / hooks（Layer 0/1），
无父包依赖。宽度一律用 ``_screen.wcswidth_simple``（唯一宽度依据）。
"""

from __future__ import annotations

import threading

from src.tui.core.style import Style
from src.tui._screen import wcswidth_simple
from ..element import BOX, TEXT, Element, h
from ..helpers import _parse_color
from ..hooks import use_state, use_effect

__all__ = ["Spinner", "ProgressBar", "Table", "Badge", "Divider"]


# ═══════════════════════════════════════════════════════════
# 公共辅助
# ═══════════════════════════════════════════════════════════


def _color(value, default: int = 6) -> int | None:
    """解析颜色 shorthand（颜色名/int）为 256 色号；解析失败回退 default。"""
    if value is None:
        return default
    parsed = _parse_color(value)
    return parsed if parsed is not None else default


def _resolve_style(props: dict, default_fg: int | None = None) -> Style | None:
    """合并 ``color``（fg shorthand）+ ``style`` 为 Style。

    ``color`` 解析成功后覆盖 style.fg；``style`` 为 None 时仅 color 生效。
    """
    color = props.get("color")
    style = props.get("style")
    fg = _color(color, default_fg) if color is not None else None
    if style is None and fg is None:
        return None
    merged = Style(fg=fg) if fg is not None else None
    if style is not None:
        merged = style.merge(merged) if merged else style
    return merged


def _repeat_to_width(char: str, width: int) -> str:
    """以 char 重复填充至目标显示宽度（不足部分补空格；宽字符按宽度换算）。"""
    if width <= 0:
        return ""
    cw = max(1, wcswidth_simple(char))
    count = width // cw
    out = char * count
    remain = width - cw * count
    if remain:
        out += " " * remain
    return out


# ═══════════════════════════════════════════════════════════
# Spinner — 旋转加载动画
# ═══════════════════════════════════════════════════════════

#: 内置动画帧字符集（Braille/几何/emoji，键名对齐 ink-spinner 常用预设）
SPINNER_FRAMES: dict[str, str] = {
    "dots": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
    "dots2": "⣾⣽⣻⢿⡿⣟⣯⣷",
    "dots3": "⠋⠙⠚⠞⠖⠦⠴⠲⠳⠓",
    "dots4": "⠄⠆⠇⠋⠙⠸⠰⠠⠰⠸⠙⠋⠇⠆",
    "dots5": "⠋⠙⠚⠒⠂⠂⠒⠲⠴⠦⠖⠒⠐⠐⠒⠓⠋",
    "dots6": "⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠴⠲⠒⠂⠂⠒⠚⠙⠉⠁",
    "dots7": "⠈⠉⠋⠓⠒⠐⠐⠒⠖⠦⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈",
    "dots8": "⠁⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈⠈",
    "dots9": "⢹⢺⢼⣸⣇⡧⡗⡏",
    "dots10": "⢄⢂⢁⡁⡈⡐⡠",
    "dots11": "⠁⠂⠄⡀⢀⠠⠐⠈",
    "line": "─╼╾╴╶",
    "line2": "⠂⠒⠐⠈⠁⠉⠐⠒⠂",
    "pipe": "┤┘┴└├┌┬┐",
    "simpleDots": "⠂⠄⠆⠇⠋⠙⠸⠰⠠⠰⠸⠙⠋⠇⠆⠄",
    "simpleDotsScrolling": "⠈⠐⠠⢀⡀⢄⡂⡆⡇⡏⡟⡿⢿⠻⠽⠾⢾⣀⣠⣄⣆⣇⣏⣟⣿",
    "bar": "▁▃▄▅▆▇█▇▆▅▄▃",
    "vertical": "▁▂▃▄▅▆▇█▇▆▅▄▃▂",
    "grow": "▁▂▃▄▅▆▇█",
    "growHorizontal": "▏▎▍▌▋▊▉█",
    "arrow": "←↖↑↗→↘↓↙",
    "moon": "🌑🌒🌓🌔🌕🌖🌗🌘",
    "dotsClassic": "⠁⠂⠄⡀⢀⠠⠐⠈",
    "shark": "▐▌▐▌",
}


def Spinner(props: dict) -> Element:
    """React Ink ``<Spinner>`` 等价物：旋转加载动画控件。

    Props:
        type: 内置动画预设名（见 ``SPINNER_FRAMES``；默认 ``"dots"``）。
        indicator: 自定义帧序列（字符串/列表——每个字符/元素一帧），
            提供时优先于 ``type``。
        interval: 帧切换间隔毫秒（默认 80）。
        color: 前景色（颜色名/int）。
        style: 完整样式（``color`` 覆盖 style.fg）。

    实现：``use_state`` 保存帧序号 + ``use_effect`` 注册 ``threading.Timer``
    周期推进帧序号（set_state → schedule → 重渲染）。组件卸载时清理 Timer
    （stop 标志防残余 tick 继续创建新 Timer）。``interval``/``indicator``
    变化不重建 Timer（挂载时捕获；React Ink setInterval deps=[] 同语义）。

    Returns:
        TEXT 元素（当前帧字符）。
    """
    indicator = props.get("indicator")
    type_ = str(props.get("type", "dots"))
    if indicator:
        frames = list(str(indicator))
    else:
        frames = list(SPINNER_FRAMES.get(type_, SPINNER_FRAMES["dots"]))
    if not frames:
        frames = [" "]
    try:
        interval = max(10, int(props.get("interval", 80)))
    except (TypeError, ValueError):
        interval = 80
    style = _resolve_style(props)
    frame_index, set_frame_index = use_state(0)

    def _create():
        stop = {"stop": False}

        def _tick():
            if stop["stop"]:
                return
            set_frame_index(lambda i: (i + 1) % len(frames))
            _schedule_next()

        def _schedule_next():
            t = threading.Timer(interval / 1000.0, _tick)
            t.daemon = True
            t.start()

        _schedule_next()

        def _cleanup():
            stop["stop"] = True

        return _cleanup

    use_effect(_create, ())

    ch = frames[frame_index % len(frames)]
    return h(TEXT, {"children": ch, "style": style, "height": 1})


# ═══════════════════════════════════════════════════════════
# ProgressBar — 进度条
# ═══════════════════════════════════════════════════════════


def ProgressBar(props: dict) -> Element:
    """React Ink ``<ProgressBar>`` 等价物：进度条控件。

    Props:
        percent: 进度（0-1 或 0-100，自动识别归一化）。
        width: 进度条区域宽度（默认 40）。
        left/right: 左右标记文本（如 ``"["`` / ``"]"``；默认空）。
        char: 进度填充字符（默认 ``"█"``；支持宽字符，按显示宽度换算）。
        color: 前景色（颜色名/int）。
        style: 完整样式（``color`` 覆盖 style.fg）。

    Returns:
        TEXT 元素（``left + 进度条 + right``）。
    """
    try:
        percent = float(props.get("percent", 0))
    except (TypeError, ValueError):
        percent = 0.0
    # 归一化：0-1 原样；(1, 100] 视为百分比；> 100 视为 100%
    if percent > 1.0:
        if percent <= 100.0:
            percent /= 100.0
        else:
            percent = 1.0
    percent = max(0.0, min(1.0, percent))
    try:
        width = max(1, int(props.get("width", 40)))
    except (TypeError, ValueError):
        width = 40
    left = str(props.get("left", ""))
    right = str(props.get("right", ""))
    char = str(props.get("char", "█")) or "█"
    style = _resolve_style(props)

    char_w = max(1, wcswidth_simple(char))
    filled_w = int(round(width * percent))
    filled_chars = filled_w // char_w
    bar = char * filled_chars + " " * (width - filled_chars * char_w)
    return h(TEXT, {"children": left + bar + right, "style": style})


# ═══════════════════════════════════════════════════════════
# Table — 对齐表格
# ═══════════════════════════════════════════════════════════

#: 边框字符元组：(左上, 右上, 左下, 右下, 横, 竖, 左T, 右T, 下T, 上T, 交叉)
_BORDER_TABLE: dict[str, tuple[str, str, str, str, str, str, str, str, str, str, str]] = {
    "single": ("┌", "┐", "└", "┘", "─", "│", "├", "┤", "┬", "┴", "┼"),
    "round": ("╭", "╮", "╰", "╯", "─", "│", "├", "┤", "┬", "┴", "┼"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃", "┣", "┫", "┳", "┻", "╋"),
    "classic": ("+", "+", "+", "+", "-", "|", "+", "+", "+", "+", "+"),
    "double": ("╔", "╗", "╚", "╝", "═", "║", "╠", "╣", "╦", "╩", "╬"),
}


def _table_border_row(chars: tuple, cell_w: list[int], left, mid, right) -> str:
    """构建表格边框行（顶/分隔/底共用）。"""
    parts = []
    for i, w in enumerate(cell_w):
        parts.append(chars[4] * w)
        if i < len(cell_w) - 1:
            parts.append(mid)
    return left + "".join(parts) + right


def Table(props: dict) -> Element:
    """React Ink ``<Table>`` 等价物：对齐表格控件。

    Props:
        data: 数据行（list of list/tuple；单元格 str() 化）。
        columns: 表头行（list of str；None 表示无表头）。
        padding: 单元格左右内边距（默认 1）。
        border: 边框风格（None=无边框对齐 | "single"/"round"/"bold"/
            "classic"/"double"）。
        headerColor: 表头前景色（默认 ``"cyan"``）。
        headerStyle: 表头完整样式（优先于 headerColor）。
        cellStyle: 数据单元格样式（默认 None）。
        borderColor: 边框颜色（颜色名/int；默认暗青 23）。

    Returns:
        BOX 元素（纵向堆叠的表格行）。
    """
    data = props.get("data", []) or []
    columns = props.get("columns")
    try:
        padding = max(0, int(props.get("padding", 1)))
    except (TypeError, ValueError):
        padding = 1
    border = props.get("border")
    if border is True:
        border = "single"
    border = str(border) if border else None
    header_style = props.get("headerStyle")
    if header_style is None:
        header_style = Style(fg=_color(props.get("headerColor", "cyan")), bold=True)
    cell_style = props.get("cellStyle")
    border_style = Style(fg=_color(props.get("borderColor"), 23))

    rows: list[list[str]] = []
    if columns is not None:
        rows.append([str(c) for c in columns])
    for row in data:
        rows.append([str(c) for c in row])
    if not rows:
        return h(BOX, {"flexDirection": "column"}, [])

    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], wcswidth_simple(cell))

    has_header = columns is not None

    # ── 无边框：纯对齐文本 ──
    if not border:
        lines = []
        for ri, r in enumerate(rows):
            cells = []
            for i in range(ncols):
                cell = r[i] if i < len(r) else ""
                cells.append(cell + " " * (widths[i] - wcswidth_simple(cell)))
            text = (" " * padding).join(cells).rstrip()
            if has_header and ri == 0:
                lines.append(h(TEXT, {"children": text, "style": header_style}))
            else:
                lines.append(h(TEXT, {"children": text, "style": cell_style}))
        return h(BOX, {"flexDirection": "column"}, lines)

    # ── 带边框 ──
    chars = _BORDER_TABLE.get(border, _BORDER_TABLE["single"])
    tl, tr, bl, br, hz, vt, ml, mr, mt, mb, cr = chars
    cell_w = [w + padding * 2 for w in widths]
    top = _table_border_row(chars, cell_w, tl, mt, tr)
    sep = _table_border_row(chars, cell_w, ml, cr, mr)
    bottom = _table_border_row(chars, cell_w, bl, mb, br)

    lines = [h(TEXT, {"children": top, "style": border_style})]
    for ri, r in enumerate(rows):
        cells = []
        for i in range(ncols):
            cell = r[i] if i < len(r) else ""
            cells.append(" " * padding + cell + " " * (widths[i] - wcswidth_simple(cell)) + " " * padding)
        row_text = vt + vt.join(cells) + vt
        if has_header and ri == 0:
            lines.append(h(TEXT, {"children": row_text, "style": header_style}))
            lines.append(h(TEXT, {"children": sep, "style": border_style}))
        else:
            lines.append(h(TEXT, {"children": row_text, "style": cell_style}))
    lines.append(h(TEXT, {"children": bottom, "style": border_style}))
    return h(BOX, {"flexDirection": "column"}, lines)


# ═══════════════════════════════════════════════════════════
# Badge — 背景色块徽章
# ═══════════════════════════════════════════════════════════

#: Badge 前景自动对比色（bg 偏暗 → 亮前景；bg 偏亮 → 暗前景）
_BADGE_FG_DARK = 231
_BADGE_FG_LIGHT = 232

#: 基础 16 色近似亮度（0-255 标度；用于前景对比判断）
_BASE_BRIGHTNESS: dict[int, int] = {
    0: 0,      # black
    1: 139,    # red
    2: 146,    # green
    3: 178,    # yellow
    4: 93,     # blue
    5: 158,    # magenta
    6: 170,    # cyan
    7: 192,    # white
    8: 128,    # brightBlack/gray
    9: 255,    # brightRed
    10: 255,   # brightGreen
    11: 255,   # brightYellow
    12: 255,   # brightBlue
    13: 255,   # brightMagenta
    14: 255,   # brightCyan
    15: 255,   # brightWhite
}
_ANSI_LEVELS = (0, 95, 135, 175, 215, 255)
#: 亮度阈值：背景亮度超过该值视为亮背景（用暗前景）
_BADGE_BRIGHTNESS_THRESHOLD = 150


def _ansi256_brightness(color: int) -> float:
    """估算 256 色号近似亮度（0-255 标度，NTSC 加权）。"""
    if color < 16:
        return _BASE_BRIGHTNESS.get(color, 128)
    if color >= 232:
        return (color - 232) * 10 + 8  # 灰阶 232→8、255→238 线性
    n = color - 16
    r = _ANSI_LEVELS[n // 36]
    g = _ANSI_LEVELS[(n // 6) % 6]
    b = _ANSI_LEVELS[n % 6]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _badge_fg_for_bg(bg: int) -> int:
    """根据背景色近似亮度返回可读前景色号（亮背景→暗前景，反之亦然）。"""
    if _ansi256_brightness(bg) > _BADGE_BRIGHTNESS_THRESHOLD:
        return _BADGE_FG_LIGHT  # 亮背景 → 暗前景
    return _BADGE_FG_DARK  # 暗背景 → 亮前景


def Badge(props: dict) -> Element:
    """React Ink ``<Badge>`` 等价物：背景色块徽章控件。

    Props:
        label: 徽章文本。
        color: 背景色（颜色名/int；默认 ``"cyan"``）。
        fg: 前景色（颜色名/int；缺省按背景亮度自动对比）。
        bold: 是否加粗（默认 False）。
        padding: 左右内边距（默认 1）。
        style: 完整样式（与上述字段合并；style 优先）。

    Returns:
        TEXT 元素（``  label  `` 背景色块）。
    """
    label = str(props.get("label", ""))
    bg = _color(props.get("color"), 6)
    fg = _color(props.get("fg")) if props.get("fg") is not None else _badge_fg_for_bg(bg)
    try:
        padding = max(0, int(props.get("padding", 1)))
    except (TypeError, ValueError):
        padding = 1
    style = Style(fg=fg, bg=bg, bold=bool(props.get("bold", False)))
    base = props.get("style")
    if base is not None:
        style = base.merge(style)
    text = " " * padding + label + " " * padding
    return h(TEXT, {"children": text, "style": style})


# ═══════════════════════════════════════════════════════════
# Divider — 分隔线
# ═══════════════════════════════════════════════════════════

_DIVIDER_DEFAULT_WIDTH = 40


def Divider(props: dict) -> Element:
    """React Ink 风格分隔线控件（``ink-divider`` 对齐）。

    Props:
        title: 中间标题（None 时输出纯分隔线）。
        width: 总显示宽度（None 时自动：有标题 = 标题宽 + 4；无标题 = 40）。
        char: 分隔字符（默认 ``"─"``；支持宽字符按宽度换算）。
        color: 分隔线前景色（颜色名/int）。
        style: 分隔线完整样式（``color`` 覆盖 style.fg）。
        titleStyle: 标题样式（默认 None）。

    Returns:
        无标题：TEXT 元素；有标题：BOX 元素（横向：线+空格+标题+空格+线）。
    """
    title = props.get("title")
    title = None if title is None else str(title)
    char = str(props.get("char", "─")) or "─"
    width = props.get("width")
    if width is not None:
        try:
            width = max(1, int(width))
        except (TypeError, ValueError):
            width = None
    if width is None:
        width = wcswidth_simple(title) + 4 if title else _DIVIDER_DEFAULT_WIDTH
    hz_style = _resolve_style(props)
    title_style = props.get("titleStyle")

    if not title:
        return h(TEXT, {"children": _repeat_to_width(char, width), "style": hz_style})

    title_w = wcswidth_simple(title)
    avail = width - title_w - 2  # 两侧各留 1 空格
    if avail <= 0:
        return h(TEXT, {"children": title, "style": title_style})
    left_w = avail // 2
    right_w = avail - left_w
    return h(BOX, {"flexDirection": "row"}, [
        h(TEXT, {"children": _repeat_to_width(char, left_w), "style": hz_style}),
        h(TEXT, {"children": " "}),
        h(TEXT, {"children": title, "style": title_style}),
        h(TEXT, {"children": " "}),
        h(TEXT, {"children": _repeat_to_width(char, right_w), "style": hz_style}),
    ])
