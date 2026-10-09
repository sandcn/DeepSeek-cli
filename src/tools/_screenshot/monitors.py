"""显示器描述与选择（多显示器 / 全屏截取的跨平台语义）。

各平台后端负责产出 :class:`Monitor` 列表（Windows 走 ``EnumDisplayMonitors``、
X11 走 ``xrandr``、macOS 走 Quartz），本模块负责**统一选择语义**：

  - 省略 / ``'all'`` / ``'virtual'``：整个虚拟桌面（所有显示器合并区域）；
  - ``'primary'`` / ``'main'``：主显示器；
  - ``1`` / ``'1'``：第 N 个显示器（1 起，按平台枚举顺序）。

拼接多个显示器时用虚拟桌面的并集矩形表示；单显示器时即该显示器区域。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    """一个显示器的屏幕矩形（左上角 + 尺寸，像素）。"""

    left: int
    top: int
    width: int
    height: int
    primary: bool = False

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def to_dict(self) -> dict:
        return {
            "left": self.left, "top": self.top,
            "width": self.width, "height": self.height,
            "primary": self.primary,
        }


class MonitorError(ValueError):
    """显示器选择非法（序号越界 / 平台枚举失败）。"""


def union(monitors: list[Monitor]) -> Monitor:
    """把多个显示器合并为包围矩形（虚拟桌面）。"""
    if not monitors:
        raise MonitorError("没有可用显示器")
    left = min(item.left for item in monitors)
    top = min(item.top for item in monitors)
    right = max(item.right for item in monitors)
    bottom = max(item.bottom for item in monitors)
    return Monitor(left, top, right - left, bottom - top,
                   any(item.primary for item in monitors))


def resolve(monitors: list[Monitor], spec=None) -> Monitor:
    """按选择值挑出目标显示器区域。

    Args:
        monitors: 平台后端产出的显示器列表。
        spec: 选择值（``None``/``'all'``/``'virtual'`` = 全部合并；
            ``'primary'``/``'main'`` = 主显示器；整数 / 数字串 = 第 N 个）。

    Raises:
        MonitorError: 列表为空，或序号越界。
    """
    if not monitors:
        raise MonitorError("当前平台未能枚举到任何显示器")
    text = "" if spec is None else str(spec).strip().lower()
    if text in ("", "all", "virtual", "desktop", "0"):
        return union(monitors)
    if text in ("primary", "main"):
        for item in monitors:
            if item.primary:
                return item
        return monitors[0]
    try:
        index = int(text)
    except ValueError:
        raise MonitorError(
            f"显示器选择非法: {spec!r}。支持 'primary'（主显示器）、'all'（全部"
            f"合并）或序号（1..{len(monitors)}）"
        ) from None
    if not 1 <= index <= len(monitors):
        raise MonitorError(
            f"显示器序号越界: {index}（当前共 {len(monitors)} 个显示器）"
        )
    return monitors[index - 1]


__all__ = [
    "Monitor",
    "MonitorError",
    "resolve",
    "union",
]
