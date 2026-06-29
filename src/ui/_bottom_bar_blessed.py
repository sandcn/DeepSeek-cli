"""ANSI/Blessed 辅助函数 — 从 _bottom_bar.py 提取。

封装常用的 ANSI 序列操作（光标定位、滚动区域、保存/恢复光标位置等）
为 Blessed API 调用，带 try/except 降级回退到原始 ANSI 序列。

职责范围：
  - 光标移动与清行（_blessed_move_clear, _blessed_cursor_goto）
  - 光标保存/恢复（_blessed_save_cursor, _blessed_restore_cursor）
  - 滚动操作（_blessed_scroll_up, _blessed_scroll_down）
  - 滚动区域设置/重置（_blessed_set_scroll_region, _blessed_reset_scroll_region）
"""

from __future__ import annotations

from ._blessed import get_terminal


__all__ = [
    "_blessed_move_clear",
    "_blessed_cursor_goto",
    "_blessed_save_cursor",
    "_blessed_restore_cursor",
    "_blessed_scroll_up",
    "_blessed_scroll_down",
    "_blessed_set_scroll_region",
    "_blessed_reset_scroll_region",
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
    """生成移到指定行列的 ANSI 序列。

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


def _blessed_save_cursor() -> str:
    """保存光标位置（DECSC/SCOSC）。

    通过 Blessed Terminal.sc 生成 DECSC 序列，
    Blessed 不可用时回退到原始 ANSI。

    Returns:
        ANSI 序列字符串。
    """
    try:
        sc = get_terminal().sc
        return sc if isinstance(sc, str) and sc else "\0337"
    except Exception:
        return "\0337"


def _blessed_restore_cursor() -> str:
    """恢复光标位置（DECRC/SCRC）。

    通过 Blessed Terminal.rc 生成 DECRC 序列，
    Blessed 不可用时回退到原始 ANSI。

    Returns:
        ANSI 序列字符串。
    """
    try:
        rc = get_terminal().rc
        return rc if isinstance(rc, str) and rc else "\0338"
    except Exception:
        return "\0338"


def _blessed_scroll_up(n: int) -> str:
    """向上滚动 n 行（SU）。

    通过 Blessed Terminal.indn 生成 SU 序列。
    n <= 0 时返回空字符串。
    Blessed 不可用时回退到原始 ANSI。

    Args:
        n: 滚动行数。

    Returns:
        ANSI 序列字符串。
    """
    if n <= 0:
        return ""
    try:
        seq = get_terminal().indn(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}S"
    except Exception:
        return f"\033[{n}S"


def _blessed_scroll_down(n: int) -> str:
    """向下滚动 n 行（SD/RI）。

    通过 Blessed Terminal.rin 生成 SD 序列。
    n <= 0 时返回空字符串。
    Blessed 不可用时回退到原始 ANSI。

    Args:
        n: 滚动行数。

    Returns:
        ANSI 序列字符串。
    """
    if n <= 0:
        return ""
    try:
        seq = get_terminal().rin(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}T"
    except Exception:
        return f"\033[{n}T"


def _blessed_set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域（DECSTBM）。top/bottom 为 1-based。

    通过 Blessed Terminal.csr 生成 DECSTBM 序列。
    Blessed 使用 0-based 坐标，内部自动转换。
    Blessed 不可用时回退到原始 ANSI。

    Args:
        top: 滚动区域起始行（1-based）。
        bottom: 滚动区域结束行（1-based）。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        seq = term.csr(top - 1, bottom - 1)
        return seq if isinstance(seq, str) and seq else f"\033[{top};{bottom}r"
    except Exception:
        return f"\033[{top};{bottom}r"


def _blessed_reset_scroll_region() -> str:
    """重置滚动区域为全屏（DECSTBM 重置）。

    ★ P0 修复 2026-06-11: 始终返回原始 ANSI 序列 \033[r，不使用
    Blessed Terminal.csr(0, -1) 生成。Blessed 的 csr(0, -1) 会返回
    \033[1;0r（因为 -1+1=0），这是非法的 DECSTBM 参数——底部行号 0
    小于顶部行号 1。不同终端对此非法序列的处理不一致：
    Termux/部分终端会清空屏幕或行为异常，导致「弹出补全信息清空上屏内容」的 Bug。
    \033[r（无参数）是标准 ANSI/DEC 重置序列，所有终端正确支持。

    Returns:
        ANSI 序列字符串（"\033[r"）。
    """
    return "\033[r"
