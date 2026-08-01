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
from src.tui._input import _expand_tabs, _wrap_by_width, _compute_cursor_visual_pos
from src.tui.core.style import Style
from src.tui.ink import register_host, Line
from src.tui.app._theme import time_glow, _S_ACCENT, _S_SEP, _S_TIME

# 占位符
_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"
_PLACEHOLDER_STREAMING = "AI 生成中..."

_PROMPT = "> "

_S_PROMPT = Style(fg=45, bold=True)
_S_TEXT = Style(fg=252)
_S_CONT = Style(fg=242)
_S_CPU = Style(fg=214)
_S_MEM = Style(fg=214)
_S_PLACEHOLDER = Style(fg=242)


def _glow_color(base: int, amp: int) -> int:
    return time_glow(base, base + amp, 12.0)


def _compute_input_rows(text: str, max_input: int) -> int:
    """输入文本换行行数（至少 1）。"""
    if not text:
        return 1
    expanded = _expand_tabs(text)
    wrapped = _wrap_by_width(expanded, max_input)
    return max(1, len(wrapped))


def _wrap_input_text(text: str, max_input: int) -> list[str]:
    if not text:
        return [""]
    expanded = _expand_tabs(text)
    return _wrap_by_width(expanded, max_input)


# ── 测量 ───────────────────────────────────────────


def _completion_height(completion) -> int:
    """补全弹窗高度（标题 + 候选项 + 提示行）。"""
    if completion is None or not completion.visible or not completion.items:
        return 0
    return len(completion.items) + 2


def _measure(fiber, avail_w) -> tuple[int, int]:
    props = fiber.props
    explicit = props.get("width")
    width = max(0, int(explicit)) if explicit is not None else avail_w
    completion = props.get("completion")
    popup_height = _completion_height(completion)
    max_input = max(1, width - len(_PROMPT))
    text = str(props.get("text", ""))
    rows = _compute_input_rows(text, max_input)
    height = popup_height + 2 + rows
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

    # ── 输入文本行 ──
    wrapped = _wrap_input_text(text, max_input)
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
                # ★ 占位符按输入区宽度截断，避免窄终端下超宽被二次换行
                if wcswidth_simple(ph) > max_input:
                    ph = _truncate_width(ph, max_input)
                line.append(ph, Style(fg=_glow_color(242, 10)))
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


__all__ = ["register", "_measure", "_paint", "_compute_input_rows"]
