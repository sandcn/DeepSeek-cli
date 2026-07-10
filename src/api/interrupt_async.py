"""Async 全局中断信号 — 基于 threading.Event

跨事件循环安全。使用 threading.Event 替代 asyncio.Event，
避免模块级 Event 绑定到特定事件循环后，
在独立事件循环（如 _model_loops.py 每线程独立循环）中访问时
抛出 RuntimeError（Task got Future attached to a different loop）。
"""

from __future__ import annotations

import asyncio
import logging
import select
import sys
import threading
import time

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

# 全局 threading Event — 不绑定任何事件循环，跨事件循环安全
_interrupted_async = threading.Event()


async def is_interrupted_async() -> bool:
    """检查是否已请求中断。asyncio 安全。"""
    return _interrupted_async.is_set()


def request_interrupt_async() -> None:
    """请求中断所有异步任务。线程安全（threading.Event.set() 是线程安全的）。"""
    _interrupted_async.set()


def flush_stdin() -> None:
    """彻底清空 stdin 残留字节。

    ESC 中断大模型输出后，EscapeMonitor 已切换到原始模式读取按键，
    但 '\x1b' 等字节可能残留在 stdin 缓冲区中未被完全消费。
    残留字节会污染后续 prompt_toolkit / Picker 的输入事件循环，
    导致 /editmsg 等交互式选择器立即接收到 ESC 信号而进入非交互模式。

    本函数在中断清除点（reset_interrupt_async）和交互选择器入口
    （_interactive_message_select）两处调用，形成双重保障：
    - 根上清：中断信号复位时第一时间排空
    - 入口清：Picker 启动前再兜底一次
    """
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
        _termios.tcflush(sys.stdin, _termios.TCIFLUSH)
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


def reset_interrupt_async() -> None:
    """清除中断信号，并清空 stdin 残留字节。

    在每轮异步用户交互开始时调用，确保：
    1. 中断信号已复位
    2. stdin 缓冲区的残留 ESC 字节已被排空
    """
    _interrupted_async.clear()
    flush_stdin()


# ── 同步桥接（用于 sync 代码调用 async 中断） ──────────────

def is_interrupted() -> bool:
    """兼容同步检查 — 读取 threading.Event 状态。

    适用于 sync 代码（如 tool 执行）中需要检查中断的场景。
    threading.Event.is_set() 不涉及锁，线程安全。
    """
    return _interrupted_async.is_set()


async def wait_for_interrupt_async(timeout: float) -> bool:
    """等待中断信号或超时，返回是否被中断。

    使用轮询模式检查 threading.Event 状态，
    避免 threading.Event.wait() 阻塞事件循环。
    每 50ms 轮询一次，中断响应延迟 < 50ms + 事件循环调度延迟。

    Args:
        timeout: 最长等待秒数

    Returns:
        True — 在超时前收到中断信号
        False — 超时（未收到中断信号）
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _interrupted_async.is_set():
            return True
        await asyncio.sleep(0.05)
    return False


# 向后兼容别名
_flush_stdin = flush_stdin
