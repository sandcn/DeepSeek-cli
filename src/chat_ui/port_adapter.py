"""ChatUIPort 的 chat_ui 层实现 — 将 ChatUIConsumer 适配为 ChatUIPort 协议。

此模块在 ChatUIConsumer.start() 时实例化并注册到全局默认端口，
使 core 层可以通过 ChatUIPort 接口与 ChatUI 交互而无需直接导入 chat_ui 模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.ports.chat_ui import ChatUIPort, set_default_chat_ui_port, reset_default_chat_ui_port

if TYPE_CHECKING:
    from .consumer import ChatUIConsumer

__all__ = ["ChatUIPortAdapter", "register_chat_ui_port", "unregister_chat_ui_port"]


class ChatUIPortAdapter(ChatUIPort):
    """将 ChatUIConsumer 适配为 ChatUIPort 协议。

    每个 ChatUIConsumer 实例对应一个适配器，
    通过 start() 时注册到全局默认端口。
    """

    def __init__(self, consumer: ChatUIConsumer) -> None:
        self._consumer = consumer

    def is_active(self) -> bool:
        return self._consumer is not None and self._consumer._lifecycle.started

    def suspend(self) -> None:
        if self._consumer:
            self._consumer.suspend()

    def resume(self) -> None:
        if self._consumer:
            self._consumer.resume()

    def write_line(self, text: str) -> None:
        if self._consumer:
            self._consumer.write_line(text)

    def get_bottom_bar(self) -> Any | None:
        if self._consumer and hasattr(self._consumer, 'bottom_bar'):
            return self._consumer.bottom_bar
        return None

    def get_panel_context(self) -> Any | None:
        """返回 ChatUIConsumer 自身作为 PanelContext（满足 PanelContext Protocol）。"""
        return self._consumer

    def flush(self, timeout: float | None = 5.0) -> None:
        """排空命令队列，委托给 ChatUIConsumer.flush()。"""
        if self._consumer:
            self._consumer.flush(timeout=timeout)

    def display_messages(self, data: list[dict], agent: Any = None, idx_map=None, speed: int = 0) -> None:
        """委托给 chat_ui 的 message_display 模块。"""
        from .infrastructure.message_display import _display_messages
        _display_messages(data, agent=agent, idx_map=idx_map, speed=speed)

    def edit_current_messages(self, agent: Any, state: dict, bottom_bar: Any = None) -> bool:
        """委托给 MessageEditor。"""
        from .components.message_editor import MessageEditor
        return MessageEditor().edit_current_messages(agent, state, bottom_bar=bottom_bar)


def register_chat_ui_port(consumer: ChatUIConsumer) -> None:
    """注册 ChatUIConsumer 到全局默认 ChatUIPort。

    在 ChatUIConsumer.start() 中调用。
    """
    adapter = ChatUIPortAdapter(consumer)
    set_default_chat_ui_port(adapter)


def unregister_chat_ui_port() -> None:
    """从全局默认 ChatUIPort 注销。

    在 ChatUIConsumer.stop() 中调用。
    """
    reset_default_chat_ui_port()
