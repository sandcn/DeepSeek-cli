"""窗口几何（输入注入的坐标换算基础）。

``WindowFrame`` 描述「窗口截图坐标系」：原点 ``(screen_x, screen_y)`` 是该
区域左上角的屏幕坐标，``width`` / ``height`` 为区域尺寸——与 ``bash_opt``
``op=screenshot`` 产物一致，因此模型可以直接按截图像素给出输入坐标。
"""

from __future__ import annotations

from dataclasses import dataclass

from .action import Point
from .result import ActionError


@dataclass(frozen=True)
class WindowFrame:
    """窗口区域的屏幕位置与尺寸（截图坐标系）。"""

    screen_x: int
    screen_y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ActionError(f"窗口尺寸非法: {self.width}x{self.height}")

    @property
    def origin(self) -> tuple[int, int]:
        return self.screen_x, self.screen_y

    def to_screen(self, point: Point) -> tuple[int, int]:
        """窗口内坐标 → 屏幕坐标。"""
        return self.screen_x + point.x, self.screen_y + point.y

    def to_local(self, screen_x: int, screen_y: int) -> Point:
        """屏幕坐标 → 窗口内坐标（可能落在窗口外，由调用方校验）。"""
        return Point(int(screen_x) - self.screen_x, int(screen_y) - self.screen_y)

    def to_dict(self) -> dict:
        return {
            "screen_x": self.screen_x,
            "screen_y": self.screen_y,
            "width": self.width,
            "height": self.height,
        }


__all__ = ["WindowFrame"]
