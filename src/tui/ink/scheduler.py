"""RenderScheduler — 按需渲染调度器（P0-1 架构修复）。

背景：修复前 ``InkSession._should_render`` 恒定返回 True，渲染线程按固定
60Hz 节拍**每拍全量重建/调和/布局/绘制**，即使空闲、无命令、无动画也照常
执行——CPU 常驻，大文档下每帧 O(文档) 成本，且输入处理与渲染同线程串行。

本调度器把「何时渲染」收敛为单一失效源：

  - ``request(reason)``：任何状态变更（命令入队 / 输入 / 重绘 / resize /
    hooks setState / 动画 tick）调用，置待处理标记并唤醒循环；
  - ``set_animating(bool)``：存在 ``useAnimation`` 等时间驱动内容时，按
    ``max_fps`` 给出下一帧截止时间；无动画时无限期阻塞等待唤醒；
  - ``wait()``：空闲时阻塞在 Event 上（不轮询）；有请求时按 ``max_fps``
    节流（连续请求不会导致超频）；
  - ``on_frame()``：一帧完成后推进节流窗口。

``max_fps`` 仍为 60Hz（用户可感知的最大刷新率），但**空闲不再渲染**；
输入轮询由 ``InputPump`` 独立驱动，不依赖渲染节拍。
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class RenderScheduler:
    """按需渲染调度器（线程安全）。"""

    def __init__(
        self,
        max_fps: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = 1.0 / max_fps if max_fps > 0 else 0.0
        self._clock = clock
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._pending = False
        self._animating = False
        self._next_allowed = 0.0
        self._last_reason = ""
        self._running = True

    # ── 生命周期 ─────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            self._running = True
        self._event.set()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._event.set()

    # ── 请求 ─────────────────────────────────────────

    def request(self, reason: str = "") -> None:
        """请求一帧（幂等；空闲等待中的调度器会被唤醒）。"""
        with self._lock:
            self._pending = True
            self._last_reason = reason
        self._event.set()

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending

    # ── 动画 ─────────────────────────────────────────

    def set_animating(self, animating: bool) -> None:
        """设置是否存在时间驱动内容（动画/流式占位）。"""
        with self._lock:
            changed = bool(animating) != self._animating
            self._animating = bool(animating)
            if changed and self._animating:
                # 首次进入动画：立即允许下一帧，由 on_frame 推进窗口。
                self._next_allowed = 0.0
        if changed and animating:
            self._event.set()

    @property
    def animating(self) -> bool:
        with self._lock:
            return self._animating

    # ── 等待 ─────────────────────────────────────────

    def wait(self) -> bool:
        """等待下一次可渲染时机（空闲阻塞、有请求/动画按 max_fps 节流）。

        Returns:
            True — 本拍应处理（有待处理请求或动画到期）；
            False — 调度器已停止（``stop``）。
        """
        while True:
            with self._lock:
                if not self._running:
                    return False
                pending = self._pending
                animating = self._animating
                now = self._clock()
                next_allowed = self._next_allowed

            if animating and now >= next_allowed:
                return True
            if pending and now >= next_allowed:
                with self._lock:
                    if self._pending:
                        self._pending = False
                        return True
                continue
            # 空闲（无请求无动画）→ 无限等待；否则等到节流/动画截止。
            if pending or animating:
                timeout = max(0.0, next_allowed - now)
            else:
                timeout = None
            self._event.wait(timeout)
            self._event.clear()

    def on_frame(self) -> None:
        """一帧完成：推进节流窗口（下一帧最早在 interval 之后）。"""
        with self._lock:
            self._next_allowed = self._clock() + self._interval


__all__ = ["RenderScheduler"]
