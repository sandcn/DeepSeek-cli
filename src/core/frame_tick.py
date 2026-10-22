"""帧驱动注册表 — 所有 60Hz 周期性工作由 render 线程 game_loop 单次调用驱动。

设计约束（用户需求 2026-10-23）：
  - render 线程恒定 60Hz，帧率不可改变；
  - 单次调用（每拍一次 ``run()``）完成本帧所有工作，不再由各个子系统
    自建 60Hz 线程 / asyncio 定时任务各跑一套节拍。

各子系统（解析进度、面板推送等）把自己的「一拍工作」注册到本注册表，
由 ``InkSession._drain_queue`` 的 ``FRAME_TICKS`` 阶段统一调用一次。注册表
本身只做线程安全的回调收集与异常隔离，不持有任何业务状态：

  - 回调在 **render 线程** 中同步执行（可安全读写渲染侧状态）；
  - 单个回调异常只记日志，不中断本帧其它回调与渲染；
  - 非 TUI 场景（单次问答 / 无 render 线程）不会调用 ``run()``，回调不执行
    （调用方按「无 UI 无需帧驱动」自行降级，见 ``ToolParseTracker``）。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List

_logger = logging.getLogger(__name__)


class FrameTickRegistry:
    """线程安全的帧驱动回调注册表（每帧 ``run()`` 一次）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[], None]] = []

    def register(self, callback: Callable[[], None]) -> None:
        """注册一帧回调（幂等：同一回调重复注册只保留一份）。"""
        if not callable(callback):
            raise TypeError("frame tick callback 必须可调用")
        with self._lock:
            if callback in self._callbacks:
                return
            self._callbacks.append(callback)

    def unregister(self, callback: Callable[[], None]) -> None:
        """注销一帧回调（未注册时无操作）。"""
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                return

    def run(self) -> None:
        """执行本帧全部回调（快照遍历；单回调异常隔离）。"""
        with self._lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                _logger.warning("帧驱动回调异常（已跳过，不影响本帧渲染）", exc_info=True)

    def clear(self) -> None:
        """清空全部回调（会话/测试清理用）。"""
        with self._lock:
            self._callbacks.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._callbacks)


_registry = FrameTickRegistry()


def get_frame_tick_registry() -> FrameTickRegistry:
    """返回全局帧驱动注册表（进程内单例）。"""
    return _registry


__all__ = ["FrameTickRegistry", "get_frame_tick_registry"]
