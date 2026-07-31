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

    from src.core.display_target import get_output_publisher
    publisher = get_output_publisher()
    if publisher is not None:
        publisher("Hello World", level="info", source="core")
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__: list[str] = [
    "DisplayTarget",
    "get_display_target",
    "OutputPublisher",
    "get_output_publisher",
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


@runtime_checkable
class OutputPublisher(Protocol):
    """核心层可见的输出发布协议。

    publish_output（src.tui.events.consumers.publish_output）隐式满足此协议
    （实现了 ``__call__(text, level, source)`` 签名）。不要求显式继承，
    仅依赖结构化子类型；runtime_checkable 使 publish_output 可被
    isinstance 识别。
    """

    def __call__(self, text: str, level: str = "info", source: str = "") -> None:
        """发布一条输出事件（text 为不带 ANSI 颜色码的纯文本）。

        Args:
            text: 输出文本。
            level: 输出级别（"info"/"success"/"warning"/"error"/"raw"）。
            source: 事件来源标识。
        """
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


def get_output_publisher() -> OutputPublisher | None:
    """返回当前活跃的输出发布函数。

    始终返回 publish_output（发布到 EventBus 供消费者渲染）；
    无头模式（无活跃 ChatUI）下输出经 OutputConsumer 兜底直写终端，
    不再静默丢弃（原链路保留）。

    与 get_display_target() 语义解耦：输出发布不依赖活跃 ChatUI 判定。

    Note:
        行为差异（收敛后）：既有实现 adapters/output.py write() 无条件
        发布 EventBus，无头模式仍产生 EventBus 输出；本函数保持该语义，
        始终返回可调用的 publish_output（无头 None 降级已移除）。
        此语义在 tests/test_core/test_display_target.py 中固化。

    Returns:
        输出发布函数（publish_output）。
    """
    # 延迟导入避免循环依赖（与原 core 侧函数内延迟导入行为一致）
    from src.tui.events import publish_output
    return publish_output
