"""补全数据端口 — 为终端补全引擎提供命令/会话/模型/主题数据。

避免 _completion.py（ui 层）直接反向依赖 core.commands / chat_msgs / config / theme。
"""

from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class CompletionDataPort(Protocol):
    """补全数据源接口。"""

    def get_registered_command_names(self) -> list[str]: ...
    def list_sessions(self) -> list[dict]: ...
    def get_models(self) -> list[str]: ...
    def get_theme_names_with_desc(self) -> list[tuple[str, str]]: ...


class DefaultCompletionDataPort:
    """默认实现：惰性导入各数据源。"""

    def get_registered_command_names(self) -> list[str]:
        from ..core.commands import get_registered_command_names
        return get_registered_command_names()

    def list_sessions(self) -> list[dict]:
        from ..chat_msgs import list_sessions
        return list_sessions()

    def get_models(self) -> list[str]:
        from ..config import MODELS
        return list(MODELS.keys()) if isinstance(MODELS, dict) else []

    def get_theme_names_with_desc(self) -> list[tuple[str, str]]:
        from ..ui.theme import get_theme_names_with_desc
        return get_theme_names_with_desc()
