"""会话级状态 — UISessionState 不可变值对象。"""

from __future__ import annotations

import dataclasses
from src._compat import dataclass


@dataclass(frozen=True, slots=True)
class UISessionState:
    """会话级数据（不可变值对象）。

    修改时使用 dataclasses.replace() 创建新快照。
    """
    model: str = ""
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status_text: str = ""
    session_title: str = ""
    session_duration: float = 0.0
    show_time: bool = True
    show_tokens: bool = True
    show_duration: bool = False


__all__ = ["UISessionState"]
