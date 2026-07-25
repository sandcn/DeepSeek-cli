"""思考/推理内容块 — ThinkingBlock（继承 StreamingBlock 基类）。

流式追加写入 IncrementalRenderer，管理推理状态转换。
动效：宽屏时首次写入 sparkle⚡ 闪烁 + 呼吸色渐变标题；
窄屏时降级为静态 ChatConfig.thinking_header。

2026-07-15 重构：使用 Color256/Style 替代 raw ANSI。
2026-07-24 增强：render(buffer) 优先输出 IncrementalRenderer 捕获的
渲染后 ANSI 文本（保留 Markdown 格式/语法高亮），而非原始纯文本。
回退路径：捕获不可用时使用 _cumulative_content 原始文本。
2026-07-26 重构：继承 StreamingBlock 基类，消除与 AnswerBlock 的重复。
"""

from __future__ import annotations

from rich.text import Text

from ..consumer.chat_config import ChatConfig
from ..state.render_state import _ReasoningState
from ..animation.animator import AnimatorContext, BreathPalette
from ..core.style import Style
from ..core.effects import sparkle_color
from ..terminal.terminal import is_narrow
from ._base import StreamingBlock


class ThinkingBlock(StreamingBlock):
    """思考/推理内容块 — 流式追加写入 IncrementalRenderer。"""
    def __init__(self, rs, *, props: dict | None = None) -> None:
        super().__init__(rs, "captured_reasoning_output", props=props)

    # ── 钩子实现 ──────────────────────────────────

    def _build_header(self) -> str | Text | None:
        """构建标题：窄屏用 ChatConfig.thinking_header，宽屏用 sparkle⚡ 呼吸色标题。

        窄屏分支保持简单条件判断（因 _build_header() 是钩子函数，非 render() 路径，
        不强制适配 render_with_narrow_fallback 模板方法）。
        """
        if is_narrow():
            return self._render_narrow_header()
        frame = AnimatorContext.get_default().frame
        c = sparkle_color(frame, 45, period=6)
        sparkle = f"\033[38;5;{c}m"
        think_color = BreathPalette.get_sine_color("think", frame)
        think_style = Style(fg=think_color)
        return f"\n  {'─' * 4} {sparkle}⚡{think_style.apply('思考')} {'─' * 4}\n"

    def _render_narrow_header(self) -> str:
        """窄屏标题 — 返回静态配置的 thinking_header（无动效）。"""
        return ChatConfig.defaults().thinking_header

    def _get_renderer(self):
        return self._rs.get_reasoning()

    def _on_first_write(self) -> None:
        """首次写入时确保推理状态已打开。"""
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()

    def _close_renderer(self) -> None:
        self._rs.close_reasoning()

    def close(self) -> None:
        self._rs.close_reasoning()

    def _is_first_write(self) -> bool:
        """根据推理状态判断是否为首次写入。"""
        return self._rs.reasoning_state in (_ReasoningState.INACTIVE, _ReasoningState.CLOSED)
