"""底部栏子包 — _BottomBar 主类 + 状态行/补全弹窗/选择器/绘图

使用方法：
    from src.ui.tui.bottom_bar import _BottomBar
    from src.ui.tui.bottom_bar.bar import _BottomBar
"""
from __future__ import annotations

# 向后兼容：确保旧导入路径有效
from .bar import _BottomBar
from .status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT
from .selection import run_bottom_bar_selection
from .completion import _CompletionPopup

__all__ = [
    "_BottomBar",
    "_StatusMixin",
    "_get_snapshot",
    "_TOKEN_SPEED_SNAPSHOT",
    "run_bottom_bar_selection",
    "_CompletionPopup",
]
