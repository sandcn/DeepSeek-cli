"""消息渲染管线 — 消息显示、编辑。

提供 display_messages（消息列表全量显示）、MessageEditor（交互式消息编辑器）。
"""

from .message_display import display_messages, MessageDisplayContext, RoleConfig
from .message_editor import MessageEditor, edit_current_messages

__all__ = [
    "display_messages",
    "MessageDisplayContext",
    "RoleConfig",
    "MessageEditor",
    "edit_current_messages",
]
