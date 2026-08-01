"""InkRenderer — 非全屏（随内容流动）帧差异渲染器。

核心算法（Ink 默认模型，非 DECSTBM）：
  1. 渲染组件树 → 全量 Frame（行列表，每行 = StyledRun 序列）。
  2. 行级 diff：找到 prev 与 new 首差异行 ``i``（含高度差）。
  3. 应用：从当前光标（文档底部）``cursor_up`` 定位到行 ``i``，
     重写行 ``i..new_height-1``；若 ``new_height < prev_height`` 清残留行。
  4. 静态内容在首差异行之前**永不重写**。
  5. 输入光标：渲染后按 ``place_cursor`` 放置。

PERF-4：
  - 平移快路径：``new_h > prev_h`` 且尾部内容整体下移且相同（仅新增 delta 行
    位于 ``prev_h`` 起始）时，跳过重写相同尾部，仅写新增 delta 行。
  - 单帧重写行数上限 ``_MAX_REWRITE_ROWS``：超限时降级为"仅写末尾
    ``_MAX_REWRITE_ROWS`` 行 + 清残留"（避免病态大重写冻结 UI）。

不切换备用屏幕、不用 DECSTBM——内容自然流入 scrollback。

Args:
    stream: 输出流（默认 sys.__stdout__；测试传 Mock/StringIO）。
"""

from __future__ import annotations

import logging
import sys

from src.tui._screen import cursor_up, cursor_down, clear_line, cursor_forward
from .output import Frame
from .diff import first_diff_line

_logger = logging.getLogger(__name__)

# 行尾清除（防止旧行尾部残留）
_CLEAR_EOL = "\033[K"

# PERF-4：单帧重写行数上限（防病态大重写冻结 UI）
_MAX_REWRITE_ROWS = 200


class InkRenderer:
    """非全屏帧差异渲染器。

    Args:
        stream: 输出流（默认 sys.__stdout__；测试传 Mock/StringIO）。
        line_callback: 可选回调，接收新增提交行文本（含换行），用于输出历史。
            仅在文档增长（新行）或首帧时回调，避免 live 区重写污染历史。
    """

    def __init__(self, stream=None, line_callback=None):
        self._stream = stream if stream is not None else sys.__stdout__
        self._line_callback = line_callback
        self._prev: Frame | None = None
        # 当前光标行（1-based；一帧渲染后位于文档底部下一行）
        self._cursor_row: int = 0

    # ── 渲染 ─────────────────────────────────────────

    def render(self, frame: Frame) -> None:
        """渲染新帧（最小差异写入）。"""
        if self._prev is None:
            self._write_full(frame)
            self._prev = frame
            self._stream.flush()
            return

        prev_h = self._prev.height
        new_h = frame.height
        i = first_diff_line(self._prev, frame)
        if i < 0:
            # 帧完全一致：仅刷新光标位置（调用方自行 place_cursor）
            return

        delta = new_h - prev_h

        # ★ PERF-4 平移快路径：仅新增 delta 行导致尾部整体下移且内容相同
        #   （delta 新行位于 prev_h 起始，尾部相同内容跳过重写）。
        #   安全条件：i >= prev_h（首差异恰在文档末尾，无需要平移的尾部）——
        #   中间插入场景（i < prev_h）尾部必须重写（终端无 insert-line 语义），
        #   走下方常规路径。
        if new_h > prev_h and i >= prev_h and self._is_tail_shifted(self._prev, frame, i, delta):
            # 定位到 prev_h+1（从 prev 文档底部开始写 delta 新行）
            n_move = self._cursor_row - (prev_h + 1)
            if n_move > 0:
                self._stream.write(cursor_up(n_move))
            elif n_move < 0:
                self._stream.write(cursor_down(-n_move))
            for line_idx in range(prev_h, new_h):
                self._stream.write("\r")
                self._stream.write(frame.render_line(line_idx))
                self._stream.write(_CLEAR_EOL)
                self._stream.write("\n")
            self._emit_new_lines(frame, prev_h, new_h)
            self._cursor_row = new_h + 1
            self._prev = frame
            self._stream.flush()
            return

        # ★ PERF-4 单帧重写行数上限：超限时降级为仅写末尾 _MAX_REWRITE_ROWS 行
        #   + 清残留（避免病态大重写冻结 UI）。
        rewrite_count = new_h - i
        if rewrite_count > _MAX_REWRITE_ROWS:
            _logger.warning(
                "单帧重写行数 %d 超上限 %d，降级为仅写末尾 %d 行",
                rewrite_count, _MAX_REWRITE_ROWS, _MAX_REWRITE_ROWS,
            )
            start_idx = max(0, new_h - _MAX_REWRITE_ROWS)
            target_row = start_idx + 1
            n_move = self._cursor_row - target_row
            if n_move > 0:
                self._stream.write(cursor_up(n_move))
            elif n_move < 0:
                self._stream.write(cursor_down(-n_move))
            for line_idx in range(start_idx, new_h):
                self._stream.write("\r")
                self._stream.write(frame.render_line(line_idx))
                self._stream.write(_CLEAR_EOL)
                self._stream.write("\n")
            # 新增内容行（文档增长）回调输出历史
            if new_h > prev_h:
                self._emit_new_lines(frame, prev_h, new_h)
            # 文档收缩：清除残留行
            if new_h < prev_h:
                for _ in range(prev_h - new_h):
                    self._stream.write(clear_line())
                    self._stream.write(cursor_down(1))
            self._cursor_row = max(new_h, prev_h) + 1
            self._prev = frame
            self._stream.flush()
            return

        # ★ 定位到行 i：从当前光标位置（_cursor_row，可能已被 place_cursor
        #   移到输入行）移动到目标行 i+1。不能假设光标恒在文档底部 prev_h+1——
        #   否则每帧重写会上移一行（输入光标行 ≠ 底部+1）。
        n_move = self._cursor_row - (i + 1)
        if n_move > 0:
            self._stream.write(cursor_up(n_move))
        elif n_move < 0:
            self._stream.write(cursor_down(-n_move))

        # ★ 重写行 i..new_h-1：raw 终端模式下 \n 不归位列 1，每行须前缀 \r。
        for line_idx in range(i, new_h):
            self._stream.write("\r")
            self._stream.write(frame.render_line(line_idx))
            self._stream.write(_CLEAR_EOL)
            self._stream.write("\n")
        # 新增内容行（文档增长）回调输出历史
        if new_h > prev_h:
            self._emit_new_lines(frame, prev_h, new_h)

        # new_h < prev_h：清除残留行（rows new_h+1 .. prev_h）
        if new_h < prev_h:
            for _ in range(prev_h - new_h):
                self._stream.write(clear_line())
                self._stream.write(cursor_down(1))

        # 清除残留行使光标落在 prev_h+1；否则在 new_h+1
        self._cursor_row = max(new_h, prev_h) + 1
        self._prev = frame
        self._stream.flush()

    def _is_tail_shifted(self, prev: Frame, frame: Frame, i: int, delta: int) -> bool:
        """检测尾部内容是否只是整体下移（仅新增 delta 行）。

        规则：``prev.lines[i:prev_h]`` 与 ``frame.lines[i+delta:new_h]``
        逐行相同（身份短路 + runs 值相等）。

        Args:
            prev: 上一帧。
            frame: 新帧。
            i: 首差异行。
            delta: 高度差（new_h - prev_h，>0 时检测有意义）。

        Returns:
            True — 尾部内容整体下移且相同（可跳过重写）。
        """
        p = prev.lines
        n = frame.lines
        start = i + delta
        if start > len(n):
            return False
        a = p[i:prev.height]
        b = n[start:len(n)]
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if x is not y and x.runs != y.runs:
                return False
        return True

    def _write_full(self, frame: Frame) -> None:
        """首帧/重置后：全量写入文档。

        raw 终端模式下 \n 不归位列 1，每行前缀 \r（与 OutputAdapter 的
        CRLF 语义一致）。
        """
        if not frame.lines:
            return
        for line in frame.lines:
            self._stream.write("\r")
            self._stream.write(line.render())
            self._stream.write("\n")
        self._emit_new_lines(frame, 0, frame.height)
        self._cursor_row = frame.height + 1

    def _emit_new_lines(self, frame: Frame, start: int, end: int) -> None:
        """回调新增行（输出历史跟踪）。"""
        if self._line_callback is None:
            return
        try:
            for idx in range(start, end):
                self._line_callback(frame.render_line(idx) + "\n")
        except Exception:
            pass

    # ── 光标 ─────────────────────────────────────────

    def place_cursor(self, row: int, col: int) -> None:
        """将光标放置到文档坐标 (row, col)（1-based）。

        从当前光标位置（_cursor_row）相对移动，避免绝对坐标在滚动终端中
        失效。raw 模式下先 \r 归位列 1 再前进到 col。
        """
        current_row = self._cursor_row
        n_up = current_row - row
        if n_up > 0:
            self._stream.write(cursor_up(n_up))
        elif n_up < 0:
            self._stream.write(cursor_down(-n_up))
        self._stream.write("\r")
        if col > 1:
            self._stream.write(cursor_forward(col - 1))
        self._stream.flush()
        self._cursor_row = row

    def set_line_callback(self, callback) -> None:
        """设置新增行回调（输出历史跟踪）。"""
        self._line_callback = callback

    # ── 生命周期 ─────────────────────────────────────

    def suspend(self) -> None:
        """暂停：重置渲染状态（live 区已作为普通行提交到 scrollback）。

        非全屏模型下文档行都是真实 scrollback 行，无需额外提交；
        仅清空 prev 状态，使恢复后重新全量渲染。
        """
        self._prev = None
        self._cursor_row = 0
        try:
            self._stream.flush()
        except Exception:
            pass

    def reset(self) -> None:
        """重置渲染状态（resume 后重新渲染）。"""
        self._prev = None
        self._cursor_row = 0

    def flush(self) -> None:
        """刷出底层输出。"""
        try:
            self._stream.flush()
        except Exception:
            pass

    # ── 测试辅助 ─────────────────────────────────────

    @property
    def prev_frame(self) -> Frame | None:
        return self._prev

    @property
    def cursor_row(self) -> int:
        return self._cursor_row


__all__ = ["InkRenderer"]
