"""_StdoutLineTracker — transparent stdout line tracker.

[DEPRECATED · inline 模式 · 2026-07-16]

inline 模式下不再安装此 tracker——sys.__stdout__ 直接输出，无需包装追踪。
保留此模块供向后兼容和测试使用。set_scroll_end() 在 inline 模式下为 NOP。

Wraps sys.__stdout__ transparently, passing all writes through while tracking
complete lines (detected by \n) in a ring buffer.

机制：
  - 所有 write/flush 原封不动穿透到真实 stdout
  - 检测 \n 将内容按行拆分存入环形缓冲区（最大 300 行）
  - 使用统一正则按数据流顺序处理光标控制序列：
    \\033[{r};{c}H 绝对光标定位（r > scroll_end → 过滤底部栏内容）
    \\0338 / \\033[u 光标恢复 → 退出底部栏模式
"""

from __future__ import annotations

import re
from collections import deque
from typing import IO, Any

# Unified regex matching cursor positioning (CUP) and cursor restore (DECRC/SCRC)
# sequences. Processed in data-stream order so that a restore between two
# cursor-positioning sequences takes effect at its actual position.
#
#   \033[{r};{c}H  — CUP (cursor absolute positioning)
#   \0338          — DECRC (restore cursor)
#   \033[u         — SCRC (restore cursor, ANSI.SYS variant)
_CONTROL_SEQ_RE = re.compile(
    r'\x1b\[(?P<row>\d+);(?P<col>\d+)H'  # CUP
    r'|\x1b8'                              # DECRC
    r'|\x1b\[u'                            # SCRC
)


class _StdoutLineTracker:
    """Transparent stdout wrapper that tracks complete lines.

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

        Uses a unified regex to match cursor positioning sequences
        (\\033[{r};{c}H) and cursor restore sequences (\\0338, \\033[u)
        in data-stream order.  Cursor positioning to a row > scroll_end
        enters bottom bar mode (content not tracked); cursor restore exits
        bottom bar mode.  All matched control sequences are stripped from
        tracked text.  Only tracks complete lines (ending with \\n) when
        scroll_end >= 1.
        """
        if self._scroll_end < 1:
            return

        prev_end = 0
        for m in _CONTROL_SEQ_RE.finditer(data):
            # Text before this control sequence
            if m.start() > prev_end:
                self._add_text(data[prev_end:m.start()])

            if m.group('row') is not None:
                # Cursor positioning: \033[{r};{c}H
                row = int(m.group('row'))
                was_in_bottom_bar = self._in_bottom_bar
                self._in_bottom_bar = (row > self._scroll_end)
                if self._in_bottom_bar != was_in_bottom_bar:
                    self._partial_line = ""
            else:
                # Cursor restore: \0338 or \033[u → exit bottom bar mode
                if self._in_bottom_bar:
                    self._in_bottom_bar = False
                    self._partial_line = ""

            prev_end = m.end()

        # Remaining text after last control sequence
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
