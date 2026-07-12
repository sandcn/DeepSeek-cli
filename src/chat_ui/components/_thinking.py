"""思考/推理内容块 — ThinkingBlock。

流式追加写入 IncrementalRenderer，管理推理状态转换。
动效：宽屏时首次写入 sparkle⚡ 闪烁 + 呼吸色渐变标题；
窄屏时降级为静态 _THINKING_HEADER。
"""

from __future__ import annotations

from ..const import _THINKING_HEADER
from ..render_state import _ReasoningState
from ...ui.tui._animator import AnimatorContext, BreathPalette
from ...ui.tui._terminal import is_narrow
from ...ui.tui._text_utils import build_sparkle_ansi
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
            if is_narrow():
                rr.write(_THINKING_HEADER)
                lines += _estimate_content_lines(_THINKING_HEADER)
            else:
                frame = AnimatorContext.get_default().frame
                sparkle = build_sparkle_ansi(frame, 45, 6)
                think_color = BreathPalette.get_sine_color("think", frame)
                header = f"\n  {'─' * 4} {sparkle}⚡\033[38;5;{think_color}m思考\033[0m {'─' * 4}\n"
                rr.write(header)
                lines += _estimate_content_lines(header)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    def render(self) -> str:
        return ""
