"""Spinner 组件 — React Ink 风格终端 spinner。

提供 <Spinner> 组件，支持 7 种 spinner 类型和自定义颜色/速度。
使用 use_spinner Hook 驱动动画帧，通过 AnimationClock 全局时钟同步。

使用示例:
    spinner = Spinner(type="dots", color="cyan")
    print(spinner.render())  # 输出当前帧的 spinner 字符
"""

from __future__ import annotations
from typing import Any
from ..components.base import TuiComponent
from ..components.animation import use_spinner, SPINNER_FRAMES
from ..infrastructure.styled import StyledText


class Spinner(TuiComponent):
    """React Ink 风格 Spinner 组件。

    属性:
        type: spinner 类型 ("braille"|"dots"|"line"|"pulse"|"bounce"|"dots_wave"|"arrow")
        color: 颜色名（如 "cyan"、"blue"），None 表示无颜色
        interval: 帧间隔毫秒，默认 80
    """

    def __init__(self, type: str = "dots", color: str | None = None,
                 interval: int = 80, **props: Any) -> None:
        super().__init__()
        self.type = type if type in SPINNER_FRAMES else "dots"
        self.color = color
        self.interval = interval

    @property
    def key(self) -> str:
        return f"spinner_{self.type}"

    def render(self) -> str | StyledText:
        anim = use_spinner({
            "type": self.type,
            "interval": self.interval,
            "color": self.color,
        })
        char = anim["char"]
        if self.color:
            return StyledText(char, fg=self.color)
        return char
