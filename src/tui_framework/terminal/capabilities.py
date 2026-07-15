"""终端能力检测 — TrueColor / UTF-8 / Emoji / 256色

检测以下终端能力并缓存为模块级常量：
  1. TrueColor（24-bit 真彩色）支持
  2. UTF-8 编码支持
  3. Emoji 渲染支持
  4. 256 色支持（兼容 xterm-256color 标准）

设计原则：
  - 检测结果在模块首次导入后缓存（通过 ``functools.cache``），后续 O(1) 访问
  - 惰性检测：首次访问对应检测函数时触发计算，避免导入时副作用
  - 双重检测：通过 Blessed Terminal + 环境变量交叉验证确保准确性
  - 安全降级：检测失败时返回保守值（False），不抛异常

使用方式：
    from tui_framework.terminal.capabilities import (
        supports_truecolor,
        supports_256color,
        supports_utf8,
        supports_emoji,
        get_capabilities_summary,
    )
"""

from __future__ import annotations

import locale
import logging
import os
import sys
from functools import cache

from .blessed import get_terminal

_logger = logging.getLogger(__name__)


@cache
def _detect_truecolor() -> bool:
    """检测终端是否支持 TrueColor（24-bit 真彩色）。"""
    try:
        term = get_terminal()
        if term.number_of_colors >= 16777216:
            return True
    except Exception:
        _logger.debug("TrueColor 检测（Blessed）失败", exc_info=True)

    colorterm = os.environ.get("COLORTERM", "")
    if colorterm in ("truecolor", "24bit"):
        return True

    return False


@cache
def _detect_256color() -> bool:
    """检测终端是否支持 256 色。"""
    try:
        term = get_terminal()
        if term.number_of_colors >= 256:
            return True
    except Exception:
        _logger.debug("256色检测（Blessed）失败", exc_info=True)

    term_env = os.environ.get("TERM", "")
    if "256color" in term_env:
        return True

    colorterm = os.environ.get("COLORTERM", "")
    if colorterm:
        return True

    return False


@cache
def _detect_utf8() -> bool:
    """检测终端环境是否支持 UTF-8 编码。"""
    try:
        if locale.getpreferredencoding().upper() == "UTF-8":
            return True
    except Exception:
        _logger.debug("UTF-8 检测（locale）失败", exc_info=True)

    try:
        if sys.stdout.encoding and sys.stdout.encoding.upper() == "UTF-8":
            return True
    except Exception:
        _logger.debug("UTF-8 检测（stdout）失败", exc_info=True)

    for env_name in ("LANG", "LC_CTYPE", "LC_ALL"):
        val = os.environ.get(env_name, "")
        if "UTF-8" in val.upper() or "utf8" in val.lower():
            return True

    return False


@cache
def _detect_emoji() -> bool:
    """检测终端是否支持 Emoji 渲染。"""
    if not _detect_utf8():
        return False

    term_env = os.environ.get("TERM", "").lower()
    no_emoji_terms = {"linux", "vt100", "vt220", "ansi", "dumb", "cons25"}
    if term_env in no_emoji_terms:
        return False

    if sys.platform == "win32":
        term_program = os.environ.get("TERM_PROGRAM", "")
        if term_program == "microsoft":
            return True
        return False

    return True


def supports_truecolor() -> bool:
    """终端是否支持 TrueColor（24-bit 真彩色）。"""
    return _detect_truecolor()


def supports_256color() -> bool:
    """终端是否支持 256 色。"""
    return _detect_256color()


def supports_utf8() -> bool:
    """终端环境是否支持 UTF-8 编码。"""
    return _detect_utf8()


def supports_emoji() -> bool:
    """终端是否支持 Emoji 渲染。"""
    return _detect_emoji()


def get_capabilities_summary() -> dict[str, bool | int]:
    """获取终端能力摘要。"""
    color_count = 0
    try:
        color_count = get_terminal().number_of_colors
    except Exception:
        pass

    return {
        "truecolor": _detect_truecolor(),
        "256color": _detect_256color(),
        "utf8": _detect_utf8(),
        "emoji": _detect_emoji(),
        "color_count": color_count,
    }


__all__ = [
    "supports_truecolor",
    "supports_256color",
    "supports_utf8",
    "supports_emoji",
    "get_capabilities_summary",
]
