"""TUI 统一状态容器 — TUIStateTree。"""

from __future__ import annotations

import dataclasses

from .session_state import UISessionState
from .input_state import InputState
from .streaming_state import StreamingState


class TUIStateTree:
    """TUI 统一状态容器。

    聚合子状态，提供单一入口访问全部 TUI 运行时数据。

    用法：
        tree = TUIStateTree()
        tree.session = dataclasses.replace(tree.session, model="gpt-4")
        tree.streaming.start()
    """

    __slots__ = ("_session", "_input", "_streaming")

    def __init__(self) -> None:
        self._session: UISessionState = UISessionState()
        self._input: InputState = InputState()
        self._streaming: StreamingState = StreamingState()

    @property
    def session(self) -> UISessionState:
        return self._session

    @property
    def input(self) -> InputState:
        return self._input

    @property
    def streaming(self) -> StreamingState:
        return self._streaming

    def update_session(self, **kwargs) -> None:
        """批量更新会话字段（使用 dataclasses.replace 创建新快照）。"""
        self._session = dataclasses.replace(self._session, **kwargs)


__all__ = ["TUIStateTree"]
