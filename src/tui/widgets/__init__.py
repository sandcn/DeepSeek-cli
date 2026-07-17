"""TUI 部件层 — 底部栏、状态栏、命令面板、会话切换器、锁定、补全、光标追踪等。

提供终端底部固定输入栏（_BottomBar）、单数据源状态栏（StatusBar）、
命令面板（CommandPalette）、会话切换器（SessionSwitcher）、
锁定原语（render_lock/io_lock/locked_print）、补全引擎（CompletionEngine）、
光标追踪（CursorTracker）和 stdout 行追踪（_StdoutLineTracker）。
"""

from __future__ import annotations

# ── 底层工具（轻量模块，无循环导入风险） ──
from .lock import (
    render_lock, io_lock, output_lock, diff_active,
    _try_acquire_io_lock, _try_acquire_output_lock,
    locked_print, OUTPUT_LOCK_TIMEOUT,
)
from .cursor_tracker import CursorTracker, CursorPosition
from .stdout_tracker import _StdoutLineTracker
from .completion import CompletionEngine, CompletionItem

# ── 延迟导入（避免循环导入：lock → widgets.__init__ → selector_base → bottom_bar → terminal → ui.colors → ...） ──

def _lazy_import(mod_path: str):
    """导入并缓存到模块命名空间。"""
    import importlib  # noqa: PLC0415
    # 绝对路径（含点）视为完整模块路径
    if '.' in mod_path and not mod_path.startswith('.'):
        return importlib.import_module(mod_path)
    return importlib.import_module(mod_path, __package__)


def __getattr__(name: str):
    _LAZY = {
        'Widget': ('src.tui.widget_base', 'Widget'),
        'RenderBuffer': ('src.tui.render_buffer', 'RenderBuffer'),
        'Vertical': ('src.tui.layout', 'Vertical'),
        'Horizontal': ('src.tui.layout', 'Horizontal'),
        'Padding': ('src.tui.layout', 'Padding'),
        'Border': ('src.tui.layout', 'Border'),
        'BaseBottomBarSelector': ('.selector_base', 'BaseBottomBarSelector'),
        "StatusBarWidget": ('.status_bar_widget', 'StatusBarWidget'),
        'StatusBar': ('.status_bar', 'StatusBar'),
        'render_normal': ('.status_bar', 'render_normal'),
        'build_normal_parts': ('.status_bar', 'build_normal_parts'),
        'render_streaming_line': ('.status_bar', 'render_streaming_line'),
        'CommandPalette': ('.command_palette', 'CommandPalette'),
        'SessionSwitcher': ('.session_switcher', 'SessionSwitcher'),
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
    "render_lock", "io_lock", "output_lock", "diff_active",
    "_try_acquire_io_lock", "_try_acquire_output_lock",
    "locked_print", "OUTPUT_LOCK_TIMEOUT",
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
    "Widget", "RenderBuffer", "Vertical",
    "Horizontal", "Padding", "Border",
]
