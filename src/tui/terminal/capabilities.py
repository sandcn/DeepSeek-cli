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
    from src.tui.terminal.capabilities import (
        supports_truecolor,
        supports_256color,
        supports_utf8,
        supports_emoji,
        get_capabilities_summary,
    )

    if supports_truecolor():
        # 使用 24-bit 颜色
        pass

    summary = get_capabilities_summary()
    # -> {"truecolor": True, "256color": True, "utf8": True, "emoji": True, "color_count": 256}
"""

from __future__ import annotations

import locale
import logging
import os
import sys
from functools import cache

from .blessed import get_terminal

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 私有检测函数（@cache 确保只执行一次）
# ═══════════════════════════════════════════════════════════


@cache
def _detect_truecolor() -> bool:
    """检测终端是否支持 TrueColor（24-bit 真彩色）。

    检测策略（任一满足即为 True）：
      1. Blessed ``Terminal.number_of_colors >= 2^24``（16777216）
      2. 环境变量 ``COLORTERM`` 为 ``truecolor`` 或 ``24bit``

    Returns:
        True 表示终端支持 TrueColor。
    """
    # 策略1：通过 Blessed Terminal 查询颜色数量
    try:
        term = get_terminal()
        if term.number_of_colors >= 16777216:
            return True
    except Exception:
        _logger.debug("TrueColor 检测（Blessed）失败", exc_info=True)

    # 策略2：检查环境变量 COLORTERM
    colorterm = os.environ.get("COLORTERM", "")
    if colorterm in ("truecolor", "24bit"):
        return True

    return False


@cache
def _detect_256color() -> bool:
    """检测终端是否支持 256 色。

    检测策略：
      1. Blessed ``Terminal.number_of_colors >= 256``
      2. 环境变量 ``TERM`` 包含 ``256color``
      3. 环境变量 ``COLORTERM`` 非空（暗示彩色终端）

    Returns:
        True 表示终端支持 256 色及以上。
    """
    # 策略1：通过 Blessed Terminal 查询颜色数量
    try:
        term = get_terminal()
        if term.number_of_colors >= 256:
            return True
    except Exception:
        _logger.debug("256色检测（Blessed）失败", exc_info=True)

    # 策略2：环境变量 TERM
    term_env = os.environ.get("TERM", "")
    if "256color" in term_env:
        return True

    # 策略3：环境变量 COLORTERM（非空意味着至少部分彩色支持）
    colorterm = os.environ.get("COLORTERM", "")
    if colorterm:
        return True

    return False


@cache
def _detect_utf8() -> bool:
    """检测终端环境是否支持 UTF-8 编码。

    检测策略（多级降级）：
      1. ``locale.getpreferredencoding()`` 返回 ``UTF-8``
      2. ``sys.stdout.encoding`` 为 ``utf-8``
      3. 环境变量 ``LANG`` / ``LC_CTYPE`` / ``LC_ALL`` 包含 ``UTF-8`` 或 ``utf8``

    Returns:
        True 表示终端环境支持 UTF-8。
    """
    # 策略1：locale 首选编码
    try:
        if locale.getpreferredencoding().upper() == "UTF-8":
            return True
    except Exception:
        _logger.debug("UTF-8 检测（locale）失败", exc_info=True)

    # 策略2：stdout 编码
    try:
        if sys.stdout.encoding and sys.stdout.encoding.upper() == "UTF-8":
            return True
    except Exception:
        _logger.debug("UTF-8 检测（stdout）失败", exc_info=True)

    # 策略3：环境变量
    for env_name in ("LANG", "LC_CTYPE", "LC_ALL"):
        val = os.environ.get(env_name, "")
        if "UTF-8" in val.upper() or "utf8" in val.lower():
            return True

    return False


@cache
def _detect_emoji() -> bool:
    """检测终端是否支持 Emoji 渲染。

    Emoji 渲染需要同时满足以下条件：
      1. 终端环境支持 UTF-8 编码（依赖 ``_detect_utf8()``）
      2. 终端类型不是已知不支持 Emoji 的古老终端（通过 ``TERM`` 排除）
      3. Windows 平台：仅 ``TERM_PROGRAM=microsoft``（Windows Terminal）时认为支持

    Returns:
        True 表示终端可能支持 Emoji 渲染。
    """
    # 前提：必须支持 UTF-8
    if not _detect_utf8():
        return False

    term_env = os.environ.get("TERM", "").lower()

    # 排除已知不支持 Emoji 的终端类型
    no_emoji_terms = {"linux", "vt100", "vt220", "ansi", "dumb", "cons25"}
    if term_env in no_emoji_terms:
        return False

    # Windows 平台特殊处理
    if sys.platform == "win32":
        term_program = os.environ.get("TERM_PROGRAM", "")
        if term_program == "microsoft":
            return True
        return False

    return True


# ═══════════════════════════════════════════════════════════
# 公有 API
# ═══════════════════════════════════════════════════════════


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
    """获取终端能力摘要。

    用于日志记录或对外暴露的探测结果快照。

    Returns:
        ``{
            "truecolor": bool,    # TrueColor 支持
            "256color": bool,     # 256 色支持
            "utf8": bool,         # UTF-8 编码支持
            "emoji": bool,        # Emoji 渲染支持
            "color_count": int,   # Blessed 报告的颜色数量（0 表示检测失败）
        }``
    """
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
