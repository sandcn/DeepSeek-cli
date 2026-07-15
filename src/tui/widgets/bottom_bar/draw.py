"""底部栏绘制方法 — inline 模式渲染函数。

【inline 模式 · 2026-07-16 重构】

职责范围：
  - 输入行绘制（_draw_input_lines_locked）—— 保留接口兼容
  - 全量底部栏绘制（_draw_all_locked）—— 保留接口兼容
  - 补全弹窗轻量重绘（_redraw_cycle_only）—— inline CUP 模式
  - 分隔线构建（_build_sep_with_system_stats）
  - 状态文本构建（_build_status_text）
  - 可视宽度计算（_visible_width）

所有函数通过 `bar` 参数接收 _BottomBar 实例访问内部状态。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING

from wcwidth import wcswidth

_logger = logging.getLogger(__name__)

from .blessed import (
    _blessed_cursor_goto,
    _blessed_move_clear,
)
from .theme import (
    _COLOR_ACCENT,
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _COLOR_SPEED,
    _COLOR_TIME,
    _MIN_INPUT_ROWS,
    _PLACEHOLDER_COMPACT,
    _PLACEHOLDER_STREAMING,
    _PLACEHOLDER_TEXT,
    get_prompt_breath_color,
    make_sep_gradient,
)
from ...core.text_utils import build_gradient_ansi
from ...core.gradient import gradient_range
from .cursor import (
    _expand_tabs,
    _wrap_by_width,
)

if TYPE_CHECKING:
    from .bar import _BottomBar


__all__ = [
    "_draw_input_lines_locked",
    "_draw_all_locked",
    "_redraw_cycle_only",
    "_build_sep_with_system_stats",
    "_build_status_text",
    "_visible_width",
]


def _draw_input_lines_locked(
    bar: _BottomBar, out, text: str, r_start: int, term_width: int,
    breath_frame: int = 0,
) -> None:
    """绘制输入行（需持有 output_lock），超长文本自动拆行。

    .. deprecated::
        inline 模式由 bar._build_input_area_lines() 统一处理。
        此函数仅保留接口兼容性，新代码不应调用。

    Args:
        bar: _BottomBar 实例。
        out: stdout 文件对象。
        text: 输入文本（空字符串显示占位提示）。
        r_start: 第一行输入区的行号。
        term_width: 当前终端宽度。
        breath_frame: 呼吸动画帧号。
    """
    import warnings
    warnings.warn("deprecated DECSTBM path invoked", DeprecationWarning, stacklevel=2)
    _logger.warning("deprecated DECSTBM path invoked")
    from ...terminal.terminal import is_narrow as _is_narrow_fn

    max_input = max(1, term_width - 4)
    expanded = _expand_tabs(text)
    wrapped = _wrap_by_width(expanded, max_input)
    bar._cached_wrapped_for = text
    bar._cached_wrapped_width = max_input
    bar._cached_wrapped_lines = wrapped
    base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
    bar._cached_input_rows = base_rows + bar._completion.height + 2
    bar._last_rendered_text = text

    # ── 补全弹窗（委托 _CompletionPopup.render） ──
    bar._completion.render(out, r_start, term_width)
    popup_height = bar._completion.height

    buf: list[str] = []

    # ── 上分割线（行尾带 CPU · MEM 信息） ──
    text_start = r_start + popup_height
    cpu_int = max(0, min(100, round(getattr(bar, '_cached_cpu_percent', 0.0))))
    mem_int = max(0, min(100, round(getattr(bar, '_cached_mem_percent', 0.0))))
    cpu_mem_info = (
        f" {_COLOR_ACCENT}CPU:{_COLOR_RESET}"
        f" {_COLOR_SPEED}{cpu_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
        f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        f"{_COLOR_ACCENT}MEM:{_COLOR_RESET}"
        f" {_COLOR_SPEED}{mem_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
    )
    cpu_mem_w = _visible_width(cpu_mem_info)
    top_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, term_width - cpu_mem_w) + cpu_mem_info
    buf.append(_blessed_move_clear(text_start) + top_sep)
    bar._cursor_tracker.set(text_start, 1)

    # ── 输入文本行 ──
    for i, segment in enumerate(wrapped):
        r = text_start + 1 + i
        if i == 0:
            if _is_narrow_fn():
                prompt_color = _COLOR_DEEP_CYAN
                prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
            else:
                prompt_color = get_prompt_breath_color(breath_frame)
                prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
            if text:
                buf.append(_blessed_move_clear(r) + prompt_prefix + segment)
            else:
                if _is_narrow_fn():
                    placeholder_color = _COLOR_DIM
                else:
                    import re
                    from ...core.text_utils import build_glow_ansi
                    from ...core.theme import THEME as _BOTTOM_THEME
                    glow_str = _BOTTOM_THEME.get('placeholder_glow', '')
                    m = re.search(r"38;5;(\d+)", glow_str)
                    if m:
                        base_val = int(m.group(1))
                        placeholder_color = build_glow_ansi(breath_frame, base_val, 12)
                    else:
                        placeholder_color = _COLOR_DIM
                if bar._status_active:
                    ph = _PLACEHOLDER_STREAMING
                    buf.append(_blessed_move_clear(r)
                               + prompt_prefix + f"{placeholder_color}{ph}\033[0m")
                else:
                    ph = _PLACEHOLDER_COMPACT if bar._completion.is_visible else _PLACEHOLDER_TEXT
                    buf.append(_blessed_move_clear(r)
                               + prompt_prefix + f"{placeholder_color}{ph}\033[0m")
        else:
            buf.append(_blessed_move_clear(r)
                       + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
        bar._cursor_tracker.set(r, 3)

    # ── 下分割线（行尾带时间戳） ──
    now_local = time.localtime()
    ts = (
        f"{now_local.tm_year}-{now_local.tm_mon}-"
        f"{now_local.tm_mday} {now_local.tm_hour:02d}:"
        f"{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
    )
    time_info = f" {_COLOR_DIM}{ts}{_COLOR_RESET}"
    time_w = _visible_width(time_info)
    bottom_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, term_width - time_w) + time_info
    bottom_sep_row = text_start + 1 + base_rows
    buf.append(_blessed_move_clear(bottom_sep_row) + bottom_sep)
    bar._cursor_tracker.set(bottom_sep_row, 1)

    # ── 填充剩余空白行 ──
    for r in range(text_start + 1 + len(wrapped), text_start + 1 + base_rows):
        buf.append(_blessed_move_clear(r) + "  ")
        bar._cursor_tracker.set(r, 1)

    if buf:
        out.write(''.join(buf))


# ── 状态分隔线 ──────────────────────────────────────────────

_PHASE_DISPLAY: dict[str, str] = {
    "thinking": "思考",
    "answering": "回答",
    "parsing": "接收工具参数",
}


def _build_status_text(bar: _BottomBar) -> str:
    """根据 bar 的状态构建分隔线状态文本（纯文本，不含 ANSI 颜色）。

    优先级（从高到低）：
      1. 工具调用中（_tool_count > 0）→ "工具调用中"
      2. 主Agent阶段（_main_phase in _PHASE_DISPLAY）→ 映射文本
      3. 其他情况 → 返回空字符串
    """
    if bar._tool_count > 0:
        status = "工具调用中"
        start_time = bar._tool_phase_start
    elif bar._main_phase in _PHASE_DISPLAY:
        status = _PHASE_DISPLAY[bar._main_phase]
        start_time = bar._main_phase_start
    else:
        return ""

    if start_time <= 0.0:
        return ""

    elapsed = time.monotonic() - start_time
    return f"\u00b7 {status} {elapsed:.2f}s"


def _build_sep_with_system_stats(
    tw: int,
    sep_start: int,
    cpu_percent: float,
    mem_percent: float,
    *,
    char: str = "\u2501",
    narrow: bool = False,
    bar: _BottomBar | None = None,
    breath_frame: int = 0,
    status_text: str = "",
    status_active: bool = False,
) -> str:
    """构建分隔线（支持状态文本前缀模式）。

    两种构建策略：
      - 状态分隔线：status_active=True 且 status_text 非空且非窄屏
      - 纯渐变分隔线：其余情况
    """
    available = max(1, tw - 2)

    if status_active and status_text and not narrow:
        status_colored = f"{_COLOR_ACCENT}{status_text}{_COLOR_RESET}"
        status_visual_width = _visible_width(status_text)
        remaining = max(1, available - status_visual_width - 1)
        sep = make_sep_gradient(remaining, start_color=sep_start, char=char)
        return f"  {status_colored} {sep}"

    sep = _build_sep_gradient_or_compose(sep_start, available, breath_frame, char)
    return f"  {sep}"


def _build_sep_gradient_or_compose(
    sep_start: int,
    sep_w: int,
    breath_frame: int,
    char: str,
    suffix_reset: bool = False,
) -> str:
    """构建分隔线渐变或组合效果。

    三档降级策略：
      1. 尝试 aurora + shimmer + neon 三合一动效（最丰富）
      2. 失败则降级为 aurora + shimmer 二合一
      3. 仍失败则降级为静态渐变
    """
    if breath_frame > 0:
        from ...core.effects import EffectRegistry
        # ── 第 1 档：三合一动效 ──
        try:
            composed = EffectRegistry.compose(
                ["aurora", "shimmer", "neon"], frame=breath_frame, length=sep_w
            )
            return build_gradient_ansi(composed, char=char, suffix_reset=suffix_reset)
        except Exception:
            # ── 第 2 档：二合一降级 ──
            try:
                composed = EffectRegistry.compose(
                    ["aurora", "shimmer"], frame=breath_frame, length=sep_w
                )
                return build_gradient_ansi(composed, char=char, suffix_reset=suffix_reset)
            except Exception:
                _logger.warning(
                    "EffectRegistry.compose() 失败，回退到静态渐变", exc_info=True
                )
    # ── 第 3 档：静态渐变 ──
    colors = gradient_range(sep_start, 237, sep_w)
    return build_gradient_ansi(colors, char=char, suffix_reset=suffix_reset)


def _visible_width(text: str) -> int:
    """计算字符串的可视宽度（去除 ANSI 转义序列）。"""
    import re
    clean = re.sub(r'\033\[[0-9;]*m', '', text)
    w = wcswidth(clean)
    return w if w >= 0 else len(clean)


def _draw_all_locked(bar: _BottomBar, out, height: int, breath_frame: int = 0) -> None:
    """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

    .. deprecated::
        inline 模式由 bar._build_all_lines() 统一处理。
        此函数仅保留接口兼容性，新代码不应调用。
    """
    import warnings
    warnings.warn("deprecated", DeprecationWarning, stacklevel=2)
    _logger.warning("deprecated DECSTBM path invoked")
    from ...terminal.terminal import is_narrow as _is_narrow_fn

    total = bar._bottom_lines
    if height - total < 1:
        return
    bar._last_rendered_total_lines = total
    r1 = height - total + 1
    subagent_start = r1 + 1
    r2 = subagent_start + len(bar._subagent_lines)

    buf: list[str] = []
    for r in range(r1, height + 1):
        buf.append(_blessed_move_clear(r))

    tw = bar._term_width()
    sep_start = 45
    if _is_narrow_fn():
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        buf.append(_blessed_cursor_goto(r1, 1) + "  " + sep)
    else:
        if breath_frame > 0:
            sep_start = bar._animator.sine_color(40, 45, 10) if bar else 40
        cpu_pct = getattr(bar, '_cached_cpu_percent', 0.0)
        mem_pct = getattr(bar, '_cached_mem_percent', 0.0)
        status_active = getattr(bar, '_status_active', False)
        status_text = _build_status_text(bar) if status_active else ""
        sep = _build_sep_with_system_stats(
            tw, sep_start, cpu_pct, mem_pct, bar=bar,
            breath_frame=breath_frame,
            status_text=status_text,
            status_active=status_active,
            narrow=_is_narrow_fn(),
        )
        buf.append(_blessed_cursor_goto(r1, 1) + sep)

    for i, line in enumerate(bar._subagent_lines):
        sr = subagent_start + i
        buf.append(_blessed_move_clear(sr) + line)

    status = bar._format_status()
    bar._last_status = status
    if status:
        if breath_frame > 0 and not _is_narrow_fn():
            dot_color = bar._animator.sine_color(45, 81, 12) if bar else 45
            dot_ansi = f"\033[38;5;{dot_color}m\u00b7{_COLOR_RESET}"
            buf.append(_blessed_move_clear(r2) + status + " " + dot_ansi)
        else:
            buf.append(_blessed_move_clear(r2) + status)

    if buf:
        out.write(''.join(buf))

    text = bar._last_text or ""
    _draw_input_lines_locked(bar, out, text, r2 + 1, tw, breath_frame)


def _redraw_cycle_only(bar: _BottomBar) -> None:
    """仅重绘补全弹窗高亮变化（inline 模式 — 委托 force_redraw）。

    inline 模式下无全屏帧，bar._last_rendered_total_lines 含义已变
    （光标到栏顶距离而非栏高），无法用旧 CUP 公式计算。直接委托
    force_redraw 全量重绘，性能差异可忽略。
    """
    bar.force_redraw()
