"""输出模型 — StyledRun / Line / Frame（用 core.style）。

输出模型是帧渲染的载体：
  - StyledRun：一段带样式的文本（text + Style）
  - Line：一行 = StyledRun 序列（render() 合并为 ANSI 字符串）
  - Frame：一帧 = Line 序列（整帧文档，供 InkRenderer 行级 diff）
  - FrameBuilder：流式构建 Frame 的辅助器（按宽换行/追加）

零 Rich 依赖：样式一律用 ``src.tui.core.style.Style``，
宽度一律用 ``_screen.wcswidth_simple``（唯一宽度依据）。
"""

from __future__ import annotations

from src._compat import dataclass
from dataclasses import field
from typing import Iterable

from src.tui.core.style import Style
from src.tui._screen import wcswidth_simple


# ═══════════════════════════════════════════════════════════
# StyledRun — 带样式的文本片段
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StyledRun:
    """一段带样式的文本。

    Attributes:
        text: 文本内容。
        style: 样式（None 表示无样式）。
    """

    text: str
    style: Style | None = None

    def render(self) -> str:
        """渲染为 ANSI 字符串（无样式时原样返回）。"""
        if self.style:
            return self.style.apply(self.text)
        return self.text

    @property
    def width(self) -> int:
        """文本显示宽度（wcswidth_simple）。"""
        return wcswidth_simple(self.text)


# ═══════════════════════════════════════════════════════════
# Line — 一行 StyledRun 序列
# ═══════════════════════════════════════════════════════════


class Line:
    """一行渲染输出（StyledRun 序列）。

    - ``render()`` 合并所有 run 为 ANSI 字符串。
    - ``width`` 为所有 run 的显示宽度总和。
    - ``append(text, style)`` 追加一段；``append_run(run)`` 追加 StyledRun。
    """

    __slots__ = ("runs",)

    def __init__(self, runs: Iterable[StyledRun] | None = None) -> None:
        self.runs: list[StyledRun] = list(runs) if runs else []

    @classmethod
    def of(cls, text: str, style: Style | None = None) -> "Line":
        """从纯文本创建单 run 行。"""
        return cls([StyledRun(text, style)])

    def append(self, text: str, style: Style | None = None) -> None:
        """追加一段文本（自动合并相邻同 style 的 run）。"""
        if not text:
            return
        if self.runs and self.runs[-1].style == style:
            last = self.runs[-1]
            self.runs[-1] = StyledRun(last.text + text, style)
            return
        self.runs.append(StyledRun(text, style))

    def append_run(self, run: StyledRun) -> None:
        """追加 StyledRun。"""
        if not run or not run.text:
            return
        self.append(run.text, run.style)

    def render(self) -> str:
        """合并为 ANSI 字符串。"""
        return "".join(r.render() for r in self.runs)

    @property
    def width(self) -> int:
        """显示宽度总和。"""
        total = 0
        for r in self.runs:
            total += r.width
        return total

    @property
    def plain(self) -> str:
        """纯文本（去样式）。"""
        return "".join(r.text for r in self.runs)

    def clone(self) -> "Line":
        """深拷贝行（runs 为不可变 StyledRun，浅拷贝列表即可）。"""
        return Line(self.runs)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Line({self.plain!r})"


# ═══════════════════════════════════════════════════════════
# Frame — 一帧（行列表）
# ═══════════════════════════════════════════════════════════


class Frame:
    """一帧渲染输出（Line 列表）。

    整个 UI 是一个输出文档：静态聊天历史 + 尾部 live 区（状态栏 + 输入）。
    每帧 = 完整文档的 Line 列表，供 InkRenderer 行级 diff。
    """

    __slots__ = ("lines",)

    def __init__(self, lines: Iterable[Line] | None = None) -> None:
        self.lines: list[Line] = list(lines) if lines else []

    @property
    def height(self) -> int:
        """文档总行数。"""
        return len(self.lines)

    def render_line(self, index: int) -> str:
        """渲染第 index 行为 ANSI 字符串。"""
        return self.lines[index].render()

    def to_ansi(self) -> str:
        """渲染整帧为 ANSI（行间以 \\n 连接，末尾换行）。"""
        if not self.lines:
            return ""
        return "\n".join(line.render() for line in self.lines) + "\n"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Frame({len(self.lines)} lines)"


# ═══════════════════════════════════════════════════════════
# FrameBuilder — 流式构建 Frame（按宽换行）
# ═══════════════════════════════════════════════════════════


class FrameBuilder:
    """流式构建 Frame 的辅助器。

    - ``append(text, style)``：追加文本到当前行，超宽自动换行。
    - ``newline()``：结束当前行。
    - ``build()``：返回 Frame。

    宽度依据为 ``wcswidth_simple``；换行宽度上限由构造参数给定。
    若 width<=0 表示不换行（保持原样，每行一逻辑行）。
    """

    __slots__ = ("_width", "_lines", "_current", "_current_width")

    def __init__(self, width: int = 0) -> None:
        self._width = width
        self._lines: list[Line] = []
        self._current: Line = Line()
        self._current_width = 0

    @property
    def width(self) -> int:
        return self._width

    def append(self, text: str, style: Style | None = None) -> None:
        """追加文本到当前行，超宽自动换行。"""
        if not text:
            return
        if self._width <= 0:
            self._current.append(text, style)
            return
        for ch in text:
            cw = wcswidth_simple(ch)
            if self._current_width + cw > self._width and self._current.runs:
                self._newline()
            self._current.append(ch, style)
            self._current_width += cw

    def append_run(self, run: StyledRun) -> None:
        """追加 StyledRun（按宽换行）。"""
        self.append(run.text, run.style)

    def append_line(self, line: Line) -> None:
        """追加一整行（强制换行后追加）。"""
        self._newline()
        self._lines.append(line)
        self._current = Line()
        self._current_width = 0

    def newline(self) -> None:
        """结束当前行（空行也结束）。"""
        self._newline()

    def _newline(self) -> None:
        self._lines.append(self._current)
        self._current = Line()
        self._current_width = 0

    def build(self) -> Frame:
        """返回已构建的 Frame（含未结束的当前行）。"""
        if self._current.runs:
            self._lines.append(self._current)
            self._current = Line()
            self._current_width = 0
        return Frame(self._lines)


__all__ = [
    "StyledRun",
    "Line",
    "Frame",
    "FrameBuilder",
]
