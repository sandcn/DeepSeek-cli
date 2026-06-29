"""助手回答块 — AnswerBlock。

流式 Markdown 渲染，管理内容流状态转换。
"""

from __future__ import annotations

from .._render_state import _ReasoningState
from ._base import TuiComponent, _estimate_content_lines


class AnswerBlock(TuiComponent):
    """助手回答块 — 流式 Markdown 渲染。"""
    def __init__(self, rs: "_RenderState"):
        self._rs = rs

    def write(self, text: str) -> int:
        """写入内容，返回估计行数。"""
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)
        return _estimate_content_lines(text)

    def close(self) -> None:
        self._rs.close_content()

    def render(self) -> str:
        return ""
