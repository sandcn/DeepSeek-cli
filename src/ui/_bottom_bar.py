"""底部栏 _BottomBar — 向后兼容导入存根

实现在 tui/bottom_bar/ 子包中。
保持旧导入路径 from src.ui._bottom_bar import _BottomBar 仍有效。
"""
from __future__ import annotations

from .tui.bottom_bar import _BottomBar
from .tui.bottom_bar.bar import _BottomBar as _BottomBar_
from .tui.bottom_bar.status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT
from .tui.bottom_bar.completion import _CompletionPopup
from .tui.bottom_bar.selection import run_bottom_bar_selection

__all__ = [
    "_BottomBar",
    "_StatusMixin",
    "_get_snapshot",
    "_TOKEN_SPEED_SNAPSHOT",
    "_CompletionPopup",
    "run_bottom_bar_selection",
]
