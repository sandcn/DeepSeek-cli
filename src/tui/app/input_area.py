"""InputArea — 输入区 host 组件（提示符 + 换行输入 + 光标 + 呼吸发光）。

注册 ``input-area`` host 标签到 ink 注册表：
  - measure_fn：补全弹窗 + 上分隔线 + 输入行 + 下分隔线高度。
  - paint_fn：绘制到画布。

复用 _input.py 的 ``_expand_tabs`` / ``_wrap_by_width`` / ``_compute_cursor_visual_pos``
（唯一真源），保证换行/CJK/光标计算与旧实现一致。
"""

from __future__ import annotations

import time

from src.tui._screen import (
    _COLOR_ACCENT,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _COLOR_SPEED,
    _COLOR_TIME,
    wcswidth_simple,
)
from src.tui._input import (
    _expand_tabs,
    _wrap_by_width,
    _tab_pos_to_expanded,
    _compute_cursor_visual_pos,
)
from src.tui.core.style import Style
from src.tui.ink import register_host, Line
from src.tui.app import _fx
from src.tui.app._theme import time_glow, _S_ACCENT, _S_SEP, _S_TEXT, _S_TIME

# 占位符
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"
_PLACEHOLDER_STREAMING = "AI 生成中..."

_PROMPT = "> "

# 方向C 步骤4：_S_TEXT 被多处使用 → 迁入 app/_theme.py 共享池；以下单处使用
# 常量保留模块私有（享元收敛原则：仅多处使用才共享）。
# P2-10：_S_PROMPT/_S_PLACEHOLDER 为死常量（定义后全项目无引用——提示符已用
# 呼吸色 _glow_color、占位符已用渐显色 _placeholder_fade_color）→ 删除。
_S_CONT = Style(fg=242)
_S_CPU = Style(fg=214)
_S_MEM = Style(fg=214)


def _glow_color(base: int, amp: int) -> int:
    return time_glow(base, base + amp, 12.0)


def _placeholder_fade_color(fiber, ph: str, end_color: int) -> int:
    """占位提示 FadeIn 渐显色号（BEAUTY-1，时间基）。

    fiber 上记录 ``(ph, start_monotonic)``；占位符出现/切换时重置起始时间，
    同占位符持续显示时 elapsed 单调递增；elapsed>=duration 后返回 end_color
    （动画结束返回终色，不再触发重绘——BEAUTY-5）。duration/start 使用
    ``_fx.fade_color`` 默认参数（对齐 TuiConfig.fade_duration_sec/fade_start_color）。
    """
    key = getattr(fiber, "_placeholder_fade_key", None)
    if key is None or key[0] != ph:
        fiber._placeholder_fade_key = (ph, time.monotonic())
        start = time.monotonic()
    else:
        start = key[1]
    elapsed = time.monotonic() - start
    return _fx.fade_color(elapsed, None, 238, end_color)


def _compute_input_layout(text: str, max_input: int) -> tuple[int, list[list[str]]]:
    """单次换行计算：返回 (总行数, 每逻辑行拆行后的段列表)。

    ``wrapped_by_logical[i]`` 为第 i 个逻辑行（按 ``\\n`` 拆分）拆行后的段列表；
    空逻辑行对应 ``[""]``。PERF-1 统一换行计算（每帧至多 1 次），
    ``_measure`` / ``_build_lines`` / ``session._position_cursor`` 均复用。
    """
    if not text:
        return 1, [[""]]
    expanded = _expand_tabs(text)
    wrapped_by_logical: list[list[str]] = []
    total_rows = 0
    for segment in expanded.split('\n'):
        seg_wrapped = _wrap_by_width(segment, max_input) or [""]
        wrapped_by_logical.append(seg_wrapped)
        total_rows += len(seg_wrapped)
    return max(1, total_rows), wrapped_by_logical


def _compute_input_rows(text: str, max_input: int) -> int:
    """输入文本换行行数（至少 1）。"""
    rows, _ = _compute_input_layout(text, max_input)
    return rows


def _wrap_input_text(text: str, max_input: int) -> list[str]:
    """输入文本拆行段列表（扁平，兼容旧调用面）。"""
    _, wrapped_by_logical = _compute_input_layout(text, max_input)
    return [seg for segs in wrapped_by_logical for seg in segs]


def _cursor_visual_from_layout(
    text: str,
    cursor_pos: int,
    wrapped_by_logical: list[list[str]],
) -> tuple[int, int]:
    """基于已缓存的换行布局计算光标视觉位置（复用缓存，避免重复换行计算）。

    与 ``_compute_cursor_visual_pos`` 语义一致：返回 (visual_line_idx, visual_col)。
    仅对光标所在逻辑行做 O(行) 定位，不重新整段换行。
    """
    if not text:
        return (0, 0)
    abs_cursor = len(text) if cursor_pos < 0 else cursor_pos

    lines = text.split('\n')
    cum = 0
    for logical_idx, logical_line in enumerate(lines):
        line_len = len(logical_line)
        if abs_cursor <= cum + line_len:
            # 光标在此逻辑行中（或在行末的 \n 上）
            pos_in_line = abs_cursor - cum
            segs = (
                wrapped_by_logical[logical_idx]
                if logical_idx < len(wrapped_by_logical)
                else [""]
            )
            expanded_in_line = _tab_pos_to_expanded(logical_line, pos_in_line)
            if expanded_in_line < 0:
                last_seg = segs[-1] if segs else ""
                col_in_line = wcswidth_simple(last_seg)
                visual_line_in_logical = len(segs) - 1 if segs else 0
            else:
                cum2 = 0
                visual_line_in_logical = 0
                for i, seg in enumerate(segs):
                    if expanded_in_line <= cum2 + len(seg):
                        visual_line_in_logical = i
                        prefix = seg[:expanded_in_line - cum2]
                        col_in_line = wcswidth_simple(prefix)
                        break
                    cum2 += len(seg)
                else:
                    visual_line_in_logical = len(segs) - 1 if segs else 0
                    col_in_line = wcswidth_simple(segs[-1]) if segs else 0
            total_before = sum(len(s) for s in wrapped_by_logical[:logical_idx])
            return (total_before + visual_line_in_logical, col_in_line)
        cum += line_len + 1

    # 超出范围 → 末尾
    last_segs = wrapped_by_logical[-1] if wrapped_by_logical else [""]
    last_seg = last_segs[-1] if last_segs else ""
    col = wcswidth_simple(last_seg)
    total_before = sum(len(s) for s in wrapped_by_logical[:-1])
    visual_row = total_before + (len(last_segs) - 1 if last_segs else 0)
    return (visual_row, col)


# ── 测量 ───────────────────────────────────────────


def _completion_height(completion) -> int:
    """补全弹窗高度（标题 + 候选项 + 提示行）。"""
    if completion is None or not completion.visible or not completion.items:
        return 0
    return len(completion.items) + 2


def _is_search_active(search) -> bool:
    """反向历史搜索是否激活（history_search 非 None 且 active，方向D 步骤14）。"""
    return search is not None and bool(getattr(search, "active", False))


def _measure(fiber, avail_w) -> tuple[int, int]:
    props = fiber.props
    explicit = props.get("width")
    width = max(0, int(explicit)) if explicit is not None else avail_w
    completion = props.get("completion")
    popup_height = _completion_height(completion)
    max_input = max(1, width - len(_PROMPT))
    text = str(props.get("text", ""))
    # ★ 方向D 步骤14：反向历史搜索覆盖行（追加一行）
    search_active = _is_search_active(props.get("history_search"))
    # ★ PERF-1：缓存命中（同 text/max_input）时复用换行布局（每帧至多 1 次换行）
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        rows, _ = cached[1]
    else:
        rows, wrapped_by_logical = _compute_input_layout(text, max_input)
        fiber._input_layout_cache = ((text, max_input), (rows, wrapped_by_logical))
    height = popup_height + 2 + rows + (1 if search_active else 0)
    return (width, height)


# ── 绘制 ───────────────────────────────────────────


def _build_lines(fiber) -> list[Line]:
    props = fiber.props
    box = fiber.layout_box
    width = box.w
    text = str(props.get("text", ""))
    completion = props.get("completion")
    popup_height = _completion_height(completion)
    status_active = bool(props.get("status_active", False))
    max_input = max(1, width - len(_PROMPT))

    # ★ PERF-1：复用 measure 阶段缓存的换行布局（未命中时回退单次计算）
    cached = getattr(fiber, "_input_layout_cache", None)
    if cached is not None and cached[0] == (text, max_input):
        _, wrapped_by_logical = cached[1]
    else:
        _, wrapped_by_logical = _compute_input_layout(text, max_input)
    wrapped = [seg for segs in wrapped_by_logical for seg in segs]

    lines: list[Line] = []

    # ── 补全弹窗 ──
    if completion is not None and completion.visible and completion.items:
        items = completion.items
        selected = completion.selected
        match_prefix = completion.match_prefix or ""
        types = completion.types or [""] * len(items)
        title = completion.title
        total = len(completion.texts) if completion.texts else len(items)
        # 标题行
        head = Line.of(" ", Style(fg=45, bold=True))
        head.append(title, Style(fg=45, bold=True))
        head.append(f" ({total}项)", _S_TIME)
        lines.append(head)
        # 候选项
        cell_w = max(1, min(max((_vwidth(i) for i in items), default=10) + 4, width - 2) - 3)
        for i, item in enumerate(items):
            line = Line()
            if i == selected:
                line.append(" \u25b6 ", Style(fg=15, bg=236))
            else:
                line.append("  ")
            for run in _styled_completion(item, types[i], match_prefix, cell_w).runs:
                line.append_run(run)
            lines.append(line)
        # 底部提示
        hint = Line.of(" ", _S_TIME)
        hint.append("Tab \u2191\u2193 Esc", _S_TIME)
        lines.append(hint)

    # ── 上分隔线（CPU/MEM） ──
    cpu = int(props.get("cpu", 0))
    mem = int(props.get("mem", 0))
    cpu_mem = f"CPU:{cpu}% \u00b7 MEM:{mem}%"
    cpu_mem_w = len(cpu_mem) + 2
    top = Line.of("", _S_SEP)
    sep_len = max(1, width - cpu_mem_w)
    top.append("\u2501" * sep_len, _S_SEP)
    top.append(" CPU:", _S_ACCENT)
    top.append(f"{cpu}%", _S_CPU)
    top.append(" \u00b7 MEM:", _S_ACCENT)
    top.append(f"{mem}%", _S_MEM)
    lines.append(top)

    # ── 反向历史搜索覆盖行（方向D 步骤14，Ctrl+R 配置门控） ──
    # 搜索激活时在上分隔线之后、输入文本行之前追加一行（measure 已增行）：
    # (reverse-i-search)`query`: match
    search = props.get("history_search")
    if _is_search_active(search):
        q = search.query
        match = ""
        if search.matches and 0 <= search.index < len(search.matches):
            match = search.matches[search.index]
        sline = Line.of("(reverse-i-search)`", _S_ACCENT)
        sline.append(q, Style(fg=221))
        sline.append("`: ", _S_ACCENT)
        sline.append(match, _S_TEXT)
        lines.append(sline)

    # ── 输入文本行 ──
    # ★ PERF-1：wrapped 已在函数开头从缓存/单次计算得到（见上），此处直接使用
    for i, segment in enumerate(wrapped):
        line = Line()
        if i == 0:
            color = _glow_color(32, 49)
            line.append(_PROMPT, Style(fg=color, bold=True))
            if text:
                line.append(segment, _S_TEXT)
            else:
                if status_active:
                    ph = _PLACEHOLDER_STREAMING
                else:
                    ph = _PLACEHOLDER_COMPACT if (completion is not None and completion.visible) else _PLACEHOLDER_TEXT
                # BEAUTY-1：占位提示 FadeIn 渐显（时间基；_glow_color 呼吸色为终色）
                line.append(ph, Style(fg=_placeholder_fade_color(fiber, ph, _glow_color(242, 10))))
        else:
            line.append("\u00b7 ", _S_CONT)
            line.append(segment, _S_TEXT)
        lines.append(line)

    # ── 下分隔线（时间戳） ──
    now_local = time.localtime()
    ts = f"{now_local.tm_year}-{now_local.tm_mon:02d}-{now_local.tm_mday:02d} {now_local.tm_hour:02d}:{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
    time_w = len(ts) + 2
    bottom = Line.of("", _S_SEP)
    bottom.append("\u2501" * max(1, width - time_w), _S_SEP)
    bottom.append(f" {ts}", _S_TIME)
    lines.append(bottom)

    return lines


def _vwidth(s: str) -> int:
    return wcswidth_simple(s)


def _styled_completion(text: str, item_type: str, match_prefix: str, cell_w: int) -> Line:
    """构建候选项行（命令/目录/匹配高亮）。"""
    out = Line()
    truncated = _truncate_width(text, cell_w)
    if item_type == "command" and truncated.startswith("/"):
        out.append("/", Style(fg=45, bold=True))
        rest = truncated[1:]
        if match_prefix and len(match_prefix) > 1 and rest.startswith(match_prefix[1:]):
            inner = match_prefix[1:]
            out.append(rest[:len(inner)], Style(fg=221))
            out.append(rest[len(inner):])
        else:
            out.append(rest)
    elif item_type == "dir" and truncated.endswith("/"):
        out.append(truncated, Style(fg=110))
    else:
        if match_prefix and truncated.startswith(match_prefix):
            out.append(truncated[:len(match_prefix)], Style(fg=221))
            out.append(truncated[len(match_prefix):])
        else:
            out.append(truncated)
    return out


def _truncate_width(s: str, max_w: int) -> str:
    w = 0
    out = []
    for ch in s:
        cw = wcswidth_simple(ch)
        if w + cw > max_w:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def _paint(fiber, canvas) -> None:
    box = fiber.layout_box
    if box is None:
        return
    lines = _build_lines(fiber)
    for i, line in enumerate(lines):
        row = box.y + i
        if 0 <= row < len(canvas):
            _merge(canvas[row], box.x, line)


def _merge(row: dict, x: int, line: Line) -> None:
    col = x
    for run in line.runs:
        for ch in run.text:
            row[col] = (ch, run.style)
            col += 1


# ── 注册 ───────────────────────────────────────────


def register() -> None:
    """注册 input-area host 组件。"""
    register_host("input-area", _measure, _paint)


__all__ = [
    "register",
    "_measure",
    "_paint",
    "_compute_input_rows",
    "_compute_input_layout",
    "_wrap_input_text",
    "_cursor_visual_from_layout",
]
