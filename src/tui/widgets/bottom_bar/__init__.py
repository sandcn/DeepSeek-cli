"""底部栏子包 — _BottomBar 主类 + 状态行/补全弹窗/选择器/绘图

使用方法：
    from src.tui.widgets.bottom_bar import _BottomBar
    from src.tui.widgets.bottom_bar.bar import _BottomBar
"""
from __future__ import annotations

# 向后兼容：确保旧导入路径有效
from .bar import _BottomBar
from .status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT
from .selection import run_bottom_bar_selection
from .completion import _CompletionPopup


def __getattr__(name: str):
    """延迟导入 StatusBarWidget 以避免循环依赖。

    status_bar_widget.py 导入 bottom_bar.theme，而此模块被
    status_bar_widget 导入，形成循环。通过 __getattr__ 延迟解析。
    """
    if name == "StatusBarWidget":
        from ..status_bar_widget import StatusBarWidget as _sbw
        globals()["StatusBarWidget"] = _sbw
        return _sbw
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)

__all__ = [
    "_BottomBar",
    "_StatusMixin",
    "StatusBarWidget",
    "_get_snapshot",
    "_TOKEN_SPEED_SNAPSHOT",
    "run_bottom_bar_selection",
    "_CompletionPopup",
]
