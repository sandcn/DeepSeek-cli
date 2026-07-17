"""流式输出状态 — StreamingState（可变，高频更新）。"""

from __future__ import annotations

import time
from src._compat import dataclass

from ..animation.animator import AnimatorContext


@dataclass(slots=True)
class StreamingState:
    """流式输出临时状态（可变，高频更新）。

    与 UISessionState 分离的原因：变化频率极高，不可变快照开销大。

    ``speed`` 为基于 output_tokens / elapsed 自动计算的 property。
    """
    active: bool = False
    start_time: float = 0.0
    output_tokens: int = 0
    pulse_phase: int = 0  # 流式脉动指示器相位（0-3 循环）
    _speed_override: float = 0.0  # 可选手动覆盖

    @property
    def speed(self) -> float:
        """获取 token 速率（tok/s）。"""
        if self.active and self.elapsed > 0 and self.output_tokens > 0:
            return self.output_tokens / self.elapsed
        return self._speed_override

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed_override = value

    @property
    def elapsed(self) -> float:
        """流式输出已进行的时间（秒）。"""
        if not self.active or self.start_time <= 0:
            return 0.0
        return time.monotonic() - self.start_time

    def tick_pulse(self) -> None:
        """推进脉动指示器相位。"""
        AnimatorContext.get_default().tick()

    def start(self) -> None:
        """进入流式状态。"""
        if self.active:
            return
        self.active = True
        self.start_time = time.monotonic()
        self.output_tokens = 0
        self.pulse_phase = 0
        self._speed_override = 0.0

    def stop(self) -> None:
        """退出流式状态。"""
        self.active = False
        self.output_tokens = 0
        self.pulse_phase = 0
        self._speed_override = 0.0


__all__ = ["StreamingState"]
