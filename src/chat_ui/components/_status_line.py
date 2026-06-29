"""状态行 — StatusLine。

模型名 · tokens · 时间 · 工具计数。
由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
"""

from __future__ import annotations

from ._base import TuiComponent


class StatusLine(TuiComponent):
    """状态行 — 模型名 · tokens · 时间 · 工具计数。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.model: str = ""
        self.tokens: int = 0
        self.elapsed: float = 0.0
        self.tool_count: int = 0
        self.tool_fail: int = 0
        self.streaming: bool = False

    def render(self) -> str:
        """渲染为单行状态文本。"""
        parts = []
        if self.model:
            parts.append(self.model)
        if self.tokens:
            parts.append(f"{self.tokens}t")
        if self.elapsed:
            parts.append(f"{self.elapsed:.1f}s")
        if self.tool_count:
            s = f"⚙{self.tool_count}"
            if self.tool_fail:
                s += f"!{self.tool_fail}"
            parts.append(s)
        return " · ".join(parts) if parts else ""
