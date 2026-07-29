"""Async 全局中断信号 — 基于 threading.Event

与同步版 interrupt.py 接口对等，但使用 threading.Event 替代 asyncio.Event，
避免事件循环绑定问题。
"""

from __future__ import annotations

import asyncio
import logging
import select
import sys
import threading

_logger = logging.getLogger(__name__)

__all__ = [
    "is_interrupted_async",
    "request_interrupt_async",
    "flush_stdin",
    "reset_interrupt_async",
    "is_interrupted",
    "wait_for_interrupt_async",
    "_flush_stdin",  # 向后兼容别名
]

# 全局 threading Event — 不绑定任何事件循环，跨循环安全
_interrupted = threading.Event()


async def is_interrupted_async() -> bool:
    """检查是否已请求中断。线程安全，跨事件循环安全。"""
    return _interrupted.is_set()


def request_interrupt_async() -> None:
    """请求中断所有异步任务。线程安全（threading.Event.set() 是线程安全的）。"""
    _interrupted.set()


def flush_stdin(input_instance=None) -> None:
    """彻底清空 stdin 残留字节。

    若传入 Input 实例且其有 flush_stdin_buffer 方法，则委托给 Input 处理；
    否则回退到直接操作 sys.stdin 的旧路径（向后兼容）。

    旧路径文档：
    ESC 中断大模型输出后，EscapeMonitor 已切换到原始模式读取按键，
    但 '\x1b' 等字节可能残留在 stdin 缓冲区中未被完全消费。
    残留字节会污染后续 prompt_toolkit / Picker 的输入事件循环，
    导致 /editmsg 等交互式选择器立即接收到 ESC 信号而进入非交互模式。

    本函数在中断清除点（reset_interrupt_async）和交互选择器入口
    （_interactive_message_select）两处调用，形成双重保障：
    - 根上清：中断信号复位时第一时间排空
    - 入口清：Picker 启动前再兜底一次
    """
    # ★ 策略模式：有 Input 实例时委托给 Input.flush_stdin_buffer()
    if input_instance is not None and hasattr(input_instance, 'flush_stdin_buffer'):
        input_instance.flush_stdin_buffer()
        return

    # ── 旧路径：直接操作 sys.stdin（向后兼容无 Input 实例的调用方） ──
    _flushed = 0
    while _flushed < 50:
        try:
            ready = select.select([sys.stdin], [], [], 0.05)
            if not ready[0]:
                break
            sys.stdin.read(1)
            _flushed += 1
        except (ValueError, OSError, TypeError):
            # stdin 已关闭或不可读时安全退出
            break
    # 用 termios.tcflush 兜底：select+read 在某些系统（如 Android Termux）
    # 上可能清不干净残留的 \x1b 字节。tcflush 直接从内核输入缓冲区丢弃
    # 所有未读数据，比 select+read 更彻底。
    try:
        import termios as _termios
        _termios.tcflush(sys.stdin.fileno(), _termios.TCIFLUSH)
    except Exception:
        _logger.debug("termios.tcflush 失败（非关键）")
    # Windows 回退：使用 msvcrt 清空 stdin 缓冲区
    # Windows 上 select.select([sys.stdin]) 抛出 OSError（仅支持 socket fd），
    # termios 模块也不存在，必须通过 msvcrt.kbhit/getch 排空。
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except ImportError:
        pass


def reset_interrupt_async(input_instance=None) -> None:
    """清除中断信号，并清空 stdin 残留字节。

    在每轮异步用户交互开始时调用，确保：
    1. 中断信号已复位
    2. stdin 缓冲区的残留 ESC 字节已被排空

    Args:
        input_instance: 可选的 Input 实例。传入时优先委托其 flush_stdin_buffer()
            方法清空缓冲区；None 时走旧路径（直接操作 sys.stdin）。
    """
    _interrupted.clear()
    flush_stdin(input_instance)


# ── 同步桥接（用于 sync 代码调用 async 中断） ──────────────

def is_interrupted() -> bool:
    """兼容同步检查 — 读取 threading.Event 状态。

    适用于 sync 代码（如 tool 执行）中需要检查中断的场景。
    threading.Event.is_set() 不涉及锁，线程安全。
    """
    return _interrupted.is_set()


async def wait_for_interrupt_async(timeout: float) -> bool:
    """等待中断信号或超时，返回是否被中断。

    通过 loop.run_in_executor() 将 blocking wait 调度到线程池执行，
    不绑定当前事件循环，跨循环安全。
    中断响应延迟 = executor 调度延迟 + 线程切换延迟（通常 <5ms）。

    Args:
        timeout: 最长等待秒数

    Returns:
        True — 在超时前收到中断信号
        False — 超时（未收到中断信号）
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _interrupted.wait, timeout)

# 向后兼容别名
_flush_stdin = flush_stdin
