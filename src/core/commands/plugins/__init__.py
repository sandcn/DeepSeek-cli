"""交互式命令插件包 — 需要 InteractiveLoop 内部状态的命令插件

包含：
- InteractiveCommandPlugin 基类（base.py）
- EditmsgPlugin（editmsg_plugin.py）
- ModelPlugin（model_plugin.py）
- LoopPlugin（loop_plugin.py）
- ToolcardPlugin（toolcard_plugin.py）— /toolcard 折叠/展开工具卡片

InteractiveCommandRegistry 已合并到 CommandPluginRegistry（commands/base.py），
registry.py 保留 get_interactive_registry() 作为向后兼容导出。
"""

from .base import InteractiveCommandPlugin
from .deitmsg_plugin import DeitmsgPlugin
from .editmsg_plugin import EditmsgPlugin
from .model_plugin import ModelPlugin
from .loop_plugin import LoopPlugin
from .toolcard_plugin import ToolcardPlugin
from .registry import get_interactive_registry

__all__ = [
    "InteractiveCommandPlugin",
    "DeitmsgPlugin",
    "EditmsgPlugin",
    "ModelPlugin",
    "LoopPlugin",
    "ToolcardPlugin",
    "get_interactive_registry",
]
