"""窄屏自适应函数 — 从 terminal.py 拆分的窄屏子集。

为需要窄屏检测但不希望引入整个 terminal.py 依赖的模块提供轻量导入路径。

使用方式：
    from src.tui.terminal.narrow import is_narrow, narrow_truncate
"""

from __future__ import annotations

from .terminal import get_terminal_width, NARROW_THRESHOLD, EXTRA_NARROW_THRESHOLD

__all__ = [
    "NARROW_THRESHOLD",
    "EXTRA_NARROW_THRESHOLD",
    "is_narrow",
    "narrow_truncate",
    "narrow_indent",
    "narrow_sep_width",
]


def is_narrow() -> bool:
    """当前终端是否为窄屏（< 80 列）"""
    return get_terminal_width() < NARROW_THRESHOLD


def _narrow_default(normal: int) -> int:
    return max(25, normal // 2)


def _extra_narrow_default(normal: int) -> int:
    return max(15, normal // 4)


def narrow_truncate(normal: int, narrow: int | None = None,
                    extra_narrow: int | None = None) -> int:
    w = get_terminal_width()
    if w >= NARROW_THRESHOLD:
        return normal
    if w >= EXTRA_NARROW_THRESHOLD:
        return narrow if narrow is not None else _narrow_default(normal)
    return extra_narrow if extra_narrow is not None else _extra_narrow_default(normal)


def narrow_indent(normal: int = 2) -> int:
    w = get_terminal_width()
    if w >= NARROW_THRESHOLD:
        return normal
    if w >= EXTRA_NARROW_THRESHOLD:
        return max(1, normal - 1)
    return 0


def narrow_sep_width(max_width: int = 40) -> int:
    tw = get_terminal_width()
    if tw >= NARROW_THRESHOLD:
        return min(max_width, tw - 4)
    return max(10, min(max_width - 10, tw - 4))
