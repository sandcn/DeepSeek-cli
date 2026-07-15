"""ANSI/Blessed 辅助函数 — inline 模式精简版。

【inline 模式 · 2026-07-16 重构】

仅保留 inline 模式所需的基本 ANSI 序列：
  - 光标移动与清行（_blessed_move_clear, _blessed_cursor_goto）

移除的全屏模式函数：
  - _blessed_save_cursor / _blessed_restore_cursor — SCOSC/DECRC
  - _blessed_scroll_up / _blessed_scroll_down — SU/SD
  - _blessed_set_scroll_region / _blessed_reset_scroll_region — DECSTBM
"""

from __future__ import annotations

from ...terminal.blessed import get_terminal


__all__ = [
    "_blessed_move_clear",
    "_blessed_cursor_goto",
]


def _blessed_move_clear(row: int) -> str:
    """生成移到指定行并清行的 ANSI 序列。

    通过 Blessed Terminal.move_xy + clear_eol 生成，
    Blessed 不可用时或返回空时回退到原始 ANSI。

    Args:
        row: 1-based 行号。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        result = term.move_xy(0, row - 1) + term.clear_eol()
        return result if result else f"\033[{row};1H\033[K"
    except Exception:
        return f"\033[{row};1H\033[K"


def _blessed_cursor_goto(row: int, col: int) -> str:
    """生成移到指定行列的 ANSI 序列（CUP）。

    通过 Blessed Terminal.move_xy 生成。
    Blessed 使用 0-based 坐标，不可用时或返回空时回退到原始 ANSI。

    Args:
        row: 1-based 行号。
        col: 1-based 列号。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        result = term.move_xy(col - 1, row - 1)
        return result if result else f"\033[{row};{col}H"
    except Exception:
        return f"\033[{row};{col}H"
