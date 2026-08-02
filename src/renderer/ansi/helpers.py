"""ansi 工具 — Run/AnsiLine 输出模型 + 换行/截断/测量 + ANSI→样式解析。

本模块自绘 ANSI（零 Rich、零 tui 依赖）：输出模型为 AnsiLine（Run 序列），
宽度依据统一用 renderer._utils.cjk_display_width，样式用本包自含 Style。
"""

from __future__ import annotations

import re
from src._compat import dataclass
from dataclasses import field

from src.renderer._utils import cjk_display_width
from .style import Style


# ═══════════════════════════════════════════════════════════
# Run / AnsiLine — 输出模型
# ═══════════════════════════════════════════════════════════


@dataclass
class Run:
    """一段带样式的文本。"""

    text: str
    style: Style | None = None

    def render(self) -> str:
        if self.style:
            return self.style.apply(self.text)
        return self.text

    @property
    def width(self) -> int:
        return cjk_display_width(self.text)


class AnsiLine:
    """一行输出（Run 序列）。"""

    __slots__ = ("runs",)

    def __init__(self, runs: list[Run] | None = None) -> None:
        self.runs: list[Run] = list(runs) if runs else []

    @classmethod
    def of(cls, text: str, style: Style | None = None) -> "AnsiLine":
        return cls([Run(text, style)])

    def append(self, text: str, style: Style | None = None) -> None:
        if not text:
            return
        if self.runs and self.runs[-1].style == style:
            self.runs[-1] = Run(self.runs[-1].text + text, style)
            return
        self.runs.append(Run(text, style))

    def append_run(self, run: Run) -> None:
        if run and run.text:
            self.append(run.text, run.style)

    def render(self) -> str:
        return "".join(r.render() for r in self.runs)

    @property
    def plain(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def width(self) -> int:
        return sum(r.width for r in self.runs)

    def clone(self) -> "AnsiLine":
        return AnsiLine(list(self.runs))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"AnsiLine({self.plain!r})"


# ═══════════════════════════════════════════════════════════
# 宽度 / 换行 / 截断
# ═══════════════════════════════════════════════════════════


def visual_width(text: str) -> int:
    """纯字符串显示宽度（剥离 ANSI 后测量）。"""
    return cjk_display_width(strip_ansi(text))


_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def wrap_line(line: AnsiLine, max_width: int) -> list[AnsiLine]:
    """将 AnsiLine 按显示宽度换行为多行（CJK 安全）。"""
    if max_width <= 0:
        return [line] if line.runs else []
    lines: list[AnsiLine] = []
    current = AnsiLine()
    current_width = 0
    for run in line.runs:
        for ch in run.text:
            cw = cjk_display_width(ch)
            if current_width + cw > max_width and current.runs:
                lines.append(current)
                current = AnsiLine()
                current_width = 0
            current.append(ch, run.style)
            current_width += cw
    if current.runs:
        lines.append(current)
    return lines


def truncate_line(line: AnsiLine, max_width: int) -> AnsiLine:
    """截断 AnsiLine 至 max_width（CJK 安全，宽字符不拆）。"""
    if max_width < 0:
        return AnsiLine()
    if line.width <= max_width:
        return line.clone()
    out = AnsiLine()
    width = 0
    for run in line.runs:
        for ch in run.text:
            cw = cjk_display_width(ch)
            if width + cw > max_width:
                return out
            out.append(ch, run.style)
            width += cw
    return out


def pad_line(line: AnsiLine, width: int) -> AnsiLine:
    """填充至指定宽度（不足补空格，超宽截断）。"""
    out = truncate_line(line, width)
    pad = width - out.width
    if pad > 0:
        out.append(" " * pad)
    return out


# ═══════════════════════════════════════════════════════════
# ANSI → Style 解析（紧急回退：带 ANSI 的纯文本转 Run 序列）
# ═══════════════════════════════════════════════════════════

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def parse_sgr_params(params: str) -> tuple[Style | None, bool]:
    """解析 SGR 参数串（'' 表示 0）为 Style 增量。

    Returns:
        (style, is_reset)：is_reset 表示遇到 0（重置）。
    """
    if not params:
        return (None, True)
    reset = False
    fg: int | tuple[int, int, int] | None = None
    bg: int | tuple[int, int, int] | None = None
    bold = italic = dim = underline = False
    parts = params.split(";")
    i = 0
    while i < len(parts):
        p = parts[i]
        # 0 / 00 / 000 → 重置（Rich 有时输出 \x1b[39;49;00m 形式的重置）
        if p == "" or (p.isdigit() and int(p) == 0):
            reset = True
        elif p == "1":
            bold = True
        elif p == "2":
            dim = True
        elif p == "3":
            italic = True
        elif p == "4":
            underline = True
        elif p == "38" or p == "48":
            if i + 1 < len(parts) and parts[i + 1] == "5" and i + 2 < len(parts):
                try:
                    n = int(parts[i + 2])
                    if p == "38":
                        fg = n
                    else:
                        bg = n
                except ValueError:
                    pass
                i += 2
            elif i + 1 < len(parts) and parts[i + 1] == "2" and i + 4 < len(parts):
                try:
                    rgb = (int(parts[i + 2]), int(parts[i + 3]), int(parts[i + 4]))
                    if p == "38":
                        fg = rgb
                    else:
                        bg = rgb
                except ValueError:
                    pass
                i += 4
        elif p.isdigit():
            n = int(p)
            if 30 <= n <= 37:
                fg = n
            elif 90 <= n <= 97:
                fg = n - 90 + 8
            elif 40 <= n <= 47:
                bg = n - 40
            elif 100 <= n <= 107:
                bg = n - 100 + 8
        i += 1
    style = Style(fg=fg, bg=bg, bold=bold, italic=italic, dim=dim, underline=underline)
    return (style, reset)


def ansi_to_runs(text: str, base_style: Style | None = None) -> list[Run]:
    """将含 ANSI 转义序列的文本解析为 Run 序列（紧急回退）。"""
    runs: list[Run] = []
    current = Style() if base_style is None else base_style
    buf = ""
    pos = 0
    for m in _SGR_RE.finditer(text):
        if m.start() > pos:
            buf += text[pos:m.start()]
        pos = m.end()
        if buf:
            runs.append(Run(buf, current) if current else Run(buf, None))
            buf = ""
        style, is_reset = parse_sgr_params(m.group(1))
        if is_reset:
            # ★ 组合 SGR「重置 + 颜色」修复（方向1）：``\x1b[0;31m`` 等
            #   （Pygments/Rich 常输出）——终端语义先 reset 再应用颜色。
            #   修复前直接 ``current = Style()`` 把同序列解析出的 fg=31 丢弃，
            #   reset 后文本渲染成默认色而非红色。
            current = Style() if base_style is None else base_style
            if style:
                current = current.merge(style)
        else:
            current = current.merge(style) if style else current
    if pos < len(text):
        buf += text[pos:]
    if buf:
        runs.append(Run(buf, current) if current else Run(buf, None))
    return runs


def ansi_to_line(text: str, base_style: Style | None = None) -> AnsiLine:
    """将含 ANSI 的文本转为 AnsiLine（紧急回退）。"""
    return AnsiLine(ansi_to_runs(text, base_style))


__all__ = [
    "Run",
    "AnsiLine",
    "visual_width",
    "strip_ansi",
    "wrap_line",
    "truncate_line",
    "pad_line",
    "parse_sgr_params",
    "ansi_to_runs",
    "ansi_to_line",
]
