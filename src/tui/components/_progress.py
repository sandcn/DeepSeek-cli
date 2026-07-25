"""进度条组件 — ProgressBar。

提供渐变填充的 ANSI 进度条，支持渐变色号填充、呼吸/脉动动效、
百分比显示和窄屏自适应。继承 TuiComponent 基类。
"""

from __future__ import annotations

from ._base import TuiComponent
from ..render_buffer import RenderBuffer
from ..terminal.narrow import narrow_truncate


class ProgressBar(TuiComponent):
    """渐变填充进度条组件。

    使用 256 色渐变色号填充已完成的进度部分，
    空部分使用占位字符显示。支持选择是否显示百分比文本。
    当 ``animated=True`` 时，填充尾部叠加正弦呼吸/脉动效果。
    当 ``pulse_mode=True`` 时，尾部 1/3 使用脉冲列车动效替代呼吸效果。

    Args:
        progress: 进度值 [0.0, 1.0]，超出范围自动 clamp。
        width: 进度条总宽度（字符数），默认 30。
        fill_char: 填充字符，默认 █ (U+2588)。
        empty_char: 空部分字符，默认 ░ (U+2591)。
        gradient_start: 渐变起始 256 色号，默认 214（亮橙）。
        gradient_end: 渐变结束 256 色号，默认 41（亮绿）。
        show_percent: 是否在末尾显示百分比文本，默认 ``True``。
        frame: 当前帧号，动效推进用。
        animated: 是否启用填充尾部呼吸/脉动动效，默认 ``True``。
        pulse_mode: 是否使用脉冲列车动效替代呼吸效果，默认 ``False``。
          仅当 ``animated=True`` 且 ``frame > 0`` 时生效。
    """

    def __init__(
        self,
        progress: float,
        width: int = 30,
        fill_char: str = "\u2588",
        empty_char: str = "\u2591",
        gradient_start: int = 214,
        gradient_end: int = 41,
        show_percent: bool = True,
        frame: int = 0,
        animated: bool = True,
        pulse_mode: bool = False,
        *,
        props: dict | None = None,
    ) -> None:
        super().__init__(props=props)
        self._progress = progress
        self._width = width
        self._fill_char = fill_char
        self._empty_char = empty_char
        self._gradient_start = gradient_start
        self._gradient_end = gradient_end
        self._show_percent = show_percent
        self._frame = frame
        self._animated = animated
        self._pulse_mode = pulse_mode

    # ── 公共 API ────────────────────────────────────────────────────────

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染进度条。

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时直接写入 buffer。

        Returns:
            str | None: 无 buffer 时返回渲染字符串；有 buffer 时返回 None。
        """
        result = self.render_with_narrow_fallback(buffer, narrow_method=self._render_narrow)
        if result is not None:
            return result

        # 宽屏：正常宽度 + 百分比
        result = self._build_progress()
        return self._finalize_render(result, buffer)

    def _render_narrow(self) -> str:
        """窄屏降级：减少宽度，隐藏百分比。"""
        width = narrow_truncate(30)
        if width <= 0:
            return ""

        progress = max(0.0, min(1.0, self._progress))
        filled_width = round(progress * width)
        empty_width = width - filled_width

        fill_part = self._build_fill(filled_width)
        empty_part = self._empty_char * empty_width

        return f"{fill_part}\033[0m{empty_part}"

    def _build_progress(self) -> str:
        """构建进度条字符串（核心渲染逻辑 — 宽屏路径）。

        Returns:
            带 ANSI 颜色和渐变的进度条字符串。
        """
        width = self._width
        show_pct = self._show_percent

        # 确保宽度有效
        if width <= 0:
            return ""

        # clamp progress 到 [0.0, 1.0]
        progress = max(0.0, min(1.0, self._progress))

        # 计算填充长度
        filled_width = round(progress * width)
        empty_width = width - filled_width

        # 构建填充部分（渐变色）
        fill_part = self._build_fill(filled_width)

        # 构建空部分
        empty_part = self._empty_char * empty_width

        # 构建百分比文本
        pct_text = ""
        if show_pct:
            pct_text = f" {round(progress * 100)}%"

        return f"{fill_part}\033[0m{empty_part}{pct_text}"

    # ── 填充构建 ────────────────────────────────────────────────────────

    def _build_fill(self, filled_width: int) -> str:
        """构建渐变色填充部分。

        使用 gradient_range 生成从 gradient_start 到 gradient_end 的色号列表，
        每个填充字符使用对应色号。当 ``animated=True`` 时，在填充尾部
        叠加正弦呼吸/脉动效果。

        Args:
            filled_width: 填充字符数。

        Returns:
            带 ANSI 渐变色号的填充字符串（不含 RESET，由调用方统一追加）。
        """
        if filled_width <= 0:
            return ""

        from ..core.gradient import gradient_range
        colors = gradient_range(self._gradient_start, self._gradient_end, filled_width)

        if self._animated and self._frame > 0 and filled_width > 0:
            if self._pulse_mode:
                return self._render_pulse_fill(colors)
            # 对填充尾部叠加呼吸脉动效果
            return self._render_animated_fill(colors)

        # 静态渐变填充
        parts: list[str] = []
        for c in colors:
            parts.append(f"\033[38;5;{c}m{self._fill_char}")
        return "".join(parts)

    def _render_animated_fill(self, colors: list[int]) -> str:
        """带动效的渐变填充渲染。

        填充尾部（最后 1/3 区域）叠加正弦呼吸脉动效果，
        使进度条看起来有"生命力"。

        Args:
            colors: 渐变色号列表。

        Returns:
            带 ANSI 动效的填充字符串。
        """
        from ..core.effects import sine_color
        n = len(colors)
        # 动效区域：最后 1/3 字符
        effect_start = max(0, n * 2 // 3)

        parts: list[str] = []
        for i, c in enumerate(colors):
            if i >= effect_start:
                # 尾部叠加呼吸效果：在原始色号和更亮色号间正弦过渡
                breath_c = sine_color(
                    self._frame + i - effect_start,
                    c,
                    min(255, c + 25),
                    period=10,
                )
                parts.append(f"\033[38;5;{breath_c}m{self._fill_char}")
            else:
                parts.append(f"\033[38;5;{c}m{self._fill_char}")
        return "".join(parts)

    def _render_pulse_fill(self, colors: list[int]) -> str:
        """脉冲列车动效填充。

        头部 2/3 区域使用静态渐变，尾部 1/3 使用脉冲列车效果，
        产生流动的脉冲光点沿进度条推进的视觉效果。

        Args:
            colors: 渐变色号列表。

        Returns:
            带 ANSI 脉冲列车动效的填充字符串。
        """
        from ..core.effects import build_pulse_train_ansi
        n = len(colors)
        # 动效区域：最后 1/3 字符
        effect_start = max(0, n * 2 // 3)

        parts: list[str] = []
        # 前 2/3 静态渐变
        for i in range(effect_start):
            parts.append(f"\033[38;5;{colors[i]}m{self._fill_char}")

        # 后 1/3 脉冲列车
        tail_width = n - effect_start
        if tail_width > 0:
            pulse_part = build_pulse_train_ansi(
                width=tail_width,
                frame=self._frame,
                color_low=self._gradient_end,
                color_high=self._gradient_start,
                char=self._fill_char,
            )
            parts.append(pulse_part)

        return "".join(parts)


__all__ = [
    "ProgressBar",
]
