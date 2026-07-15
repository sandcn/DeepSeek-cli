"""窄屏自适应函数 — 轻量导入路径。

为需要窄屏检测但不希望引入整个 terminal.py 依赖的模块提供轻量导入路径。

使用方式：
    from tui_framework.terminal.narrow import is_narrow, narrow_truncate
"""

from __future__ import annotations

from . import terminal as _term

__all__ = [
    "is_narrow",
    "narrow_truncate",
    "narrow_indent",
    "narrow_sep_width",
]


def is_narrow() -> bool:
    """当前终端是否为窄屏（< 80 列）"""
    return _term.get_terminal_width() < _term.NARROW_THRESHOLD


def _narrow_default(normal: int) -> int:
    return max(25, normal // 2)


def _extra_narrow_default(normal: int) -> int:
    return max(15, normal // 4)


def narrow_truncate(normal: int, narrow: int | None = None,
                    extra_narrow: int | None = None) -> int:
    w = _term.get_terminal_width()
    if w >= _term.NARROW_THRESHOLD:
        return normal
    if w >= _term.EXTRA_NARROW_THRESHOLD:
        return narrow if narrow is not None else _narrow_default(normal)
    return extra_narrow if extra_narrow is not None else _extra_narrow_default(normal)


def narrow_indent(normal: int = 2) -> int:
    w = _term.get_terminal_width()
    if w >= _term.NARROW_THRESHOLD:
        return normal
    if w >= _term.EXTRA_NARROW_THRESHOLD:
        return max(1, normal - 1)
    return 0


def narrow_sep_width(max_width: int = 40) -> int:
    tw = _term.get_terminal_width()
    if tw >= _term.NARROW_THRESHOLD:
        return min(max_width, tw - 4)
    return max(10, min(max_width - 10, tw - 4))
