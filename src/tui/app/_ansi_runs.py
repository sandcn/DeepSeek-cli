"""_ansi_runs — ANSI（SGR）字符串 → ink ``StyledRun`` 行解析。

部分运行期产物是**已渲染的 ANSI 文本**（如 ``tui._diff_renderer`` 的
``render_diff_to_ansi`` 返回的 diff 块），要在 React Ink 视图里原样展示需
把 SGR 颜色/样式转回 ``StyledRun``。本模块提供纯函数解析器——不依赖
renderer.ansi 的 ``AnsiLine``（那条路径需要结构化对象，而这里只有字符串）。

支持：基础/亮色 30-37/90-97、40-47/100-107，256 色 ``38;5;n``/``48;5;n``，
真彩 ``38;2;r;g;b``/``48;2;r;g;b``，以及 bold/dim/italic/underline 与 reset。

依赖约束：仅依赖 tui 核心 style/color 与 ink 输出模型——无 app 反向依赖。
"""

from __future__ import annotations

import re

from src.tui.core.color import TrueColor
from src.tui.core.style import Style
from src.tui.ink import StyledRun

__all__ = ["ansi_line_to_runs", "ansi_text_to_rows"]

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

#: 基础前景 30-37 → 256 色号。
_FG_BASIC = {30: 0, 31: 1, 32: 2, 33: 3, 34: 4, 35: 5, 36: 6, 37: 7}
#: 亮色前景 90-97 → 256 色号。
_FG_BRIGHT = {90: 8, 91: 9, 92: 10, 93: 11, 94: 12, 95: 13, 96: 14, 97: 15}
#: 基础背景 40-47 → 256 色号。
_BG_BASIC = {40: 0, 41: 1, 42: 2, 43: 3, 44: 4, 45: 5, 46: 6, 47: 7}
#: 亮色背景 100-107 → 256 色号。
_BG_BRIGHT = {100: 8, 101: 9, 102: 10, 103: 11, 104: 12, 105: 13, 106: 14, 107: 15}


class _SGR:
    """SGR 状态累积器。"""

    __slots__ = ("fg", "bg", "bold", "dim", "italic", "underline")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.fg = None
        self.bg = None
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False

    def style(self) -> Style:
        if not (self.fg or self.bg or self.bold or self.dim or self.italic or self.underline):
            return None
        return Style(
            fg=self.fg, bg=self.bg, bold=self.bold, dim=self.dim,
            italic=self.italic, underline=self.underline,
        )


def _apply_codes(state: _SGR, codes: list) -> None:
    """按序应用一组 SGR 码（支持 38/48 的 5;n 与 2;r;g;b 扩展）。"""
    i = 0
    n = len(codes)
    while i < n:
        try:
            c = int(codes[i])
        except (TypeError, ValueError):
            i += 1
            continue
        if c == 0:
            state.reset()
        elif c == 1:
            state.bold = True
        elif c == 2:
            state.dim = True
        elif c == 3:
            state.italic = True
        elif c == 4:
            state.underline = True
        elif c == 22:
            state.bold = False
            state.dim = False
        elif c == 23:
            state.italic = False
        elif c == 24:
            state.underline = False
        elif c == 39:
            state.fg = None
        elif c == 49:
            state.bg = None
        elif c in _FG_BASIC:
            state.fg = _FG_BASIC[c]
        elif c in _FG_BRIGHT:
            state.fg = _FG_BRIGHT[c]
        elif c in _BG_BASIC:
            state.bg = _BG_BASIC[c]
        elif c in _BG_BRIGHT:
            state.bg = _BG_BRIGHT[c]
        elif c in (38, 48):
            target = "fg" if c == 38 else "bg"
            if i + 1 < n and codes[i + 1] == "5" and i + 2 < n:
                try:
                    setattr(state, target, int(codes[i + 2]) & 0xFF)
                except (TypeError, ValueError):
                    pass
                i += 3
                continue
            if i + 1 < n and codes[i + 1] == "2" and i + 4 < n:
                try:
                    rgb = (int(codes[i + 2]), int(codes[i + 3]), int(codes[i + 4]))
                    setattr(state, target, TrueColor(*rgb))
                except Exception:
                    pass
                i += 5
                continue
        i += 1


def ansi_line_to_runs(text: str) -> list:
    """单行 ANSI 文本 → ``list[StyledRun]``（无样式段用 ``None`` 样式）。

    空文本返回空列表；解析异常按纯文本处理（不中断渲染）。
    """
    if not text:
        return []
    try:
        return _parse(text)
    except Exception:
        return [StyledRun(str(text), None)]


def _parse(text: str) -> list:
    runs: list = []
    state = _SGR()
    pos = 0
    for m in _SGR_RE.finditer(text):
        if m.start() > pos:
            seg = text[pos:m.start()]
            if seg:
                runs.append(StyledRun(seg, state.style()))
        raw = m.group(1)
        codes = raw.split(";") if raw else ["0"]
        _apply_codes(state, codes)
        pos = m.end()
    if pos < len(text):
        seg = text[pos:]
        if seg:
            runs.append(StyledRun(seg, state.style()))
    return runs


def ansi_text_to_rows(text: str) -> list:
    """多行 ANSI 文本 → ``list[list[StyledRun]]``（每行一个 runs 列表）。"""
    if not text:
        return []
    return [ansi_line_to_runs(line) for line in str(text).split("\n")]
