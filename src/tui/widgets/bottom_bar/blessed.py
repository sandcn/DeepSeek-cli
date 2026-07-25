"""ANSI/Blessed 辅助函数 — 从 _bottom_bar.py 提取。

封装常用的 ANSI 序列操作（光标定位、滚动区域、保存/恢复光标位置等）
为 Blessed API 调用，带 try/except 降级回退到原始 ANSI 序列。

职责范围：
  - 光标移动与清行（_blessed_move_clear, _blessed_cursor_goto）
  - 光标保存/恢复（_blessed_save_cursor, _blessed_restore_cursor）
  - 滚动操作（_blessed_scroll_up, _blessed_scroll_down）
  - 滚动区域设置/重置（_blessed_set_scroll_region, _blessed_reset_scroll_region）
  - 通用终端辅助（_is_cygwin_or_wsl, _save_terminal_settings,
    _restore_terminal_settings, _drain_stdin_residual）
"""

from __future__ import annotations

import logging
import os
import select
import sys

from ...terminal.blessed import get_terminal

_logger = logging.getLogger(__name__)


__all__ = [
    "_blessed_move_clear",
    "_blessed_cursor_goto",
    "_blessed_save_cursor",
    "_blessed_restore_cursor",
    "_blessed_scroll_up",
    "_blessed_scroll_down",
    "_blessed_set_scroll_region",
    "_blessed_reset_scroll_region",
    "_is_cygwin_or_wsl",
    "_save_terminal_settings",
    "_restore_terminal_settings",
    "_drain_stdin_residual",
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

    ★ P0 修复 2026-07-11: 始终返回原始 ANSI 序列 \033[r，不使用
    Blessed Terminal.csr(0, -1) 生成。Blessed 的 csr(0, -1) 会返回
    \033[1;0r（因为 -1+1=0），这是非法的 DECSTBM 参数——底部行号 0
    小于顶部行号 1。不同终端对此非法序列的处理不一致：
    Termux/部分终端会清空屏幕或行为异常，导致「弹出补全信息清空上屏内容」的 Bug。
    \033[r（无参数）是标准 ANSI/DEC 重置序列，所有终端正确支持。

    Returns:
        ANSI 序列字符串（"\033[r"）。
    """
    return "\033[r"


# ═══════════════════════════════════════════════════════════
# 通用终端辅助函数（从 selection.py 迁移）
# ═══════════════════════════════════════════════════════════


def _is_cygwin_or_wsl() -> bool:
    """检测当前环境是否为 Cygwin 或 WSL，且标准输入为 tty。

    Cygwin 和 WSL 下 Blessed term.inkey() 可能无法正确解析 ANSI escape 序列，
    需要绕过 Blessed 路径改用原始 I/O 读取。

    WSL 检测分两步，按优先级依次尝试（任一满足即判定为 WSL）：
    1. 读取 /proc/version，若内容（不区分大小写）包含 "microsoft" 则判定为 WSL
       — 覆盖 WSL1 和 WSL2，也覆盖无 WSL_DISTRO_NAME 环境变量的场景
    2. 检查 WSL_DISTRO_NAME 环境变量是否存在（WSL2 下默认存在）
       — 作为 /proc/version 读取失败的兜底（权限不足、文件不存在等）
    两步均失败时判定为非 WSL。

    设计决策：
    - 两步检测互为备灾：/proc/version 覆盖 WSL1（无 WSL_DISTRO_NAME），
      WSL_DISTRO_NAME 覆盖 /proc/version 不可读的场景
    - 异常静默：所有异常均被捕获并记录调试日志，不会传播，
      确保函数在任何异常场景下都不会抛异常，只返回 False
    - 前置 tty 检查：先检查 os.isatty()，非 tty 环境直接返回 False，
      避免无终端时不必要的文件读取

    Returns:
        True 若环境为 Cygwin 或 WSL 且 stdin 是 tty。
    """
    if not os.isatty(sys.stdin.fileno()):
        return False
    # ── Cygwin 检测 ──
    if sys.platform == 'cygwin':
        return True
    # ── WSL 检测 ──
    try:
        with open("/proc/version", "r") as f:
            content = f.read()
        if "microsoft" in content.lower():
            return True
    except Exception as exc:
        _logger.debug("WSL 检测异常（读取 /proc/version）: %s", exc)
    try:
        if 'WSL_DISTRO_NAME' in os.environ:
            return True
    except Exception as exc:
        _logger.debug("WSL 检测异常（检查环境变量）: %s", exc)
    return False


def _save_terminal_settings(fd: int):
    """保存当前终端设置，用于后续恢复。

    Args:
        fd: 终端文件描述符。

    Returns:
        termios 设置列表，可传给 _restore_terminal_settings 恢复。
    """
    from src._compat_termios import termios
    return termios.tcgetattr(fd)


def _restore_terminal_settings(fd: int, settings) -> None:
    """恢复终端设置，异常静默。

    Args:
        fd: 终端文件描述符。
        settings: _save_terminal_settings 返回的 termios 设置。
    """
    try:
        from src._compat_termios import termios
        termios.tcsetattr(fd, termios.TCSADRAIN, settings)
    except Exception as exc:
        _logger.warning("终端设置恢复失败: %s", exc)


def _drain_stdin_residual(
    fd: int,
    timeout_per_round: float = 0.02,
    rounds: int = 3,
    max_per_round: int = 4096,
) -> None:
    """对 stdin 执行多轮排空，清除终端模式切换后延迟到达的残余字节。

    使用 select.select + os.read 进行非阻塞读取，辅以轮间 tcflush 确定性能清空。
    3 轮 × 20ms 轮询 + 每轮后 tcflush，总超时 ≤60ms。

    Args:
        fd: 终端文件描述符（如 sys.stdin.fileno()）。
        timeout_per_round: 每轮 select 超时时间（秒），默认 0.02（20ms）。
        rounds: 轮数，默认 3。
        max_per_round: 每轮最大读取字节数，默认 4096。
    """
    for _ in range(rounds):
        ready = False
        try:
            ready, _, _ = select.select([fd], [], [], timeout_per_round)
        except Exception as exc:
            _logger.debug("_drain_stdin_residual select 异常: %s", exc)
        if ready:
            try:
                os.read(fd, max_per_round)
            except Exception as exc:
                _logger.debug("_drain_stdin_residual os.read 异常: %s", exc)
        # 轮间 tcflush：清空可能已到达但被 select 遗漏的字节
        try:
            from src._compat_termios import termios as _termios
            _termios.tcflush(fd, _termios.TCIFLUSH)
        except Exception as exc:
            _logger.debug("_drain_stdin_residual tcflush 异常: %s", exc)
    # ★ 最后兜底：非阻塞检查 + tcflush，关闭最终轮 tcflush 与函数返回之间的微小间隙
    try:
        r, _, _ = select.select([fd], [], [], 0)
        if r:
            os.read(fd, max_per_round)
    except Exception as exc:
        _logger.debug("_drain_stdin_residual 最后兜底 select/read 异常: %s", exc)
    try:
        from src._compat_termios import termios as _termios
        _termios.tcflush(fd, _termios.TCIFLUSH)
    except Exception as exc:
        _logger.debug("_drain_stdin_residual 最后兜底 tcflush 异常: %s", exc)
