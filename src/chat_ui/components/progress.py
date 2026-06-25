"""Progress 组件 — React Ink 风格终端进度条。

提供 <Progress> 组件，支持确定进度和不确定进度两种模式。

使用示例:
    # 确定进度 — 50%
    p = Progress(value=0.5, width=20, color="cyan")
    print(p.render())
    # 输出: [██████████░░░░░░░░░░] 50%
    
    # 不确定进度 — 动画扫过
    p = Progress(value=None, width=20, color="yellow")
    print(p.render())
    # 输出: [░░░███░░░░░░░░░░░░░░░] (动画扫过)
"""

from __future__ import annotations
from typing import Any
from ..components.base import TuiComponent
from ..components.animation import use_progress
from ..infrastructure.styled import StyledText


class Progress(TuiComponent):
    """React Ink 风格 Progress 组件。
    
    属性:
        value: 进度值 0.0~1.0，None 表示不确定模式（动画扫过）
        width: 进度条宽度（字符数），默认 20
        color: 进度条颜色名（如 "cyan"、"green"），None 无颜色
        label: 左侧标签文本（如 "Building..."），默认空
        interval: 动画帧间隔（仅不确定模式），默认 80ms
    """
    
    def __init__(self, value: float | None = None, width: int = 20,
                 color: str = "cyan", label: str = "",
                 interval: int = 80, **props: Any) -> None:
        super().__init__()
        self.value = value
        self.width = max(width, 5)  # 最小宽度 5
        self.color = color
        self.label = label
        self.interval = interval
    
    @property
    def key(self) -> str:
        return f"progress_{id(self)}"
    
    def render(self) -> str | StyledText:
        # 确定模式时 clamp value 到 [0.0, 1.0]
        clamped_value: float | None = None
        if self.value is not None:
            clamped_value = max(0.0, min(1.0, float(self.value)))
        
        result = use_progress({
            "value": clamped_value,
            "width": self.width,
            "style": "bar",
            "color": self.color,
            "interval": self.interval,
        })
        rendered: str = result["rendered"]
        if self.label:
            rendered = f"{self.label} {rendered}"
        
        # 通过 StyledText 应用颜色
        if self.color:
            return StyledText(rendered, fg=self.color)
        return rendered
