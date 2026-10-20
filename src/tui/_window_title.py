"""终端窗口标题 —— 设置、记忆与退出还原（唯一真源）。

背景：程序会在多个时机改写终端窗口标题（会话标题生成完成、``--load``
恢复会话、周期保存同步、``ink render(windowTitle=...)``）——修复前退出时
**不还原**，终端标签会一直停留在最后一次设置的会话标题上（用户需求：
「退出程序时要把原来的终端标题还原」）。

本模块把「写标题」收敛为一处，并记录程序是否接管过标题：

- ``set_terminal_title()``：所有设置入口共用（OSC 0 序列，控制字符剥离）；
  首次调用标记「已接管」，同时记住写入流（``ink render()`` 可传自定义流）；
- ``restore_terminal_title()``：退出（正常 / 异常 / ``atexit``）时还原：
  1) 环境变量 ``CHAT_TERMINAL_TITLE`` 显式指定恢复值 → 写回该值；
  2) 否则写**空标题**（``OSC 0;``）——终端回退自身默认标题，shell 提示符
     出现后会按自己的规则重新设置标题（这也是「未设置标题」的初始状态）；
- 从未接管标题（本程序没写过）时**不写任何序列**，绝不干扰用户终端；
- 幂等：重复还原只写一次；还原后再次设置标题会重新进入「已接管」状态。

为什么不做「真正读取原标题」：终端标题没有跨平台可用的读取接口
（XTWINOPS ``CSI 21 t`` 仅部分终端支持，且需从 stdin 收响应——会与 TUI
输入读取竞争、Windows Terminal 不支持）。因此采用「显式配置 > 清空回退」
的策略，既跨平台可预测，也不偷用户输入。
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import sys
import threading
from typing import Any

_logger = logging.getLogger(__name__)

#: 标题中的控制字符（OSC 序列注入防护：标题里出现 ``\x07``/ESC 会截断序列）。
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

#: 显式指定「退出时恢复的标题」的环境变量（可选；未设置则恢复为空标题）。
_RESTORE_ENV = "CHAT_TERMINAL_TITLE"

_lock = threading.RLock()
#: 最近一次写入标题所用的流（默认 ``sys.__stdout__``；ink render 可传自定义流）。
_stream: Any = None
#: 程序是否已接管标题（写过至少一次且尚未还原）。
_applied = False
#: 是否已还原（幂等标记；再次设置标题会复位）。
_restored = False
#: 退出恢复目标标题（``None`` = 清空 → 终端回退默认标题）。
_original: str | None = None
_original_resolved = False


def _resolve_original() -> str | None:
    """解析退出恢复目标（``CHAT_TERMINAL_TITLE``；空串视为未设置）。"""
    global _original, _original_resolved
    if _original_resolved:
        return _original
    _original_resolved = True
    try:
        value = os.environ.get(_RESTORE_ENV, "")
    except Exception:
        value = ""
    _original = _CONTROL_RE.sub("", str(value)).strip() or None
    return _original


def _sanitize(title: Any) -> str:
    """标题清洗（剥离控制字符，防 OSC 序列注入）。"""
    return _CONTROL_RE.sub("", str(title if title is not None else ""))


def _write(stream: Any, title: str) -> bool:
    """写 OSC 0 窗口标题序列（无流/非 TTY 时静默跳过）。"""
    out = stream if stream is not None else sys.__stdout__
    if out is None:  # pythonw / 无 TTY：静默跳过（与既有 set_window_title 一致）
        return False
    try:
        out.write(f"\033]0;{title}\007")
        flush = getattr(out, "flush", None)
        if flush is not None:
            flush()
        return True
    except (OSError, ValueError, AttributeError):
        _logger.debug("窗口标题写入失败（无 TTY？）: %r", title, exc_info=True)
        return False


def set_terminal_title(title: Any, *, stream: Any = None) -> None:
    """设置终端窗口标题（所有入口共用；记录「已接管」以便退出还原）。

    Args:
        title: 标题文本（控制字符剥离后写入）。
        stream: 写入流（默认 ``sys.__stdout__``；``ink render()`` 传会话输出流）。
    """
    global _stream, _applied, _restored
    text = _sanitize(title)
    with _lock:
        _stream = stream if stream is not None else (sys.__stdout__)
        _applied = True
        _restored = False
        _write(_stream, text)


def restore_terminal_title() -> bool:
    """还原终端窗口标题（退出路径：正常 / 异常 / ``atexit``）。

    Returns:
        True = 写出了还原序列；False = 本程序从未接管标题或已还原（幂等）。
    """
    global _applied, _restored
    with _lock:
        if not _applied or _restored:
            return False
        _applied = False
        _restored = True
        original = _resolve_original()
        return _write(_stream, original or "")


def reset_state_for_tests() -> None:
    """复位模块状态（仅测试用：避免用例间互相影响）。"""
    global _stream, _applied, _restored, _original, _original_resolved
    with _lock:
        _stream = None
        _applied = False
        _restored = False
        _original = None
        _original_resolved = False


def title_managed() -> bool:
    """程序当前是否接管着标题（供诊断/测试断言）。"""
    with _lock:
        return bool(_applied)


#: 进程退出兜底：覆盖不经过应用主循环的路径（``chat.py -p`` 单次问答、
#: 初始化失败、未捕获异常等）。幂等——主路径已还原时此处不再写。
atexit.register(restore_terminal_title)

__all__ = [
    "set_terminal_title",
    "restore_terminal_title",
    "reset_state_for_tests",
    "title_managed",
]
