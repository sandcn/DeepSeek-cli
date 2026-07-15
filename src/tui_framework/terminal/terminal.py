"""终端宽度检测 — 框架内核层。

提供终端宽度 TTL 缓存查询和窄屏自适应函数。
从 src/tui/terminal/terminal.py 提取宽度相关子集，
去除 LockedTerminal 和 raw mode 等业务依赖。

使用方式：
    from tui_framework.terminal.terminal import get_terminal_width, is_narrow
"""

from __future__ import annotations

from .blessed import get_terminal
from ..core.ttl_cache import TTLCache


def _fetch_terminal_width() -> int:
    """获取终端宽度（列数），通过 Blessed Terminal，异常时回退 80。"""
    try:
        return get_terminal().width
    except Exception:
        return 80


# 终端宽度 TTL 缓存实例（0.5s TTL，减少 10Hz tick 循环中 syscall 开销）
_term_width_cache: TTLCache[int] = TTLCache(
    fetcher=_fetch_terminal_width, ttl=0.5,
)


# ═══════════════════════════════════════════════════════════
# 终端宽度检测（TTL 缓存）
# ═══════════════════════════════════════════════════════════

NARROW_THRESHOLD = 80
EXTRA_NARROW_THRESHOLD = 50


def get_terminal_width() -> int:
    """获取终端宽度（列数），带 0.5s TTL 缓存。"""
    return _term_width_cache.get()


def set_narrow_threshold(normal: int, extra: int) -> None:
    """允许用户按需调整窄屏阈值（全局生效）。

    Args:
        normal: 普通窄屏阈值（列数），< 此值视为窄屏，默认 80。
        extra: 超窄屏阈值（列数），< 此值视为超窄屏，默认 50。
    """
    global NARROW_THRESHOLD, EXTRA_NARROW_THRESHOLD
    NARROW_THRESHOLD = normal
    EXTRA_NARROW_THRESHOLD = extra


# ═══════════════════════════════════════════════════════════
# 窄屏自适应函数
# ═══════════════════════════════════════════════════════════


def is_narrow() -> bool:
    """当前终端是否为窄屏（< 80 列）"""
    return get_terminal_width() < NARROW_THRESHOLD


def _narrow_default(normal: int) -> int:
    return max(25, normal // 2)


def _extra_narrow_default(normal: int) -> int:
    return max(15, normal // 4)


def narrow_truncate(normal: int, narrow: int | None = None,
                    extra_narrow: int | None = None) -> int:
    """按终端宽度自适应截断值。

    Args:
        normal: 正常宽屏时的值。
        narrow: 窄屏时的值（None 时自动计算为 normal//2，至少 25）。
        extra_narrow: 超窄屏时的值（None 时自动计算为 normal//4，至少 15）。

    Returns:
        根据当前终端宽度选定的值。
    """
    w = get_terminal_width()
    if w >= NARROW_THRESHOLD:
        return normal
    if w >= EXTRA_NARROW_THRESHOLD:
        return narrow if narrow is not None else _narrow_default(normal)
    return extra_narrow if extra_narrow is not None else _extra_narrow_default(normal)


def narrow_indent(normal: int = 2) -> int:
    """按终端宽度自适应缩进。

    宽屏: normal, 窄屏: max(1, normal-1), 超窄屏: 0
    """
    w = get_terminal_width()
    if w >= NARROW_THRESHOLD:
        return normal
    if w >= EXTRA_NARROW_THRESHOLD:
        return max(1, normal - 1)
    return 0


def narrow_sep_width(max_width: int = 40) -> int:
    """按终端宽度自适应分隔线宽度。"""
    tw = get_terminal_width()
    if tw >= NARROW_THRESHOLD:
        return min(max_width, tw - 4)
    return max(10, min(max_width - 10, tw - 4))


__all__ = [
    "get_terminal_width",
    "is_narrow",
    "narrow_truncate", "narrow_indent",
    "narrow_sep_width",
    "set_narrow_threshold",
    "NARROW_THRESHOLD",
    "EXTRA_NARROW_THRESHOLD",
]
