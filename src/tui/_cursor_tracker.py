"""CursorTracker — 全局光标坐标追踪器。

追踪终端光标在屏幕上的实际位置（1-based row/col）。
所有通过 chat_ui 渲染的内容都应通过此追踪器管理坐标。

核心契约：
  - 渲染前：调用 move_to() 明确目标位置
  - 渲染后：调用 record_newlines() 更新位置
  - 任何时候可通过 .pos 获取当前光标位置

使用方式：
    tracker = CursorTracker()
    tracker.move_to(10, 5)         # 移动 + 记录
    tracker.record_newlines(3)     # 记录了 3 行输出
    pos = tracker.save()           # 保存检查点
    tracker.restore(pos)           # 恢复检查点
    print(tracker.pos)             # (row=13, col=1)

设计决策：
  - 单线程使用（仅在 render 线程中），无需锁
  - 坐标 1-based，与人类习惯和终端 ANSI 序列一致
  - 轻量无依赖，仅依赖标准库
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import IO, Optional


@dataclass
class CursorPosition:
    """光标在终端上的位置（1-based 行列坐标）。

    Attributes:
        row: 终端行号（1-based，从屏幕顶部第 1 行开始）。
        col: 终端列号（1-based，从屏幕左侧第 1 列开始）。
    """
    row: int = 1
    col: int = 1

    def __str__(self) -> str:
        return f"(row={self.row}, col={self.col})"


class CursorTracker:
    """全局光标位置追踪器。

    追踪终端光标在屏幕上的实际位置，提供移动、记录、恢复能力。
    所有通过 chat_ui 渲染的内容应通过此追踪器管理坐标。
    """

    def __init__(
        self,
        initial_row: int = 1,
        initial_col: int = 1,
        _file: Optional[IO[str]] = None,
    ):
        """初始化追踪器。

        Args:
            initial_row: 初始行号（1-based），默认 1。
            initial_col: 初始列号（1-based），默认 1。
            _file: 输出文件对象，默认 sys.__stdout__（仅测试用）。
        """
        self._row = initial_row
        self._col = initial_col
        self._file = _file or sys.__stdout__

    # ── 公开属性 ──────────────────────────────────────

    @property
    def pos(self) -> CursorPosition:
        """获取当前光标位置的快照。

        Returns:
            当前光标位置（1-based row/col）的深拷贝。
        """
        return CursorPosition(self._row, self._col)

    # ── 坐标移动（写终端 + 内部更新） ─────────────────

    def move_to(self, row: int, col: int) -> None:
        """移动光标到指定位置并记录坐标（写 ANSI 序列）。

        使用 CUP (Cursor Position) ANSI 序列 \\033[row;colH。
        Args:
            row: 目标行号（1-based）。
            col: 目标列号（1-based）。
        """
        self._file.write(f"\033[{row};{col}H")
        self._row = row
        self._col = col

    def move_xy(self, col: int, row: int) -> None:
        """Blessed 风格的光标移动 — 0-based 输入，自动转换为 1-based。

        Args:
            col: 目标列号（0-based，x 坐标）。
            row: 目标行号（0-based，y 坐标）。
        """
        self.move_to(row + 1, col + 1)

    # ── 内部状态更新（不写终端） ──────────────────────

    def set(self, row: int, col: int) -> None:
        """直接设置当前位置（不写终端，仅更新内部状态）。

        用于在已通过其他方式定位光标后，同步追踪器的状态。

        自动 clamp：负值或零值会被提升为 1（终端坐标最小有效值），
        防止负值坐标被传给 cursor_goto ANSI 序列导致终端异常。

        Args:
            row: 行号（1-based），负值或零值自动 clamp 到 1。
            col: 列号（1-based），负值或零值自动 clamp 到 1。
        """
        self._row = max(1, row)
        self._col = max(1, col)

    def record_newlines(self, n: int) -> None:
        """记录输出了 n 行文本后的光标位置变化。

        行号增加 n，列号重置为 1（终端的自然行为）。

        Args:
            n: 输出的行数（须 ≥ 0，不检查）。
        """
        self._row += n
        self._col = 1

    def record_move_down(self, n: int = 1) -> None:
        """记录光标下移 n 行（列不变）。

        适用于光标在行内移动不改变列号的场景。

        Args:
            n: 下移行数，默认 1。
        """
        self._row += n

    # ── 检查点模式 ────────────────────────────────────

    def save(self) -> CursorPosition:
        """保存当前位置检查点。

        Returns:
            当前光标位置的快照，可后续传递给 restore() 恢复。
        """
        return CursorPosition(self._row, self._col)

    def restore(self, pos: CursorPosition) -> None:
        """恢复到之前保存的位置（仅更新内部状态，不写终端）。

        Args:
            pos: 由 save() 返回的位置快照。
        """
        self._row = pos.row
        self._col = pos.col

    # ── 辅助 ──────────────────────────────────────────

    def __repr__(self) -> str:
        return f"CursorTracker(pos=({self._row}, {self._col}))"
