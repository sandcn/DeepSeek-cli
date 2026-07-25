"""思考/推理内容块 — ThinkingBlock。

流式追加写入 IncrementalRenderer，管理推理状态转换。
动效：宽屏时首次写入 sparkle⚡ 闪烁 + 呼吸色渐变标题；
窄屏时降级为静态 ChatConfig.thinking_header。

2026-07-15 重构：使用 Color256/Style 替代 raw ANSI。
2026-07-24 增强：render(buffer) 优先输出 IncrementalRenderer 捕获的
渲染后 ANSI 文本（保留 Markdown 格式/语法高亮），而非原始纯文本。
回退路径：捕获不可用时使用 _cumulative_content 原始文本。
"""

from __future__ import annotations

from ..animation.transitions import FadeIn
from ..consumer.chat_config import ChatConfig
from ..state.render_state import _ReasoningState
from ..animation.animator import AnimatorContext, BreathPalette
from ..core.style import Style
from ..terminal.terminal import is_narrow
from ..core.text_utils import build_sparkle_ansi
from ..render_buffer import RenderBuffer
from ._base import StreamingBlock, _estimate_content_lines, apply_fade_in


class ThinkingBlock(StreamingBlock):
    """思考/推理内容块 — 流式追加写入 IncrementalRenderer。"""
    def __init__(self, rs, *, props: dict | None = None) -> None:
        super().__init__(rs, props=props)

    @property
    def _captured_attr(self) -> str:
        return "captured_reasoning_output"

    def write(self, text: str) -> int:
        """写入推理内容，返回估计行数。"""
        self._cumulative_content.append(text)
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is None:
            return 0
        lines = 0
        if is_first:
            if is_narrow():
                thinking_header = ChatConfig.defaults().thinking_header
                rr.write(thinking_header)
                lines += _estimate_content_lines(thinking_header)
            else:
                frame = AnimatorContext.get_default().frame
                sparkle = build_sparkle_ansi(frame, 45, 6)
                think_color = BreathPalette.get_sine_color("think", frame)
                # 使用 Style.apply 构建「思考」标签（替代 raw ANSI）
                think_style = Style(fg=think_color)
                header = f"\n  {'─' * 4} {sparkle}⚡{think_style.apply('思考')} {'─' * 4}\n"
                rr.write(header)
                lines += _estimate_content_lines(header)
        # 首次内容写入：集成 FadeIn 入场动效（使用共享的 apply_fade_in 函数）
        if is_first and not is_narrow():
            frame = AnimatorContext.get_default().frame
            text = apply_fade_in(text, frame)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()


