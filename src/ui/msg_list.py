# DEPRECATED: 此文件仅为向后兼容保留，请直接导入 src.tui.pipeline
"""交互式会话消息编辑器 — 兼容包装层

实现在 `src/tui/pipeline/message_editor` 子模块中。
此文件保持向后兼容，所有符号从新框架重新导出。

新代码应直接导入：
    from src.tui.pipeline import edit_current_messages, display_messages
"""

from src.tui.pipeline import (  # noqa: F401
    edit_current_messages,
    display_messages,
)

