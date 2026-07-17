"""输入状态 — InputState（可变，线程安全）。"""

from __future__ import annotations

import threading
import time
from dataclasses import field
from src._compat import dataclass


_ESC_DOUBLE_CLICK_INTERVAL = 0.5
"""两次 Esc 间隔 < 500ms 视为双击。"""


@dataclass(slots=True)
class InputState:
    """输入状态（可变，线程安全）。

    Esc 双击检测使用内部锁保护。
    模型状态统一由 UISessionState.model 管理（单数据源）。
    """
    _last_esc_time: float = 0.0
    _esc_lock: threading.Lock = field(default_factory=threading.Lock)

    def record_esc_press(self) -> bool:
        """记录一次 Esc 按键，返回 True 表示双击（<500ms 内两次按下）。"""
        now = time.monotonic()
        with self._esc_lock:
            if now - self._last_esc_time < _ESC_DOUBLE_CLICK_INTERVAL:
                self._last_esc_time = 0.0
                return True
            self._last_esc_time = now
            return False

    def reset_esc_state(self) -> None:
        """重置 Esc 双击检测状态。"""
        with self._esc_lock:
            self._last_esc_time = 0.0


__all__ = ["InputState"]
