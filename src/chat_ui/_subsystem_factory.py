"""chat_ui 子系统工厂模块 — 封装对 ui 层私有模块的依赖。

所有对 ui._* 私有模块的 import 集中在此文件，
chat_ui 其他模块通过此工厂获取子系统实例，不再直接 import ui._*。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bottom_bar import BottomBar
    from ..bottom_bar import CursorTracker
    from ..ui._completion import CompletionEngine
    from ..ui.events.event_bus import DisplayEventBus


def create_bottom_bar(cursor_tracker: "CursorTracker | None" = None) -> "BottomBar":
    """创建底部栏实例"""
    from .bottom_bar import BottomBar  # noqa: PLC0415
    return BottomBar(cursor_tracker=cursor_tracker)


def create_cursor_tracker() -> "CursorTracker":
    """创建光标追踪器实例（单例模式，全局共享）"""
    from .bottom_bar import CursorTracker  # noqa: PLC0415
    return CursorTracker()


def create_completion_engine() -> "CompletionEngine":
    """创建补全引擎实例"""
    from ..ui._completion import CompletionEngine  # noqa: PLC0415
    return CompletionEngine()


def get_event_bus() -> "DisplayEventBus":
    """获取全局事件总线单例"""
    from ..ui.events.event_bus import DisplayEventBus  # noqa: PLC0415
    return DisplayEventBus()
