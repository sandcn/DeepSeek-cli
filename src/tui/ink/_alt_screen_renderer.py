"""AlternateScreenRenderer — 备用屏（alternate screen）渲染器（B3 第一步）。

与 ``InkRenderer`` 的区别：
  - ``InkRenderer`` 是非全屏流动模型：文档写入终端 scrollback，靠
    ``_buf_h/_start_row/_effective_offset`` 跟踪物理光标与漂移；
  - 本渲染器工作在备用屏：固定视口（终端高度），每帧**全量重绘**可见区，
    不依赖 scrollback、不做行级增量、不产生输出历史回调。

通过 ``render(alternateScreen=True)`` 或 ``InkSession(alternate_screen=True)``
启用；默认仍使用 ``InkRenderer``。当前实现是 B3 的可运行基线（视口裁剪 +
全量帧 + 光标定位），后续可在此之上加滚动偏移/增量 diff。
"""

from __future__ import annotations

import sys
from typing import Callable

_HOME = "\x1b[H"
_CLEAR_EOL = "\x1b[2K"
_CLEAR_BELOW = "\x1b[J"
_CLEAR_ALL = "\x1b[2J\x1b[H"
_CURSOR_SHOW = "\x1b[?25h"
_CURSOR_HIDE = "\x1b[?25l"


class AlternateScreenRenderer:
    """备用屏渲染器（固定视口 + 每帧全量重绘）。

    公开方法面与 ``InkRenderer`` 对齐，供 ``InkSession`` 无差别调用。
    """

    def __init__(self, stream=None, line_callback=None, height=None):
        self._stream = stream if stream is not None else sys.__stdout__
        self._width = 0
        self._height = int(height or 0)
        self._cursor_row = 1
        self._prev = None
        self._line_callback = line_callback
        self._cursor_visible = True
        self._cursor_row_provider = None
        self._content_count = 0
        #: 视口向上滚动的行数（0=贴底；>0 显示更早内容）
        self._offset = 0
        #: 最近一帧可见窗口的起始文档索引（0-based）
        self._window_start = 0
        #: 行级增量：上次可见行的渲染文本 + 窗口/高度基准
        self._prev_texts = None
        self._prev_start = -1
        self._prev_height = -1

    # ── 属性 ─────────────────────────────────────────

    @property
    def stream(self):
        return self._stream

    def prev_frame(self):
        return self._prev

    def cursor_row(self) -> int:
        return self._cursor_row

    # ── 尺寸 ─────────────────────────────────────────

    def set_width(self, width: int) -> None:
        self._width = int(width) if width else 0

    def set_height(self, height: int) -> None:
        self._height = int(height) if height else 0
        if self._height > 0:
            self._cursor_row = max(1, min(self._cursor_row, self._height))

    def set_cursor_row_provider(self, provider) -> None:
        """兼容 InkRenderer API（备用屏不做启动行锚定）。"""
        self._cursor_row_provider = provider

    # ── 渲染 ─────────────────────────────────────────

    def _window(self, lines: list) -> tuple[int, list]:
        """返回（窗口起始索引, 可见行）。"""
        if self._height <= 0 or len(lines) <= self._height:
            return 0, lines
        max_offset = max(0, len(lines) - self._height)
        self._offset = max(0, min(self._offset, max_offset))
        start = len(lines) - self._height - self._offset
        return start, lines[start:start + self._height]

    def scroll_by(self, delta: int) -> None:
        """滚动视口（正数向上看更早内容，负数向下；0=不滚动）。"""
        if self._prev is None:
            return
        total = len(self._prev.lines)
        max_offset = max(0, total - self._height) if self._height > 0 else 0
        self._offset = max(0, min(self._offset + int(delta), max_offset))

    def scroll_to_bottom(self) -> None:
        """视口回到最新内容（贴底）。"""
        self._offset = 0

    def render(self, frame) -> None:
        all_lines = list(frame.lines)
        start, lines = self._window(all_lines)
        texts = [ln.render() for ln in lines]
        full = (
            self._prev_texts is None
            or start != self._prev_start
            or self._height != self._prev_height
            or len(texts) != len(self._prev_texts)
        )
        buf: list[str] = []
        if full:
            buf.append(_HOME)
            for i, text in enumerate(texts):
                buf.append(_CLEAR_EOL)
                buf.append(text)
                if i < len(texts) - 1:
                    buf.append("\r\n")
            buf.append(_CLEAR_BELOW)
        else:
            # 行级增量：只重写内容变化的行（绝对定位，不触发滚动）
            for i, (old, new) in enumerate(zip(self._prev_texts, texts)):
                if old != new:
                    buf.append(f"\x1b[{i + 1};1H")
                    buf.append(_CLEAR_EOL)
                    buf.append(new)
        if buf:
            self._stream.write("".join(buf))
            self._stream.flush()
        self._prev = frame
        self._prev_texts = texts
        self._prev_start = start
        self._prev_height = self._height
        self._window_start = start
        self._cursor_row = max(1, len(texts))

    # ── 光标 ─────────────────────────────────────────

    def place_cursor(self, row: int, col: int) -> None:
        """定位光标（row 为文档 1-based 行，映射到当前可见窗口）。"""
        r = int(row or 1) - int(self._window_start)
        c = int(col or 1)
        if r < 1:
            # 光标在窗口上方：钳到首行（备用屏无 scrollback）
            r = 1
        if self._height > 0:
            r = max(1, min(r, self._height))
        if self._width > 0:
            c = max(1, min(c, self._width))
        self._stream.write(f"\x1b[{r};{c}H")
        self._stream.flush()
        self._cursor_row = r

    def set_cursor_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._cursor_visible:
            return
        self._cursor_visible = visible
        self._stream.write(_CURSOR_SHOW if visible else _CURSOR_HIDE)
        self._stream.flush()

    def goto_bottom(self, rows: int) -> None:
        r = max(1, int(rows or 1))
        self._stream.write(f"\x1b[{r};1H")
        self._stream.flush()
        self._cursor_row = r

    # ── 生命周期 ─────────────────────────────────────

    def full_clear(self) -> None:
        self._stream.write(_CLEAR_ALL)
        self._stream.flush()
        self._cursor_row = 1

    def reset(self, full: bool = False) -> None:
        self._prev = None
        self._prev_texts = None
        self._prev_start = -1
        self._prev_height = -1
        self._cursor_row = 1
        self._offset = 0
        self._window_start = 0
        if full:
            self.full_clear()

    def suspend(self) -> None:
        self.reset()

    def resume(self) -> None:
        self.full_clear()

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def emit_exit_note(self, text: str) -> None:
        """退出提示（备用屏退出后由终端模式管理器还原，提示写到当前屏）。"""
        if not text:
            return
        self._stream.write(f"\r\n{text}\r\n")
        self._stream.flush()

    def set_line_callback(self, callback) -> None:
        """兼容 API：备用屏不产生输出历史行回调。"""
        self._line_callback = callback

    def set_content_line_count(self, count: int, start=None, resync: bool = False) -> None:
        """兼容 API：备用屏无 scrollback 输出历史。"""
        self._content_count = int(count or 0)

    def reset_content_lines(self) -> None:
        self._content_count = 0


__all__ = ["AlternateScreenRenderer"]
