"""底部栏绘制方法 — 从 _bottom_bar.py 提取的渲染函数。

职责范围：
  - 输入行绘制（_draw_input_lines_locked）
  - 全量底部栏绘制（_draw_all_locked）
  - 补全弹窗轻量重绘（_redraw_cycle_only）
  （无该项）

所有函数通过 `bar` 参数接收 _BottomBar 实例访问内部状态。
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from wcwidth import wcswidth

_logger = logging.getLogger(__name__)

from .blessed import (
    _blessed_cursor_goto,
    _blessed_move_clear,
    _blessed_restore_cursor,
    _blessed_save_cursor,
    _blessed_scroll_down,
    _blessed_scroll_up,
)
from .theme import (
    _BOTTOM_MIN_LINES,
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
]


def _draw_input_lines_locked(
    bar: _BottomBar, out, text: str, r_start: int, term_width: int,
    breath_frame: int = 0,
) -> None:
    """绘制输入行（需持有 output_lock），超长文本自动拆行。

    性能优化：将所有 ANSI 序列收集到缓冲区后一次写入，
    减少高频循环中的独立 write() 系统调用次数。

    Args:
        bar: _BottomBar 实例。
        out: stdout 文件对象。
        text: 输入文本（空字符串显示占位提示）。
        r_start: 第一行输入区的行号（分隔线+状态行之后）。
        term_width: 当前终端宽度（由调用方传入，避免重复系统调用）。
        breath_frame: 呼吸动画帧号（用于提示符颜色变化）。
    """
    max_input = max(1, term_width - 4)
    # 延迟导入避免循环依赖
    from ...terminal.terminal import is_narrow as _is_narrow_fn
    expanded = _expand_tabs(text)
    wrapped = _wrap_by_width(expanded, max_input)
    bar._cached_wrapped_for = text
    bar._cached_wrapped_width = max_input
    bar._cached_wrapped_lines = wrapped
    base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
    bar._cached_input_rows = base_rows + bar._completion.height
    bar._last_rendered_text = text

    # ── 补全弹窗（委托 _CompletionPopup.render） ──
    bar._completion.render(out, r_start, term_width)
    popup_height = bar._completion.height

    # ── 输入文本行（在弹窗下方） ──
    text_start = r_start + popup_height
    # ★ 性能优化：批量收集 ANSI 序列，一次 write
    buf: list[str] = []
    for i, segment in enumerate(wrapped):
        r = text_start + i
        if i == 0:
            if _is_narrow_fn():
                prompt_color = _COLOR_DEEP_CYAN
                prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
            else:
                prompt_color = get_prompt_breath_color(breath_frame)
                prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
            if text:
                buf.append(_blessed_move_clear(r)
                           + prompt_prefix + segment)
            else:
                # ★ 占位符呼吸效果：宽屏使用主题色联动 glow，窄屏保持静态
                if _is_narrow_fn():
                    placeholder_color = _COLOR_DIM
                else:
                    import re
                    from ...core.text_utils import build_glow_ansi  # type: ignore[import-untyped]
                    from ...core.theme import THEME as _BOTTOM_THEME       # type: ignore[import-untyped]
                    glow_str = _BOTTOM_THEME.get('placeholder_glow', '')
                    m = re.search(r"38;5;(\d+)", glow_str)
                    if m:
                        base = int(m.group(1))
                        placeholder_color = build_glow_ansi(breath_frame, base, 12)
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
        bar._cursor_tracker.set(r, 3)  # 提示符从第3列开始
    # ★ 填充剩余空白行，确保输入区至少 3 行
    for r in range(text_start + len(wrapped), text_start + 3):
        buf.append(_blessed_move_clear(r) + "  ")
        bar._cursor_tracker.set(r, 1)
    if buf:
        out.write(''.join(buf))


# 【技术债】_build_sep_with_system_stats() 约 150 行，复杂度合理但体量偏大。
# 当前不拆分，后续若增加更多效果类型可考虑按效果路径拆分为子函数：
#   - _build_sep_static() — 静态渐变分隔线
#   - _build_sep_composed() — EffectRegistry 组合效果分隔线
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
) -> str:
    """构建带主Agent状态、CPU/MEM 系统信息和当前时间的分隔线。

    ── 布局策略（始终存在的信息） ──
      行尾：CPU 使用率 + MEM 使用率 + 当前时间（始终显示）

    ── 流式输出期间（bar._status_active=True 或工具有运行） ──
      行首优先级：工具调用中（工具有运行） > 主Agent阶段（思考/回答/接收工具参数）
      布局：``  工具调用中 2.10s  ━━(渐变)━━  CPU: 23% · MEM: 45%  2027-1-1 00:00:01  ``

    ── 非流式 / 空闲期间 ──
      行首：纯渐变分隔线
      布局：``  ━━(渐变)━━  CPU: 23% · MEM: 45%  2027-1-1 00:00:01  ``

    空间不足时（窄屏 / 宽度 < 60 列）：回退到纯渐变分隔线，跳过所有信息。

    Args:
        tw: 终端宽度（列数）。
        sep_start: 分隔线起始 256 色号。
        cpu_percent: CPU 使用率（0.0 ~ 100.0）。
        mem_percent: 内存使用率（0.0 ~ 100.0）。
        char: 分隔线字符，默认 ━ (U+2501)。
        narrow: 是否为窄屏模式，窄屏时跳过信息嵌入。
        bar: _BottomBar 实例，用于读取主Agent阶段和流式状态。

    Returns:
        完整的带 ANSI 颜色的分隔线字符串（含前导 ``  `` 缩进和尾部 RESET）。
    """
    import time as _time

    available = max(1, tw - 2)  # 去除前导 2 空格

    # ── 构建行尾信息：CPU/MEM + 当前时间（始终存在） ──
    now_local = _time.localtime()
    time_str = (
        f"{now_local.tm_year}-{now_local.tm_mon}-"
        f"{now_local.tm_mday} {now_local.tm_hour:02d}:"
        f"{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
    )
    time_info = f" {_COLOR_DIM}{time_str}{_COLOR_RESET} "

    cpu_int = max(0, min(100, round(cpu_percent)))
    mem_int = max(0, min(100, round(mem_percent)))
    cpu_str = str(cpu_int)
    mem_str = str(mem_int)

    cpu_mem_info = (
        f" {_COLOR_ACCENT}CPU:{_COLOR_RESET}"
        f" {_COLOR_SPEED}{cpu_str}{_COLOR_ACCENT}%{_COLOR_RESET}"
        f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        f"{_COLOR_ACCENT}MEM:{_COLOR_RESET}"
        f" {_COLOR_SPEED}{mem_str}{_COLOR_ACCENT}%{_COLOR_RESET}"
    )
    right_info = cpu_mem_info + time_info
    right_w = _visible_width(right_info)

    # ── 窄屏 / 空间不足 → 纯渐变分隔线 ──
    if narrow or right_w >= available - 6:
        fallback_sep = make_sep_gradient(available, start_color=sep_start, char=char)
        return f"  {fallback_sep}"

    # ── 判断是否显示阶段状态（行首） ──
    # 流式期间或工具有运行时，在行首显示阶段状态 + 耗时
    is_streaming = bar is not None and bar._status_active
    has_tools = bar is not None and bar._tool_count > 0
    show_status = is_streaming or has_tools

    if show_status:
        # ── 构建行首：阶段状态 + 实时耗时 ──
        phase = bar._main_phase if bar else ""
        phase_start = bar._main_phase_start if bar else 0.0
        tool_count = bar._tool_count if bar else 0

        # 阶段名 → 中文映射
        phase_map = {
            "thinking":  "思考",
            "answering": "回答",
            "parsing":   "接收工具参数",
        }
        now = _time.monotonic()

        # ★ 工具有运行时优先显示"工具调用中"（无论当前 phase 是什么）
        if tool_count > 0:
            tool_elapsed = (
                now - bar._tool_phase_start
                if bar and bar._tool_phase_start > 0
                else 0.0
            )
            left_info = (
                f" {_COLOR_ACCENT}工具调用中{_COLOR_RESET}"
                f" {_COLOR_TIME}{tool_elapsed:.2f}s{_COLOR_RESET} "
            )
        elif phase in phase_map:
            phase_label = phase_map[phase]
            phase_elapsed = now - phase_start if phase_start > 0 else 0.0
            left_info = (
                f" {_COLOR_SPEED}{phase_label}{_COLOR_RESET}"
                f" {_COLOR_TIME}{phase_elapsed:.2f}s{_COLOR_RESET} "
            )
        else:
            left_info = ""

        # ── 有阶段信息 → 布局：左信息 + 渐变线 + 右侧 CPU/MEM + 时间 ──
        if left_info:
            left_w = _visible_width(left_info)
            sep_w = available - left_w - right_w
            if sep_w >= 4:
                sep_str = _build_sep_gradient_or_compose(
                    sep_start, sep_w, breath_frame, char, suffix_reset=True
                )
                return f"  {left_info}{sep_str}{right_info}"
            # 空间不足时回退到右侧信息 + 纯分隔线
            # 不单独 return，让 fallthrough 到下方通用路径

    # ── 非流式 / 无阶段 / 空间不足：渐变线 + 右侧 CPU/MEM + 时间 ──
    # 布局：渐变分隔线 + CPU/MEM 信息 + 时间
    sep_w = available - right_w
    if sep_w < 4:
        fallback_sep = make_sep_gradient(available, start_color=sep_start, char=char)
        return f"  {fallback_sep}"

    sep_str = _build_sep_gradient_or_compose(
        sep_start, sep_w, breath_frame, char, suffix_reset=False
    )
    return f"  {sep_str}{right_info}"


def _build_sep_gradient_or_compose(
    sep_start: int,
    sep_w: int,
    breath_frame: int,
    char: str,
    suffix_reset: bool = False,
) -> str:
    """构建分隔线渐变或组合效果（消除重复代码）。

    当 breath_frame > 0 时使用 EffectRegistry 组合效果（aurora+shimmer），
    否则回退到静态 gradient_range 渐变。
    """
    if breath_frame > 0:
        from ...core.effects import EffectRegistry
        try:
            composed = EffectRegistry.compose(
                ["aurora", "shimmer"], frame=breath_frame, length=sep_w
            )
            return build_gradient_ansi(composed, char=char, suffix_reset=suffix_reset)
        except Exception:
            _logger.warning(
                "EffectRegistry.compose() 失败，回退到静态渐变", exc_info=True
            )
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

    布局（简约风）：
      第 1 行：左青右灰渐变分隔线（内容区与输入区的视觉边界）
      第 2 行：状态行（模型名·耗时·令牌数，青/灰两色）
      第 3 行起：青 ❯ <text>   （输入提示符 + 实时键入文本，超长拆行）
                 灰 · <text>    （续行，· 前缀）
                 （空输入时显示灰色占位提示）

    终端高度不足以容纳底部栏时跳过绘制。

    性能优化：批量收集 ANSI 序列后一次写入，减少独立 write() 次数。

    Args:
        bar: _BottomBar 实例。
        out: stdout 文件对象。
        height: 终端高度。
        breath_frame: 呼吸动画帧号（用于提示符颜色变化）。
    """
    total = bar._bottom_lines
    if height - total < 1:
        return
    bar._last_bottom_lines = total
    r1 = height - total + 1
    subagent_start = r1 + 1
    r2 = subagent_start + len(bar._subagent_lines)

    # ★ 批量收集清行序列
    buf: list[str] = []
    for r in range(r1, height + 1):
        buf.append(_blessed_move_clear(r))

    tw = bar._term_width()
    # 延迟导入避免循环依赖
    from ...terminal.terminal import is_narrow as _is_narrow_fn
    sep_start = 45  # 默认青色
    if _is_narrow_fn():
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        buf.append(_blessed_cursor_goto(r1, 1) + "  " + sep)
    else:
        # 分隔线增强：breath_frame>0 时使用波动效果 + 呼吸起始色
        if breath_frame > 0:
            sep_start = bar._animator.sine_color(40, 45, 10) if bar else 40

        # 嵌入 CPU/内存使用率信息
        cpu_pct = getattr(bar, '_cached_cpu_percent', 0.0)
        mem_pct = getattr(bar, '_cached_mem_percent', 0.0)
        sep = _build_sep_with_system_stats(
            tw, sep_start, cpu_pct, mem_pct, bar=bar,
            breath_frame=breath_frame,
        )
        buf.append(_blessed_cursor_goto(r1, 1) + sep)

    # ── subagent 面板行（在分隔线与状态行之间） ──
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
    """仅重绘补全弹窗高亮变化（轻量路径，调用方须持有 output_lock）。

    与 force_redraw() 不同，此方法仅更新弹窗行的选中高亮
    和快捷键提示行，不重绘分隔线/状态行/输入区。

    由 render 线程在 CYCLE_COMPLETION 命令 handler 中调用。

    Args:
        bar: _BottomBar 实例。
    """
    if not bar._completion.is_visible or not bar._completion._items:
        return
    out = sys.__stdout__
    out.write(_blessed_save_cursor())
    height = bar._term_height()
    total = bar._bottom_lines
    popup_start = height - total + 3
    tw = bar._term_width()
    bar._completion.render_cycle_update(out, popup_start, tw)
    out.write(_blessed_restore_cursor())
    out.flush()
    bar._last_height = height
