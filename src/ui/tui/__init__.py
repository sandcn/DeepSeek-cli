"""TUI 模块 — 兼容存根，所有符号重导出到 src.tui"""

from __future__ import annotations

import sys
import importlib

# ── 子模块重导出（保持旧 from src.ui.tui import message_editor 可用） ──
_MODULE_ALIASES = {
    'message_editor': 'src.tui.pipeline.message_editor',
    'status_bar': 'src.tui.widgets.status_bar',
    'command_palette': 'src.tui.widgets.command_palette',
    'session_switcher': 'src.tui.widgets.session_switcher',
    '_animator': 'src.tui.core.animator',
    '_effects': 'src.tui.core.effects',
    '_text_utils': 'src.tui.core.text_utils',
    '_selector_base': 'src.tui.widgets.selector_base',
}

for _attr, _target in _MODULE_ALIASES.items():
    _mod = importlib.import_module(_target)
    sys.modules[f'{__name__}.{_attr}'] = _mod
    globals()[_attr] = _mod

# ── 便捷导出 ──
from src.tui.terminal import (
    is_narrow, get_terminal_width,
    narrow_truncate, narrow_indent, narrow_sep_width,
)
from src.tui.pipeline import MessageEditor, edit_current_messages, display_messages
from src.tui.widgets import StatusBar
from src.tui.widgets import CommandPalette
from src.tui.widgets import SessionSwitcher

from src.tui.core import (
    TUIStateTree, UISessionState, InputState,
)

from src.tui.terminal.ports import ILockedTerminal
from src.tui.widgets.selector_base import BaseBottomBarSelector
from src.tui.core.text_utils import truncate

__all__ = [
    # 子模块（公开）
    "message_editor", "status_bar", "command_palette",
    "session_switcher", "_effects",
    # ── narrow ──
    "is_narrow", "get_terminal_width",
    "narrow_truncate", "narrow_indent", "narrow_sep_width",
    # ── message_editor ──
    "MessageEditor", "edit_current_messages", "display_messages",
    # ── status_bar ──
    "StatusBar",
    # ── command_palette ──
    "CommandPalette",
    # ── session_switcher ──
    "SessionSwitcher",
    # ── 状态 ──
    "TUIStateTree", "UISessionState", "InputState",
    # ── ports ──
    "ILockedTerminal",
    # ── selector_base ──
    "BaseBottomBarSelector",
    # ── text_utils ──
    "truncate",
]
