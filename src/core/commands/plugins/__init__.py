"""交互式命令插件包 — 需要 InteractiveLoop 内部状态的命令插件

包含：
- InteractiveCommandPlugin 基类（base.py）
- EditmsgPlugin（editmsg_plugin.py）
- ModelPlugin（model_plugin.py）
- LoopPlugin（loop_plugin.py）
- PetPlugin（pet_plugin.py）

InteractiveCommandRegistry 已合并到 CommandPluginRegistry（commands/base.py），
registry.py 保留 get_interactive_registry() 作为向后兼容导出。
"""

from .base import InteractiveCommandPlugin
from .deitmsg_plugin import DeitmsgPlugin
from .editmsg_plugin import EditmsgPlugin
from .loop_plugin import LoopPlugin
from .model_plugin import ModelPlugin
from .models_plugin import ModelsPlugin
from .pet_plugin import PetPlugin
from .registry import get_interactive_registry
from .skill_plugin import SkillPlugin

__all__ = [
    "InteractiveCommandPlugin",
    "DeitmsgPlugin",
    "EditmsgPlugin",
    "ModelPlugin",
    "ModelsPlugin",
    "LoopPlugin",
    "PetPlugin",
    "SkillPlugin",
    "get_interactive_registry",
]
