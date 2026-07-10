"""思考/推理内容块 — ThinkingBlock。

流式追加写入 IncrementalRenderer，管理推理状态转换。
"""

from __future__ import annotations

from ..const import _THINKING_HEADER
from ..render_state import _ReasoningState
from ._base import TuiComponent, _estimate_content_lines


class ThinkingBlock(TuiComponent):
    """思考/推理内容块 — 流式追加写入 IncrementalRenderer。"""
    def __init__(self, rs: "_RenderState"):
        self._rs = rs

    def write(self, text: str) -> int:
        """写入推理内容，返回估计行数。"""
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is None:
            return 0
        lines = 0
        if is_first:
            rr.write(_THINKING_HEADER)
            lines += _estimate_content_lines(_THINKING_HEADER)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    def render(self) -> str:
        return ""
