"""助手回答块 — AnswerBlock。

流式 Markdown 渲染，管理内容流状态转换。
"""

from __future__ import annotations

from ..animation.transitions import FadeIn
from ..consumer.render_state import _ReasoningState
from ..framework import Framework
from ..terminal.terminal import is_narrow
from ._base import TuiComponent, _estimate_content_lines


class AnswerBlock(TuiComponent):
    """助手回答块 — 流式 Markdown 渲染。

    入场动效（2026-07-15）：
      - 首次 write() 触发 FadeIn 渐显效果
      - 后续 chunk 不做过渡（避免与流式渲染冲突）
    """
    def __init__(self, rs: "_RenderState"):
        self._rs = rs
        self._first_write: bool = True

    def write(self, text: str) -> int:
        """写入内容，返回估计行数。"""
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        content = self._rs.get_content()
        # 首次写入：集成 FadeIn 入场动效
        # 【技术债】此 FadeIn 首次写入逻辑与 ThinkingBlock.write() 中的
        # FadeIn 入场逻辑重复（FadeIn(smooth, 6f, 240→253) + fade_prefix 包裹）。
        # 后续可提取为公共 mixin 或工具函数（如 _apply_fade_in_first_write）。
        if self._first_write:
            self._first_write = False
            if not is_narrow():
                frame = Framework.get_default().get_animator().frame
                fade = FadeIn(easing="smooth", total_frames=6, start_color=240, end_color=253)
                fade_prefix = fade.render(frame)
                if fade_prefix:
                    text = f"{fade_prefix}{text}\033[0m"
        content.write(text)
        return _estimate_content_lines(text)

    def close(self) -> None:
        self._rs.close_content()

    def render(self) -> str:
        return ""
