"""kitty — kitty 键盘协议常量与工具（React Ink v6/v7 ``kittyFlags`` 等价）。

官方 React Ink 导出 ``kittyFlags`` / ``kittyModifiers`` / ``resolveFlags`` 与
``KittyKeyboardOptions``；本模块对齐同名 API，并提供协议启用/禁用的终端
控制序列构建（供 ``render({kittyKeyboard})`` 使用）。

参考协议：https://sw.kovidgoyal.net/kitty/keyboard-protocol/
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

_logger = logging.getLogger(__name__)

#: 协议增强标志（``render({kittyKeyboard: {flags: [...]}})`` 可用名）。
kittyFlags = {
    "disambiguateEscapeCodes": 1,
    "reportEventTypes": 2,
    "reportAlternateKeys": 4,
    "reportAllKeysAsEscapeCodes": 8,
    "reportAssociatedText": 16,
}

#: 修饰键位掩码（kitty CSI-u 的 ``modifier = 1 + 位掩码``）。
kittyModifiers = {
    "shift": 1,
    "alt": 2,
    "ctrl": 4,
    "super": 8,
    "hyper": 16,
    "meta": 32,
    "capsLock": 64,
    "numLock": 128,
}


def resolveFlags(flags: Iterable[str] | None) -> int:
    """把标志名列表解析为位掩码（未知名忽略，None → 0）。"""
    if not flags:
        return 0
    total = 0
    for name in flags:
        total |= kittyFlags.get(name, 0)
    return total


def encode_modifiers(bits: int) -> int:
    """把位掩码编码为 CSI-u 修饰值（``1 + bits``，负数视作 0）。"""
    try:
        bits = int(bits)
    except (TypeError, ValueError):
        return 1
    return max(0, bits) + 1


def resolve_kitty_options(options: Any, querier: Any = None) -> int:
    """解析 ``kittyKeyboard`` 选项为「是否启用 + 标志掩码」。

    接受：
      - None/False：不启用 → 返回 -1；
      - True：启用（默认标志 ``disambiguateEscapeCodes``）→ 返回该掩码；
      - dict：``{"mode": "auto"|"enabled"|"disabled", "flags": [...]}``——
        ``disabled`` 返回 -1；``enabled`` 返回 ``resolveFlags(flags)``
        （flags 缺省为 ``["disambiguateEscapeCodes"]``）；``auto`` 时若提供
        ``querier``（``() -> int | None``）则**实际查询终端**，查询不到
        （非 TTY/不支持/超时）返回 -1（不启用）。

    ★ React Ink v7 对齐：auto 模式从「硬编码终端白名单」改为**查询所有终端**
    ——终端支持则由应答给出标志掩码，不支持/无应答则不启用（错误启用会让
    普通按键变成 CSI-u 序列而宿主解析不了）。
    """
    if options is None or options is False:
        return -1
    if options is True:
        return kittyFlags["disambiguateEscapeCodes"]
    if isinstance(options, dict):
        mode = options.get("mode")
        if mode == "disabled":
            return -1
        flags = options.get("flags")
        default_flags = (
            resolveFlags(flags) if flags else kittyFlags["disambiguateEscapeCodes"]
        )
        if mode == "auto" and querier is not None:
            try:
                reported = querier()
            except Exception:
                return -1
            if reported is None:
                return -1
            # 终端应答给出的是**已支持标志**：与请求标志取交集（缺省请求标志
            # 时直接用应答值）。
            try:
                reported = int(reported)
            except (TypeError, ValueError, OverflowError):
                return -1
            if reported < 0:
                return -1
            return (reported & default_flags) if flags else reported
        return default_flags
    return -1


#: kitty 键盘协议查询序列（``CSI ? u``）——终端以 ``CSI ? <flags> u`` 应答。
_QUERY_SEQUENCE = "\x1b[?u"
#: 查询应答正则（``ESC[?<flags>u``）。
_QUERY_REPLY_RE = re.compile(rb"\x1b\[\?(\d+)u")
#: 查询等待超时（秒）——不支持协议的终端不回包，短超时避免阻塞启动。
_QUERY_TIMEOUT = 0.2


def decode_query_reply(data: bytes) -> int | None:
    """从终端应答字节中解析 kitty 标志掩码（未匹配返回 None）。"""
    match = _QUERY_REPLY_RE.search(data)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def query_kitty_support(
    stdin_fd: int | None = None, out_stream: Any = None, timeout: float | None = None,
) -> int | None:
    """查询终端 kitty 键盘协议支持（``CSI ? u`` → ``CSI ? <flags> u``）。

    仅在 stdin/stdout 均为 TTY 时发送查询（否则直接返回 None——管道/重定向
    环境不应写入控制序列）。查询期间 stdin 临时置 raw，结束无条件恢复。

    Returns:
        终端报告的支持标志掩码；非 TTY/超时/无应答返回 None。
    """
    import os as _os
    import select as _select
    import sys as _sys
    import time as _time

    from src._compat_termios import HAS_TERMIOS, termios, tty

    if not HAS_TERMIOS:
        return None
    try:
        fd = _sys.stdin.fileno() if stdin_fd is None else int(stdin_fd)
    except (AttributeError, ValueError, OSError, TypeError):
        return None
    stream = out_stream if out_stream is not None else _sys.__stdout__
    try:
        if not _os.isatty(fd) or not _os.isatty(stream.fileno()):
            return None
    except (AttributeError, ValueError, OSError):
        return None
    wait = _QUERY_TIMEOUT if timeout is None else max(0.01, float(timeout))
    try:
        saved = termios.tcgetattr(fd)
    except Exception:
        return None
    try:
        tty.setraw(fd)
        stream.write(_QUERY_SEQUENCE)
        stream.flush()
        deadline = _time.monotonic() + wait
        buf = b""
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = _select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                break
            if not ready:
                break
            try:
                chunk = _os.read(fd, 64)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            value = decode_query_reply(buf)
            if value is not None:
                return value
    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except Exception:
            _logger.debug("kitty 查询后恢复 termios 失败", exc_info=True)
    return None


def enable_sequence(flags: int = 0) -> str:
    """启用 kitty 键盘协议的终端序列（``CSI > flags u``）。

    ``flags`` 为位掩码（``resolveFlags`` 结果）；非法/非正数回退 1
    （``disambiguateEscapeCodes``）。
    """
    try:
        value = int(flags)
    except (TypeError, ValueError, OverflowError):
        value = 1
    if value <= 0:
        value = 1
    return f"\x1b[>{value}u"


def disable_sequence() -> str:
    """禁用（弹出）kitty 键盘协议的终端序列（``CSI < u``）。"""
    return "\x1b[<u"


__all__ = [
    "kittyFlags",
    "kittyModifiers",
    "resolveFlags",
    "encode_modifiers",
    "resolve_kitty_options",
    "decode_query_reply",
    "query_kitty_support",
    "enable_sequence",
    "disable_sequence",
]
