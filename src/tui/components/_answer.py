"""助手回答块 — AnswerBlock。

流式 Markdown 渲染，管理内容流状态转换。
"""

from __future__ import annotations

from ..animation.transitions import FadeIn
from ..state.render_state import _ReasoningState
from ..framework import get_animator
from ..render_buffer import RenderBuffer
from ..terminal.terminal import is_narrow
from ._base import TuiComponent, _estimate_content_lines, apply_fade_in


class AnswerBlock(TuiComponent):
    """助手回答块 — 流式 Markdown 渲染。

    入场动效（2026-07-15）：
      - 首次 write() 触发 FadeIn 渐显效果
      - 后续 chunk 不做过渡（避免与流式渲染冲突）
    """
    def __init__(self, rs: "_RenderState", *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self._rs = rs
        self._first_write: bool = True
        self._cumulative_content: list[str] = []

    def write(self, text: str) -> int:
        """写入内容，返回估计行数。"""
        self._cumulative_content.append(text)
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        content = self._rs.get_content()
        # 首次写入：集成 FadeIn 入场动效（使用共享的 apply_fade_in 函数）
        if self._first_write:
            self._first_write = False
            if not is_narrow():
                frame = get_animator().frame
                text = apply_fade_in(text, frame)
        content.write(text)
        return _estimate_content_lines(text)

    def close(self) -> None:
        self._rs.close_content()

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染累积的回答内容。

        当传入 buffer 时，将内容写入 buffer 并返回 None；
        未传入 buffer 时以字符串形式返回全部累积内容。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时内容直接写入 buffer。

        Returns:
            str | None: 未传入 buffer 时返回累积文本；传入时返回 None。
        """
        full_content = "".join(self._cumulative_content)
        if buffer is not None:
            if full_content:
                buffer.write(0, 0, full_content)
            return None
        return full_content
