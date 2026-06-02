"""_StdoutLineTracker — transparent stdout line tracker for save/restore.

Wraps sys.__stdout__ transparently, passing all writes through while tracking
complete lines (detected by \n) in a ring buffer. Used by _BottomBar to save
scroll-area content before completion popup expansion and restore it after collapse.

机制：
  - 所有 write/flush 原封不动穿透到真实 stdout
  - 检测 \n 将内容按行拆分存入环形缓冲区（最大 300 行）
  - 检测 \\033[{r};{c}H 绝对光标定位：若 r > scroll_end 则过滤底部栏内容
  - 检测 \\0338 / \\033[u 光标恢复 → 退出底部栏模式
"""

from __future__ import annotations

import re
from collections import deque
from typing import IO, Any

_CURSOR_POS_RE = re.compile(r'\x1b\[(\d+);(\d+)H')


class _StdoutLineTracker:
    """Transparent stdout wrapper that tracks complete lines for save/restore.

    All write/flush calls pass through to the real stdout unchanged.
    Lines are detected by \\n characters and stored in a ring buffer.
    Content written to the bottom bar area (detected via cursor positioning
    sequences) is filtered out and not tracked.
    """

    _MAX_LINES = 300

    def __init__(self, real_stdout: IO[str]):
        self._real_stdout = real_stdout
        self._ring: deque[str] = deque(maxlen=self._MAX_LINES)
        self._partial_line: str = ""
        self._scroll_end: int = 0
        self._in_bottom_bar: bool = False
        self._saved_rows: list[str] | None = None

    # ── File object protocol ──

    @property
    def encoding(self) -> str:
        return getattr(self._real_stdout, 'encoding', 'utf-8')

    @property
    def errors(self) -> str:
        return getattr(self._real_stdout, 'errors', 'strict')

    @property
    def buffer(self) -> Any:
        return self._real_stdout.buffer

    def fileno(self) -> int:
        return self._real_stdout.fileno()

    def isatty(self) -> bool:
        return self._real_stdout.isatty()

    def writable(self) -> bool:
        return True

    # ── Core write/flush ──

    def write(self, data: str) -> int:
        self._real_stdout.write(data)
        self._track(data)
        return len(data)

    def flush(self) -> None:
        self._real_stdout.flush()

    # ── Scroll end management ──

    def set_scroll_end(self, scroll_end: int) -> None:
        """Update the scroll region end row.

        Called by _BottomBar whenever DECSTBM is updated.
        scroll_end < 1 disables tracking.
        """
        self._scroll_end = scroll_end

    # ── Line tracking ──

    def _track(self, data: str) -> None:
        """Process data for line tracking.

        Strips cursor positioning sequences (\\033[{r};{c}H) from tracked
        content and uses them to detect bottom bar mode. Handles cursor
        restore sequences (\\0338, \\033[u) to exit bottom bar mode.
        Only tracks complete lines (ending with \\n) when scroll_end >= 1.
        """
        if self._scroll_end < 1:
            return

        # Handle cursor restore: \0338 or \033[u
        if '\x1b8' in data or '\x1b[u' in data:
            self._in_bottom_bar = False
            self._partial_line = ""

        # Process cursor positioning sequences: \033[{r};{c}H
        prev_end = 0
        for m in _CURSOR_POS_RE.finditer(data):
            # Text before this cursor position sequence
            if m.start() > prev_end:
                self._add_text(data[prev_end:m.start()])

            row = int(m.group(1))
            was_in_bottom_bar = self._in_bottom_bar
            self._in_bottom_bar = (row > self._scroll_end)
            if self._in_bottom_bar != was_in_bottom_bar:
                self._partial_line = ""

            prev_end = m.end()

        # Remaining text after last cursor position sequence
        if prev_end < len(data):
            self._add_text(data[prev_end:])

    def _add_text(self, text: str) -> None:
        """Accumulate text and extract complete lines (split on \\n)."""
        self._partial_line += text
        if '\n' in self._partial_line:
            *complete_lines, self._partial_line = self._partial_line.split('\n')
            if not self._in_bottom_bar:
                for line in complete_lines:
                    self._ring.append(line)

    # ── Save/restore API ──

    def save_rows_to_restore(self, n: int) -> None:
        """Save the last n complete lines from the ring buffer.

        Called before SU scroll in show_completions() to snapshot the
        content that will be scrolled out of view.

        Args:
            n: Number of rows to save.
        """
        if n <= 0:
            return
        ring_list = list(self._ring)
        if not ring_list:
            return
        self._saved_rows = list(ring_list[-n:]) if len(ring_list) >= n else list(ring_list)

    def get_saved_rows(self) -> list[str] | None:
        """Get the saved rows for restoration, or None if nothing saved."""
        return self._saved_rows

    def clear_saved(self) -> None:
        """Clear saved rows after they have been restored to the terminal."""
        self._saved_rows = None
