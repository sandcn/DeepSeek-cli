"""TUI 部件层 — 底部栏、状态栏、锁定、补全、光标追踪等。

提供终端底部固定输入栏（_BottomBar）、单数据源状态栏（StatusBarWidget）、
锁定原语（render_lock/io_lock）、补全引擎（CompletionEngine）、
光标追踪（CursorTracker）和 stdout 行追踪（_StdoutLineTracker）。
"""

from __future__ import annotations

# ── 底层工具（轻量模块，无循环导入风险） ──
from .lock import (
    render_lock, io_lock, diff_active,
    _try_acquire_output_lock, OUTPUT_LOCK_TIMEOUT,
)
from .cursor_tracker import CursorTracker, CursorPosition
from .stdout_tracker import _StdoutLineTracker
from .completion import CompletionEngine, CompletionItem

# ── 基础模块（无循环导入风险：不依赖 widgets 子包中的任何模块，
#    也不依赖 terminal.terminal，因此不会与 widgets.lock → terminal.terminal
#    形成循环导入） ──
from ..widget_base import Widget
from ..render_buffer import RenderBuffer
from ..layout import (
    Vertical, Horizontal, Padding, Border, Grid, Center,
)

from .._lazy import LazyLoader

# ── 懒加载模块代理（避免循环导入） ──
# 循环导入链：terminal.terminal → widgets.lock → widgets.__init__ → ... → terminal.terminal
# 涉及终端操作的模块（bottom_bar、status_bar 等）必须延迟导入，
# 因为这些模块直接或间接导入 terminal.terminal.is_narrow / get_terminal_width。
# 当 terminal.terminal 在其模块顶层导入 widgets.lock 时，会触发 widgets.__init__ 加载，
# 此时若再立即加载这些终端相关模块，就会形成环形依赖。

_status_bar_widget_mod = LazyLoader("src.tui.widgets.status_bar_widget")
_bottom_bar_mod = LazyLoader("src.tui.widgets.bottom_bar")


# ── 符号到懒加载模块的映射（供 __getattr__ 使用） ──

_SYMBOL_MAP: dict[str, LazyLoader] = {
    "StatusBarWidget": _status_bar_widget_mod,
    '_BottomBar': _bottom_bar_mod,
    '_StatusMixin': _bottom_bar_mod,
    '_get_snapshot': _bottom_bar_mod,
    '_TOKEN_SPEED_SNAPSHOT': _bottom_bar_mod,
    'run_bottom_bar_selection': _bottom_bar_mod,
    '_CompletionPopup': _bottom_bar_mod,
}


def __getattr__(name: str):
    """模块级 __getattr__ — 从对应懒加载模块延迟解析符号。

    当 ``from src.tui.widgets import XXX`` 执行时，如果 XXX 不是模块的
    直接属性，Python 会调用此函数，从 _SYMBOL_MAP 中查找对应的
    LazyLoader 并执行延迟导入。

    Raises:
        AttributeError: 符号不在 __SYMBOL_MAP__ 中时抛出。
    """
    loader = _SYMBOL_MAP.get(name)
    if loader is not None:
        return getattr(loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """支持 dir() 列出所有导出符号。"""
    return sorted(__all__)

__all__ = [
    # lock
    "render_lock", "io_lock", "diff_active",
    "_try_acquire_output_lock", "OUTPUT_LOCK_TIMEOUT",
    # cursor_tracker
    "CursorTracker", "CursorPosition",
    # stdout_tracker
    "_StdoutLineTracker",
    # completion
    "CompletionEngine", "CompletionItem",
    # status_bar
    "StatusBarWidget",
    # bottom_bar
    "_BottomBar", "_StatusMixin",
    "_get_snapshot", "_TOKEN_SPEED_SNAPSHOT",
    "run_bottom_bar_selection", "_CompletionPopup",
    # widget framework
    "Widget", "RenderBuffer", "Vertical",
    "Horizontal", "Padding", "Border",
    "Grid", "Center",
]
