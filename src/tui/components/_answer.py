"""助手回答块 — AnswerBlock（继承 StreamingBlock 基类）。

流式 Markdown 渲染，管理内容流状态转换。

2026-07-24 增强：render(buffer) 优先输出 IncrementalRenderer 捕获的
渲染后 ANSI 文本（保留 Markdown 格式/语法高亮），而非原始纯文本。
回退路径：捕获不可用时使用 _cumulative_content 原始文本。
2026-07-26 重构：继承 StreamingBlock 基类，消除与 ThinkingBlock 的重复。
"""

from __future__ import annotations

from ..state.render_state import _ReasoningState
from ._base import StreamingBlock


class AnswerBlock(StreamingBlock):
    """助手回答块 — 流式 Markdown 渲染。

    入场动效（2026-07-15）：
      - 首次 write() 触发 FadeIn 渐显效果
      - 后续 chunk 不做过渡（避免与流式渲染冲突）
    """
    def __init__(self, rs, *, props: dict | None = None) -> None:
        super().__init__(rs, "captured_content_output", props=props)

    # ── 钩子实现 ──────────────────────────────────

    def _build_header(self) -> None:
        """AnswerBlock 无标题。"""
        return None

    def _get_renderer(self):
        return self._rs.get_content()

    def _on_first_write(self) -> None:
        """首次写入时关闭推理（如推理仍活跃）。"""
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()

    def _close_renderer(self) -> None:
        self._rs.close_content()

    def close(self) -> None:
        self._rs.close_content()
