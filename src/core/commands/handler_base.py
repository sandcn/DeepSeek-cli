"""命令分发框架 — CommandHandler 抽象基类

为 InteractiveLoop._handle_command_msg 的 if/elif 命令分发链
提供统一的协议抽象，遵循开闭原则（对扩展开放，对修改关闭）。

每个命令对应一个 Handler 实例，通过注册表统一分发。
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...app_loop import SessionState
    from ...core.session import ChatSession


class CommandHandler(abc.ABC):
    """命令处理器协议——每个命令对应一个 handler 实例。

    继承此类实现具体命令的处理逻辑，通过 _register_default_handlers()
    注册到 InteractiveLoop._command_handlers 字典中。

    使用方式:
        class MyHandler(CommandHandler):
            async def handle(self, content, session, state, loop, chat_ui, monitor):
                # 处理逻辑
                return True
    """

    @abc.abstractmethod
    async def handle(
        self,
        content: str,
        session: ChatSession,
        state: SessionState,
        loop: Any,  # InteractiveLoop 实例
        chat_ui: Any,
        monitor: Any,
    ) -> bool:
        """处理命令。

        Args:
            content: 完整命令字符串（含命令名和参数）
            session: 当前会话
            state: 会话状态
            loop: InteractiveLoop 实例
            chat_ui: ChatUIConsumer 实例
            monitor: EscapeMonitor 实例

        Returns:
            True 表示命令已处理，False 表示命令未识别（回退通用路径）
        """
        ...
