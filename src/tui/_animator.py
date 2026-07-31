"""最小动画上下文存根 — 替换已删除的 animation/ 模块。

提供与旧 AnimatorContext 兼容的接口，帧号从 0 开始单调递增，
每次 tick() 调用递增 1，在 render 线程中由底部栏重绘驱动。
"""

from __future__ import annotations

import math
import threading


class AnimatorContext:
    """轻量动画上下文 — 提供 frame 计数器和 breath_frame 属性。

    兼容旧 AnimatorContext 的公开 API：
        - tick() → None: 帧计数递增
        - frame → int: 当前帧号
        - breath_frame → int: 呼吸帧号（与 frame 同值）
        - pulse_frame → int: 脉冲帧号（frame % 4）
        - get_default() → AnimatorContext: 获取单例

    不需要任何第三方库，纯标准库实现。
    """

    _instance: "AnimatorContext | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._frame: int = 0

    @classmethod
    def get_default(cls) -> "AnimatorContext":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def frame(self) -> int:
        return self._frame

    @property
    def breath_frame(self) -> int:
        """呼吸帧 — 兼容旧接口，返回与 frame 相同的值。"""
        return self._frame

    @property
    def pulse_frame(self) -> int:
        """脉冲帧 — 4 帧循环，兼容旧接口。"""
        return self._frame % 4

    def sine_color(self, lo: int, hi: int, period: int = 12) -> int:
        """正弦波颜色插值。

        Args:
            lo: 最暗色号。
            hi: 最亮色号。
            period: 周期帧数。

        Returns:
            插值色号（lo~hi 范围）。
        """
        ratio = (math.sin(2 * math.pi * self._frame / period) + 1) / 2
        return lo + int((hi - lo) * ratio)

    def tick(self) -> None:
        """帧计数递增。每帧调用一次。"""
        self._frame += 1

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）。"""
        with cls._lock:
            cls._instance = None


__all__ = ["AnimatorContext"]
