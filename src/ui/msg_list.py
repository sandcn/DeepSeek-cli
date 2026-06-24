# DEPRECATED: 此文件仅为向后兼容保留，请直接导入 src.ui.tui.message_editor
"""交互式会话消息编辑器 — 兼容包装层

实现在 `src/ui/tui/message_editor` 子模块中。
此文件保持向后兼容，所有符号从 tui 子模块重新导出。

新代码应直接导入：
    from src.ui.tui.message_editor import MessageEditor
    from src.ui.tui._message_display import display_messages
"""

from src.chat_ui.tui.message_editor import MessageEditor  # noqa: F401
from src.chat_ui.tui._message_display import display_messages  # noqa: F401

edit_current_messages = lambda agent, state: MessageEditor().edit_current_messages(agent, state)

