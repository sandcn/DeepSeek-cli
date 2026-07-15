"""装饰分隔线组件 — Separator。

提供静态渐变和多种动效（波动/流光/闪烁/辉光呼吸）风格的分隔线渲染。
动效依赖 frame 参数推进，每帧调用一次 render() 获得不同视觉效果。
继承 TuiComponent 基类，可通过 render_to_adapter() 输出到 OutputAdapter。
"""

from __future__ import annotations

from ._base import TuiComponent


class Separator(TuiComponent):
    """装饰分隔线组件。

    支持五种动效风格：
      - ``"static"``：静态渐变分隔线
      - ``"wave"``：正弦波动效果（如水波沿分隔线流动）
      - ``"shimmer"``：流光扫光效果（亮带沿分隔线方向移动）
      - ``"sparkle"``：闪烁效果（每字符独立闪烁，如星光）
      - ``"glow"``：辉光呼吸效果（整线均匀呼吸脉动）

    Args:
        char: 分隔线字符，默认 ━ (U+2501)。
        width: 分隔线宽度（字符数）。``None`` 时自动根据终端宽度计算。
        style: 动效风格，可选 ``"wave"`` / ``"shimmer"`` / ``"sparkle"`` /
               ``"glow"`` / ``"static"``。
        start_color: 起始 256 色号，默认 45（亮青）。
        end_color: 结束 256 色号，默认 237（深灰）。
        frame: 当前帧号，动效推进用。每帧调用 ``render()`` 时递增。
    """

    def __init__(
        self,
        char: str = "\u2501",
        width: int | None = None,
        style: str = "wave",
        start_color: int = 45,
        end_color: int = 237,
        frame: int = 0,
    ) -> None:
        self._char = char
        self._width = width
        self._style = style
        self._start_color = start_color
        self._end_color = end_color
        self._frame = frame

    # ── 公共 API ────────────────────────────────────────────────────────

    def render(self) -> str:
        """渲染分隔线。

        Returns:
            带 ANSI 颜色和动效的分隔线字符串，空宽度时返回空字符串。
        """
        colors = self._build_colors()
        if not colors:
            return ""

        style = self._style
        if style == "static":
            return self._render_static(colors)
        elif style == "wave":
            return self._render_wave(colors)
        elif style == "shimmer":
            return self._render_shimmer(colors)
        elif style == "sparkle":
            return self._render_sparkle(colors)
        elif style == "glow":
            return self._render_glow(colors)
        else:
            return self._render_static(colors)

    # ── 宽度解析 ────────────────────────────────────────────────────────

    def _resolve_width(self) -> int:
        """计算实际分隔线宽度。

        未指定时根据终端宽度自适应（委托窄屏函数计算）。
        """
        if self._width is not None:
            return max(0, self._width)
        from ..terminal.narrow import narrow_sep_width
        return narrow_sep_width(max_width=40)

    def _build_colors(self) -> list[int]:
        """生成 start_color → end_color 渐变色号列表。

        Returns:
            色号列表（0-255），宽度为 0 时返回空列表。
        """
        width = self._resolve_width()
        if width <= 0:
            return []
        from ...ui.colors import gradient_range
        return gradient_range(self._start_color, self._end_color, width)

    # ── 动效策略 ────────────────────────────────────────────────────────

    def _render_static(self, colors: list[int]) -> str:
        """静态渐变分隔线。

        每个字符使用渐变色号中的对应值，无动效。
        """
        from ..core.text_utils import build_gradient_ansi
        return build_gradient_ansi(colors, char=self._char)

    def _render_wave(self, colors: list[int]) -> str:
        """波动分隔线（水波流动效果）。

        在渐变基础上叠加正弦波动，帧号推进时波浪沿分隔线方向传播。
        """
        from ..core.text_utils import build_sep_wave
        return build_sep_wave(colors, self._frame, char=self._char)

    def _render_shimmer(self, colors: list[int]) -> str:
        """流光分隔线（亮带扫光效果）。

        一条亮带沿分隔线方向周期性移动，产生扫光视觉效果。
        """
        from ..core.text_utils import build_sep_shimmer
        return build_sep_shimmer(colors, self._frame, char=self._char)

    def _render_sparkle(self, colors: list[int]) -> str:
        """闪烁分隔线（每字符独立闪烁，如星光）。

        每个字符的亮度独立闪烁，相邻字符带相位偏移，产生星点闪烁效果。
        """
        from ..core.effects import sparkle_brightness
        parts: list[str] = []
        for i, c in enumerate(colors):
            # 每字符带相位偏移（i），产生独立闪烁效果
            t = sparkle_brightness(self._frame + i, period=6)
            brightened = max(0, min(255, round(c + t * 25)))
            parts.append(f"\033[38;5;{brightened}m{self._char}")
        return "".join(parts) + "\033[0m"

    def _render_glow(self, colors: list[int]) -> str:
        """辉光呼吸分隔线（整线均匀呼吸）。

        整条分隔线使用同一呼吸色号，在 start_color 和 end_color+30 间
        正弦呼吸，产生柔光脉动视觉效果。
        """
        from ..core.effects import sine_color
        breath_color = sine_color(
            self._frame,
            self._start_color,
            min(255, self._end_color + 30),
            period=12,
        )
        return f"\033[38;5;{breath_color}m{self._char * len(colors)}\033[0m"


__all__ = [
    "Separator",
]
