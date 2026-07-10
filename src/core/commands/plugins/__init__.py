"""交互式命令插件包 — 需要 InteractiveLoop 内部状态的命令插件

包含：
- InteractiveCommandPlugin 基类（base.py）
- EditmsgPlugin（editmsg_plugin.py）
- ModelPlugin（model_plugin.py）
- LoopPlugin（loop_plugin.py）
- InteractiveCommandRegistry（registry.py）
"""

from .base import InteractiveCommandPlugin
from .editmsg_plugin import EditmsgPlugin
from .model_plugin import ModelPlugin
from .loop_plugin import LoopPlugin
from .registry import InteractiveCommandRegistry, get_interactive_registry

__all__ = [
    "InteractiveCommandPlugin",
    "EditmsgPlugin",
    "ModelPlugin",
    "LoopPlugin",
    "InteractiveCommandRegistry",
    "get_interactive_registry",
]
