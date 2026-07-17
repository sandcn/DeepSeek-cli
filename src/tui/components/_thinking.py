"""思考/推理内容块 — ThinkingBlock。

流式追加写入 IncrementalRenderer，管理推理状态转换。
动效：宽屏时首次写入 sparkle⚡ 闪烁 + 呼吸色渐变标题；
窄屏时降级为静态 _THINKING_HEADER。

2026-07-15 重构：使用 Color256/Style 替代 raw ANSI。
"""

from __future__ import annotations

from ..animation.transitions import FadeIn
from ..engine.const import _THINKING_HEADER
from ..state.render_state import _ReasoningState
from ..animation.animator import AnimatorContext, BreathPalette
from ..core.style import Style
from ..terminal.terminal import is_narrow
from ..core.text_utils import build_sparkle_ansi
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
                # 使用 Style.apply 构建「思考」标签（替代 raw ANSI）
                think_style = Style(fg=think_color)
                header = f"\n  {'─' * 4} {sparkle}⚡{think_style.apply('思考')} {'─' * 4}\n"
                rr.write(header)
                lines += _estimate_content_lines(header)
        # 首次内容写入：集成 FadeIn 入场动效
        # 【技术债】此 FadeIn 入场逻辑与 AnswerBlock.write() 中的
        # FadeIn 首次写入逻辑重复（FadeIn(smooth, 6f, 240→253) + fade_prefix 包裹）。
        # 后续可提取为公共 mixin 或工具函数（如 _apply_fade_in_first_write）。
        if is_first and not is_narrow():
            frame = AnimatorContext.get_default().frame
            fade = FadeIn(easing="smooth", total_frames=6, start_color=240, end_color=253)
            fade_prefix = fade.render(frame)
            if fade_prefix:
                text = f"{fade_prefix}{text}\033[0m"
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    def render(self) -> str:
        return ""
