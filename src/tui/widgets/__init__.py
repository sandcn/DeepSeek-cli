"""TUI 部件层 — 底部栏、状态栏、命令面板、会话切换器、锁定、补全、光标追踪等。

提供终端底部固定输入栏（_BottomBar）、单数据源状态栏（StatusBar）、
命令面板（CommandPalette）、会话切换器（SessionSwitcher）、
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

# ── 延迟导入（避免循环导入） ──
# 循环导入链：terminal.terminal → widgets.lock → widgets.__init__ → ... → terminal.terminal
# 涉及终端操作的模块（bottom_bar、status_bar、command_palette 等）必须延迟导入，
# 因为这些模块直接或间接导入 terminal.terminal.is_narrow / get_terminal_width。
# 当 terminal.terminal 在其模块顶层导入 widgets.lock 时，会触发 widgets.__init__ 加载，
# 此时若再立即加载这些终端相关模块，就会形成环形依赖。

def _lazy_import(mod_path: str):
    """导入并缓存到模块命名空间。"""
    import importlib  # noqa: PLC0415
    # 绝对路径（含点）视为完整模块路径
    if '.' in mod_path and not mod_path.startswith('.'):
        return importlib.import_module(mod_path)
    return importlib.import_module(mod_path, __package__)


def __getattr__(name: str):
    _LAZY = {
        # selector_base（间接导入 terminal.terminal 通过 bottom_bar.selection → terminal.blessed？
        #   不，blessed 不导入 terminal.terminal，但 bottom_bar/__init__.py 被触发时
        #   会导入 .bar → .completion → terminal.terminal，形成循环）
        'BaseBottomBarSelector': ('.selector_base', 'BaseBottomBarSelector'),
        # status_bar / status_bar_widget（直接导入 terminal.terminal）
        "StatusBarWidget": ('.status_bar_widget', 'StatusBarWidget'),
        'StatusBar': ('.status_bar', 'StatusBar'),
        'render_normal': ('.status_bar', 'render_normal'),
        'build_normal_parts': ('.status_bar', 'build_normal_parts'),
        'render_streaming_line': ('.status_bar', 'render_streaming_line'),
        # command_palette（直接导入 terminal.terminal）
        'CommandPalette': ('.command_palette', 'CommandPalette'),
        # session_switcher（直接导入 terminal.terminal）
        'SessionSwitcher': ('.session_switcher', 'SessionSwitcher'),
        # bottom_bar（bar.py → completion.py → terminal.terminal，形成循环）
        '_BottomBar': ('.bottom_bar', '_BottomBar'),
        '_StatusMixin': ('.bottom_bar', '_StatusMixin'),
        '_get_snapshot': ('.bottom_bar', '_get_snapshot'),
        '_TOKEN_SPEED_SNAPSHOT': ('.bottom_bar', '_TOKEN_SPEED_SNAPSHOT'),
        'run_bottom_bar_selection': ('.bottom_bar', 'run_bottom_bar_selection'),
        '_CompletionPopup': ('.bottom_bar', '_CompletionPopup'),
    }
    if name in _LAZY:
        mod_path, attr = _LAZY[name]
        mod = _lazy_import(mod_path)
        result = getattr(mod, attr)
        globals()[name] = result
        return result
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
    # selector_base
    "BaseBottomBarSelector",
    # status_bar
    "StatusBar", "StatusBarWidget", "render_normal", "build_normal_parts", "render_streaming_line",
    # command_palette
    "CommandPalette",
    # session_switcher
    "SessionSwitcher",
    # bottom_bar
    "_BottomBar", "_StatusMixin",
    "_get_snapshot", "_TOKEN_SPEED_SNAPSHOT",
    "run_bottom_bar_selection", "_CompletionPopup",
    # widget framework
    "Widget", "RenderBuffer",
]
