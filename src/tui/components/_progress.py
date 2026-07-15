"""进度条组件 — ProgressBar。

提供渐变填充的 ANSI 进度条，支持渐变色号填充、呼吸/脉动动效、
百分比显示和窄屏自适应。继承 TuiComponent 基类。
"""

from __future__ import annotations

from ._base import TuiComponent


class ProgressBar(TuiComponent):
    """渐变填充进度条组件。

    使用 256 色渐变色号填充已完成的进度部分，
    空部分使用占位字符显示。支持选择是否显示百分比文本。
    当 ``animated=True`` 时，填充尾部叠加正弦呼吸/脉动效果。

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
    ) -> None:
        self._progress = progress
        self._width = width
        self._fill_char = fill_char
        self._empty_char = empty_char
        self._gradient_start = gradient_start
        self._gradient_end = gradient_end
        self._show_percent = show_percent
        self._frame = frame
        self._animated = animated

    # ── 公共 API ────────────────────────────────────────────────────────

    def render(self) -> str:
        """渲染进度条。

        Returns:
            带 ANSI 颜色和渐变的进度条字符串。
        """
        # 窄屏降级：减少宽度，隐藏百分比
        if self._is_narrow():
            width = self._narrow_width()
            show_pct = False
        else:
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

    # ── 窄屏检测 ────────────────────────────────────────────────────────

    @staticmethod
    def _is_narrow() -> bool:
        """检测当前是否为窄屏。"""
        from ..terminal.narrow import is_narrow
        return is_narrow()

    @staticmethod
    def _narrow_width() -> int:
        """获取窄屏下的进度条宽度。"""
        from ..terminal.narrow import narrow_truncate
        return narrow_truncate(30)

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


__all__ = [
    "ProgressBar",
]
