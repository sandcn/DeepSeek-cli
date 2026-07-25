"""装饰分隔线组件 — Separator。

提供静态渐变和多种动效（波动/流光/闪烁/辉光呼吸/极光/彩虹/脉冲）风格的分隔线渲染。
动效依赖 frame 参数推进，每帧调用一次 render() 获得不同视觉效果。
继承 TuiComponent 基类，可通过 render_to_adapter() 输出到 OutputAdapter。
"""

from __future__ import annotations

from ._base import TuiComponent
from ..render_buffer import RenderBuffer


class Separator(TuiComponent):
    """装饰分隔线组件。

    支持八种动效风格：
      - ``"static"``：静态渐变分隔线
      - ``"wave"``：正弦波动效果（如水波沿分隔线流动）
      - ``"shimmer"``：流光扫光效果（亮带沿分隔线方向移动）
      - ``"sparkle"``：闪烁效果（每字符独立闪烁，如星光）
      - ``"glow"``：辉光呼吸效果（整线均匀呼吸脉动）
      - ``"aurora"``：极光飘动效果（多层正弦波叠加，模拟极光流动）
      - ``"rainbow"``：彩虹旋转效果（色环滚动，每字符独立颜色）
      - ``"pulse"``：脉冲列车效果（高斯脉冲沿分隔线传播）

    Args:
        char: 分隔线字符，默认 ━ (U+2501)。
        width: 分隔线宽度（字符数）。``None`` 时自动根据终端宽度计算。
        style: 动效风格，可选 ``"wave"`` / ``"shimmer"`` / ``"sparkle"`` /
               ``"glow"`` / ``"static"`` / ``"aurora"`` / ``"rainbow"`` /
               ``"pulse"``。
        start_color: 起始 256 色号，默认 45（亮青）。
        end_color: 结束 256 色号，默认 237（深灰）。
        frame: 当前帧号，动效推进用。每帧调用 ``render()`` 时递增。
        num_pulses: 脉冲列车效果中的脉冲数量（仅 style="pulse" 时有效），默认 2。
        animated: 是否启用动效，默认 True。设为 False 时忽略 style，
                  始终使用静态渐变渲染（窄屏/低频动画场景）。
    """

    def __init__(
        self,
        char: str = "\u2501",
        width: int | None = None,
        style: str = "wave",
        start_color: int = 45,
        end_color: int = 237,
        frame: int = 0,
        num_pulses: int = 2,
        animated: bool = True,
        *,
        props: dict | None = None,
    ) -> None:
        super().__init__(props=props)
        self._char = char
        self._width = width
        self._style = style
        self._start_color = start_color
        self._end_color = end_color
        self._frame = frame
        self._num_pulses = num_pulses
        self._animated = animated

    # ── 公共 API ────────────────────────────────────────────────────────

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染分隔线。

        当 ``animated=False`` 或窄屏时降级为静态渐变渲染，
        避免不必要的动效消耗。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时直接写入 buffer。

        Returns:
            str | None: 无 buffer 时返回渲染字符串；有 buffer 时返回 None。
        """
        result = self._build_separator()
        if buffer is not None:
            if result:
                buffer.write(0, 0, result)
            return None
        return result

    def _build_separator(self) -> str:
        """构建分隔线字符串（核心渲染逻辑）。"""
        colors = self._build_colors()
        if not colors:
            return ""

        # 非动画模式：跳过所有动效，使用静态渐变
        if not self._animated:
            return self._render_static(colors)

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
        elif style == "aurora":
            return self._render_aurora()
        elif style == "rainbow":
            return self._render_rainbow()
        elif style == "pulse":
            return self._render_pulse()
        else:
            return self._render_static(colors)

    # ── 宽度解析 ────────────────────────────────────────────────────────

    def _resolve_width(self) -> int:
        """计算实际分隔线宽度。

        未指定时根据终端宽度自适应（委托窄屏函数计算）。
        """
        if self._width is not None:
            return max(0, self._width)
        from ..terminal.terminal import narrow_sep_width
        return narrow_sep_width(max_width=40)

    def _build_colors(self) -> list[int]:
        """生成 start_color → end_color 渐变色号列表。

        Returns:
            色号列表（0-255），宽度为 0 时返回空列表。
        """
        width = self._resolve_width()
        if width <= 0:
            return []
        from ..core.gradient import gradient_range
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

    def _render_aurora(self) -> str:
        """极光飘动分隔线（多层正弦波叠加）。

        模拟极光在天空中缓慢飘动的效果，
        多层正弦波在时空维度交错移动。
        """
        width = self._resolve_width()
        if width <= 0:
            return ""
        from ..core.effects import build_aurora_ansi
        return build_aurora_ansi(width, self._frame, char=self._char)

    def _render_rainbow(self) -> str:
        """彩虹旋转分隔线（色环滚动）。

        每个字符在彩虹色环上取色，frame 控制整体旋转，
        产生彩虹滚动视觉效果。
        """
        width = self._resolve_width()
        if width <= 0:
            return ""
        from ..core.effects import build_rainbow_ansi
        return build_rainbow_ansi(self._char * width, self._frame)

    def _render_pulse(self) -> str:
        """脉冲列车分隔线（高斯脉冲传播）。

        多个高斯形状脉冲沿分隔线方向传播，
        模拟心跳脉冲列的视觉效果。
        """
        width = self._resolve_width()
        if width <= 0:
            return ""
        from ..core.effects import build_pulse_train_ansi
        return build_pulse_train_ansi(
            width, self._frame,
            color_low=self._end_color,
            color_high=self._start_color,
            char=self._char,
        )


__all__ = [
    "Separator",
]
