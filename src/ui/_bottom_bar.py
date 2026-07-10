"""底部栏 _BottomBar — 向后兼容导入存根

实现在 _bottom_bar_pkg/ 子包中。
保持旧导入路径 from src.ui._bottom_bar import _BottomBar 仍有效。
"""
from __future__ import annotations

from ._bottom_bar_pkg import _BottomBar
from ._bottom_bar_pkg.bar import _BottomBar as _BottomBar_
from ._bottom_bar_pkg.status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT
from ._bottom_bar_pkg.completion import _CompletionPopup
from ._bottom_bar_pkg.selection import run_bottom_bar_selection

__all__ = [
    "_BottomBar",
    "_StatusMixin",
    "_get_snapshot",
    "_TOKEN_SPEED_SNAPSHOT",
    "_CompletionPopup",
    "run_bottom_bar_selection",
]
