"""app — 应用组件与模型（AppModel + apply_cmd + 组件树）。

RenderCmd → AppModel（apply.py）→ 组件树（app.py）→ 帧渲染。
"""

from __future__ import annotations

from .model import (
    AppModel,
    ChatBlock,
    CompletionState,
    StatusState,
    UserSelectState,
    ReasoningState,
)
from .apply import apply_cmd
from .app import build_app_element, App

__all__ = [
    "AppModel",
    "ChatBlock",
    "CompletionState",
    "StatusState",
    "UserSelectState",
    "ReasoningState",
    "apply_cmd",
    "build_app_element",
    "App",
]
