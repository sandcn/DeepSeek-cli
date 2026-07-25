"""独立转轮控件 — Spinner。

提供预定义多帧集的旋转动画转轮，支持多种样式（dots/line/arrow/bounce/clock），
可设置颜色、当前帧号和附加文本。

设计模式: 状态 (State) — style 参数选择不同帧集状态，
Spinner 在不同帧集间切换。
"""

from __future__ import annotations

from ..render_buffer import RenderBuffer
from ..core.style import Style
from ._base import TuiComponent

__all__ = [
    "Spinner",
]

# ═══════════════════════════════════════════════════════════
# 预定义多帧集
# ═══════════════════════════════════════════════════════════

_SPINNER_FRAMES: dict[str, list[str]] = {
    "dots": [
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
    ],
    "line": [
        "─", "╱", "╲", "│", "╱", "╲",
    ],
    "arrow": [
        "←", "↖", "↑", "↗", "→", "↘", "↓", "↙",
    ],
    "bounce": [
        "⠁", "⠃", "⠇", "⠧", "⠿", "⠧", "⠇", "⠃",
    ],
    "clock": [
        "🕐", "🕑", "🕒", "🕓", "🕔", "🕕",
        "🕖", "🕗", "🕘", "🕙", "🕚", "🕛",
    ],
}

# ═══════════════════════════════════════════════════════════
# Spinner 组件
# ═══════════════════════════════════════════════════════════

# _RESET 已移除 — 使用 Style.apply() 自动管理 RESET


class Spinner(TuiComponent):
    """独立转轮控件 — 旋转动画指示器。

    根据 style 参数选择预定义帧集，结合 frame 索引推进动画，
    可选附加文本显示在转轮字符之后。

    Attributes:
        style: 帧集名称（"dots"/"line"/"arrow"/"bounce"/"clock"）。
        color: 转轮字符的 256 色号，默认 45（亮青色）。
        frame: 当前帧号（单调递增），用于推进动画。
        text: 附加文本（可选），显示在转轮字符之后。
    """

    def __init__(
        self,
        style: str = "dots",
        color: int = 45,
        frame: int = 0,
        text: str = "",
        *,
        props: dict | None = None,
    ) -> None:
        super().__init__(props=props)
        self.style = style
        self.color = color
        self.frame = frame
        self.text = text

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染当前帧的转轮字符及附加文本。

        窄屏降级：移除 ANSI 颜色，仅返回帧字符 + 文本。
        未知 style 兜底：返回 "?" 标记。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时直接写入 buffer。

        Returns:
            str | None: 无 buffer 时返回渲染字符串；有 buffer 时返回 None。
        """
        result = self.render_with_narrow_fallback(buffer, narrow_method=self._render_narrow)
        if result is not None:
            return result

        # 宽屏：带颜色的转轮字符
        result = self._build_spinner()
        return self._finalize_render(result, buffer)

    def _render_narrow(self) -> str:
        """窄屏降级：纯文本转轮字符，不加颜色。"""
        frames = _SPINNER_FRAMES.get(self.style)
        if not frames:
            frame_char = "?"
        else:
            frame_char = frames[self.frame % len(frames)]

        if self.text:
            return f"{frame_char} {self.text}"
        return frame_char

    def _build_spinner(self) -> str:
        """构建转轮字符串（核心渲染逻辑 — 宽屏路径）。"""
        frames = _SPINNER_FRAMES.get(self.style)
        if not frames:
            frame_char = "?"
        else:
            frame_char = frames[self.frame % len(frames)]

        # 宽屏：带颜色的转轮字符（使用 Style）
        styled_char = Style(fg=self.color).apply(frame_char)
        if self.text:
            return f"{styled_char} {self.text}"
        return styled_char
