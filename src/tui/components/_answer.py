"""助手回答块 — AnswerBlock。

流式 Markdown 渲染，管理内容流状态转换。

2026-07-24 增强：render(buffer) 优先输出 IncrementalRenderer 捕获的
渲染后 ANSI 文本（保留 Markdown 格式/语法高亮），而非原始纯文本。
回退路径：捕获不可用时使用 _cumulative_content 原始文本。
"""

from __future__ import annotations

from ..animation.transitions import FadeIn
from ..state.render_state import _ReasoningState
from ..framework import get_animator
from ..render_buffer import RenderBuffer
from ..terminal.terminal import is_narrow
from ._base import StreamingBlock, _estimate_content_lines, apply_fade_in


class AnswerBlock(StreamingBlock):
    """助手回答块 — 流式 Markdown 渲染。

    入场动效（2026-07-15）：
      - 首次 write() 触发 FadeIn 渐显效果
      - 后续 chunk 不做过渡（避免与流式渲染冲突）
    """
    def __init__(self, rs: "ChatRenderState", *, props: dict | None = None) -> None:
        super().__init__(rs, props=props)
        self._first_write: bool = True

    @property
    def _captured_attr(self) -> str:
        return "captured_content_output"

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


