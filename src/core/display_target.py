"""显示目标协议 — 核心层到显示层的桥接接口。

提供 DisplayTarget 协议和 get_display_target() 工厂函数，
消除核心层对 TUI 内部模块（src.tui.consumer）的直接依赖。

设计原则：
  - 依赖倒置：core 层定义协议，tui 层实现协议
  - 零 I/O：纯协议定义，不涉及终端操作
  - 延迟导入：get_display_target() 在首次调用时才导入 TUI 模块
  - 无头模式安全：无显示目标时返回 None，调用方自行降级

用法::

    from src.core.display_target import get_display_target
    target = get_display_target()
    if target is not None:
        target.write_line("Hello World")
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__: list[str] = [
    "DisplayTarget",
    "get_display_target",
]


@runtime_checkable
class DisplayTarget(Protocol):
    """核心层可见的显示目标协议。

    ChatUIConsumer 隐式满足此协议（实现了 write_line / on_user_message /
    on_notification / on_error）。不要求显式继承，仅依赖结构化子类型。
    """

    def write_line(self, text: str) -> None:
        """向显示目标写入一行文本。"""
        ...

    def on_user_message(self, text: str) -> None:
        """通知显示了用户消息。"""
        ...

    def on_notification(self, text: str) -> None:
        """显示通知消息。"""
        ...

    def on_error(self, message: str) -> None:
        """显示错误消息。"""
        ...


def get_display_target() -> DisplayTarget | None:
    """返回当前活跃的显示目标。

    在 TUI 模式返回 ChatUIConsumer 实例，无头模式返回 None。

    Returns:
        DisplayTarget 实例，无活跃显示目标时返回 None。
    """
    # 延迟导入避免循环依赖
    from src.tui.consumer import get_active_chat_ui
    return get_active_chat_ui()
