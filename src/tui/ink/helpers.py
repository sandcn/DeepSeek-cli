"""ink 工具函数 — ANSI 剥离 / 宽度测量 / 换行截断。

所有宽度计算统一走 ``_screen.wcswidth_simple``（唯一宽度依据）。
ANSI 转义序列不占显示宽度，测量前需先剥离或识别。
"""

from __future__ import annotations

import re

from src.tui._screen import wcswidth_simple
from src.tui.core.style import Style
from .output import StyledRun, Line, Frame

# ANSI 转义序列（SGR 颜色/属性 + 光标控制）
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)


def strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回纯文本。"""
    return _ANSI_RE.sub("", text)


def has_ansi(text: str) -> bool:
    """是否包含 ANSI 转义序列。"""
    return "\x1b" in text


def visual_width(text: str) -> int:
    """字符串显示宽度（先剥离 ANSI，再按 wcswidth_simple 测量）。"""
    return wcswidth_simple(strip_ansi(text))


def wrap_runs_by_width(runs: list[StyledRun], max_width: int) -> list[Line]:
    """将 StyledRun 序列按显示宽度换行为多行。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 每行最大显示宽度；<=0 表示不换行。

    Returns:
        换行后的 Line 列表。
    """
    if max_width <= 0:
        return [Line(runs)] if runs else []
    lines: list[Line] = []
    current = Line()
    current_width = 0
    for run in runs:
        if not run.text:
            continue
        # 单个 run 内按字符拆（保持样式一致性，逐字符累积宽度）
        text = run.text
        buf = ""
        buf_width = 0
        for ch in text:
            cw = wcswidth_simple(ch)
            if current_width + buf_width + cw > max_width and (current.runs or buf):
                if buf:
                    current.append(buf, run.style)
                    buf = ""
                    buf_width = 0
                lines.append(current)
                current = Line()
                current_width = 0
            buf += ch
            buf_width += cw
        if buf:
            current.append(buf, run.style)
            current_width += buf_width
    if current.runs:
        lines.append(current)
    return lines


def truncate_runs(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """将 StyledRun 序列截断至 max_width 显示宽度（保持样式）。

    超宽部分丢弃；截断点在字符边界，不拆分宽字符（CJK）。
    """
    if max_width < 0:
        return []
    out: list[StyledRun] = []
    width = 0
    for run in runs:
        if width >= max_width:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
    return out


def truncate_line(line: Line, max_width: int) -> Line:
    """将行截断至 max_width 显示宽度（保持样式）。

    超宽部分丢弃；宽度不足时原样返回。截断点在字符边界，
    不拆分宽字符（CJK）。
    """
    if max_width < 0:
        return Line()
    if line.width <= max_width:
        return line.clone()
    out = Line()
    width = 0
    for run in line.runs:
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                return out
            out.append(ch, run.style)
            width += cw
    return out


def pad_line(line: Line, width: int) -> Line:
    """将行填充至指定宽度（不足补空格；已超宽则截断）。"""
    out = truncate_line(line, width)
    pad = width - out.width
    if pad > 0:
        out.append(" " * pad)
    return out


def line_to_ansi(line: Line) -> str:
    """Line → ANSI 字符串（含行末样式重置）。"""
    return line.render()


__all__ = [
    "strip_ansi",
    "has_ansi",
    "visual_width",
    "wrap_runs_by_width",
    "truncate_runs",
    "truncate_line",
    "pad_line",
    "line_to_ansi",
]
