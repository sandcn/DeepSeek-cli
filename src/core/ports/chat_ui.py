"""ChatUI 端口接口 — core 层与 chat_ui 层的依赖倒置抽象。

通过此端口，core 层无需直接 import chat_ui 模块即可：
- 检查 ChatUI 是否活跃
- 暂停/恢复终端独占控制
- 写入文本到 ChatUI 显示区
- 获取底部栏引用
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ChatUIPort",
    "NullChatUIPort",
    "DefaultChatUIPort",
    "get_default_chat_ui_port",
    "set_default_chat_ui_port",
    "reset_default_chat_ui_port",
]


@runtime_checkable
class ChatUIPort(Protocol):
    """ChatUI 依赖倒置端口。

    所有 core 层需要与 ChatUI 交互的方法均通过此接口，
    消除 core → chat_ui 的直接 import 依赖。
    """

    def is_active(self) -> bool:
        """返回 ChatUI 当前是否处于活跃状态。"""
        ...

    def suspend(self) -> None:
        """暂停 ChatUI 渲染/输入，释放终端给交互式工具独占使用。"""
        ...

    def resume(self) -> None:
        """恢复 ChatUI 渲染/输入（在 suspend() 之后调用）。"""
        ...

    def write_line(self, text: str) -> None:
        """向 ChatUI 显示区写入一行文本。

        Args:
            text: 要写入的文本行（不含换行符，ChatUI 内部处理换行）。
        """
        ...

    def get_bottom_bar(self) -> Any | None:
        """获取底部栏实例引用。

        用于需要直接操作底部栏的场景（如补全弹窗、选择弹窗）。
        返回 None 表示 ChatUI 未激活或无底部栏。
        """
        ...


class NullChatUIPort(ChatUIPort):
    """空实现 — 所有操作为空操作，用于 ChatUI 不存在时的降级。"""

    def is_active(self) -> bool:
        return False

    def suspend(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def write_line(self, text: str) -> None:
        pass

    def get_bottom_bar(self) -> Any | None:
        return None


class DefaultChatUIPort(ChatUIPort):
    """默认实现 — 延迟导入 chat_ui 模块并委托给 get_active_chat_ui()。

    此实现位于 core 层但通过延迟导入避免循环依赖。
    运行时需先调用 set_default_chat_ui_port() 注册实际实现。
    """

    def __init__(self) -> None:
        self._port: ChatUIPort = NullChatUIPort()

    def set_port(self, port: ChatUIPort) -> None:
        """注入实际的 ChatUIPort 实现（由 chat_ui 层在启动时调用）。"""
        self._port = port

    def is_active(self) -> bool:
        return self._port.is_active()

    def suspend(self) -> None:
        self._port.suspend()

    def resume(self) -> None:
        self._port.resume()

    def write_line(self, text: str) -> None:
        self._port.write_line(text)

    def get_bottom_bar(self) -> Any | None:
        return self._port.get_bottom_bar()


# 线程安全的全局默认端口
_default_port: ChatUIPort = NullChatUIPort()
_default_lock = threading.Lock()


def get_default_chat_ui_port() -> ChatUIPort:
    """获取全局默认 ChatUIPort。

    在 ChatUI 未启动时返回 NullChatUIPort，
    ChatUI 启动后通过 set_default_chat_ui_port() 注入实际实现。
    """
    with _default_lock:
        return _default_port


def set_default_chat_ui_port(port: ChatUIPort) -> None:
    """设置全局默认 ChatUIPort（由 chat_ui 层在启动时调用）。"""
    global _default_port
    with _default_lock:
        _default_port = port


def reset_default_chat_ui_port() -> None:
    """重置为 NullChatUIPort（由 chat_ui 层在停止时调用）。"""
    global _default_port
    with _default_lock:
        _default_port = NullChatUIPort()
