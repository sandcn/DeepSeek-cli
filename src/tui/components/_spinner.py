"""独立转轮控件 — Spinner。

提供预定义多帧集的旋转动画转轮，支持多种样式（dots/line/arrow/bounce/clock），
可设置颜色、当前帧号和附加文本。

动效增强（2026-07-16）：
  - 呼吸渐变：使用 sine_color 使 spinner 颜色随帧号呼吸渐变
  - 多帧集：支持 dots/bars/arrow/bounce/clock 五种样式
  - 窄屏降级：窄屏去掉颜色，保留帧字符

设计模式: 状态 (State) — style 参数选择不同帧集状态，
Spinner 在不同帧集间切换。
"""

from __future__ import annotations

from tui_framework.terminal.narrow import is_narrow
from ..core.style import Style
from ..core.effects import sine_color
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
    "bars": [
        "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█",
        "▇", "▆", "▅", "▄", "▃", "▂", "▁",
    ],
}

# ═══════════════════════════════════════════════════════════
# Spinner 组件
# ═══════════════════════════════════════════════════════════


class Spinner(TuiComponent):
    """独立转轮控件 — 旋转动画指示器。

    根据 style 参数选择预定义帧集，结合 frame 索引推进动画，
    可选附加文本显示在转轮字符之后。
    颜色使用 sine_color 呼吸渐变，产生柔和脉动效果。

    Attributes:
        style: 帧集名称（"dots"/"line"/"arrow"/"bounce"/"clock"/"bars"）。
        color: 转轮字符的基准 256 色号，默认 45（亮青色）。
        color_high: 呼吸渐变的最高色号，默认 color+25。
        frame: 当前帧号（单调递增），用于推进动画。
        text: 附加文本（可选），显示在转轮字符之后。
        breath_period: 呼吸渐变周期（帧数），默认 12。
    """

    def __init__(
        self,
        style: str = "dots",
        color: int = 45,
        frame: int = 0,
        text: str = "",
        color_high: int | None = None,
        breath_period: int = 12,
    ) -> None:
        self.style = style
        self.color = color
        self.frame = frame
        self.text = text
        self._color_high = color_high if color_high is not None else min(255, color + 25)
        self._breath_period = breath_period

    def render(self) -> str:
        """渲染当前帧的转轮字符及附加文本。

        宽屏：使用 sine_color 呼吸渐变颜色 + 帧字符。
        窄屏降级：移除 ANSI 颜色，仅返回帧字符 + 文本。
        未知 style 兜底：返回 "?" 标记。

        Returns:
            str: 渲染后的文本（含 ANSI 颜色序列或纯文本）。
        """
        frames = _SPINNER_FRAMES.get(self.style)
        if not frames:
            frame_char = "?"
        else:
            frame_char = frames[self.frame % len(frames)]

        if is_narrow():
            # 窄屏：纯文本，不加颜色
            if self.text:
                return f"{frame_char} {self.text}"
            return frame_char

        # 宽屏：sine_color 呼吸渐变颜色
        breath_color = sine_color(
            self.frame, self.color, self._color_high, self._breath_period,
        )
        styled_char = Style(fg=breath_color).apply(frame_char)
        if self.text:
            return f"{styled_char} {self.text}"
        return styled_char
