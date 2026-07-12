"""思考/推理内容块 — ThinkingBlock。

流式追加写入 IncrementalRenderer，管理推理状态转换。
思考标题使用呼吸色增强。
"""

from __future__ import annotations

from ..const import _THINKING_HEADER
from ..render_state import _ReasoningState
from ..renderer.bridge import make_breath_style
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
            # 思考→回答过渡使用 morph_color 情绪渐变：蓝紫系↔青绿系
            # 推理阶段为蓝紫色，推理结束时渐变到回答阶段的青色
            # 直接构建 ANSI 字符串（IncrementalRenderer.write 仅接受 str）
            from ...ui.tui._effects import morph_color
            from ...ui.tui._animator import AnimatorContext
            _frame = AnimatorContext.get_default().frame
            _morph_c = morph_color(_frame, [24,33,42], [41,82,122], morph_period=60, breath_period=12)
            rr.write(f"\n  \033[1;38;5;{_morph_c}m─ 思考 ─\033[0m\n")
            lines += _estimate_content_lines(_THINKING_HEADER)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    def render(self) -> str:
        return ""
