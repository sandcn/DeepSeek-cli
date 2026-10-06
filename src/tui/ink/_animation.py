"""_animation — useAnimation 共享动画驱动（React Ink v7 等价）。

官方 ``useAnimation`` 语义：所有动画组件**共用一个定时器**——一次定时器
推进只触发一轮渲染，多个动画组件合并渲染。本框架渲染循环为 30Hz
（``TuiConfig.render_interval``），故驱动直接挂在渲染循环上：
``advance_animation()`` 每帧调用一次（session 注入），递增全局 tick 序号并
通知全部订阅者（订阅者经 ``useSyncExternalStore`` 的 ``_schedule`` 请求
重渲染）。

组件侧 ``useAnimation`` 依据「当前时间 - 起始时间」推导 ``frame``/``time``/
``delta``——渲染被节流时 delta 自然增大（官方语义「Accounts for throttled
renders」）。

依赖：仅标准库（无 ink 内部依赖，避免循环）。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

_logger = logging.getLogger(__name__)

#: 全局 tick 序号（每次 ``advance_animation`` 递增；useSyncExternalStore 快照）。
_tick_seq: int = 0
#: 上次 tick 的单调时钟（供测试/诊断）。
_last_tick_time: float = 0.0
#: 动画订阅者（useAnimation → useSyncExternalStore subscribe 注入）。
_listeners: "set[Callable[[], None]]" = set()


def animation_snapshot() -> int:
    """当前 tick 序号（useSyncExternalStore get_snapshot）。"""
    return _tick_seq


def subscribe_animation(listener: Callable[[], None]) -> Callable[[], None]:
    """订阅动画 tick 变更；返回取消订阅函数。"""
    _listeners.add(listener)
    return lambda: _listeners.discard(listener)


def advance_animation(now: float | None = None) -> None:
    """推进一帧动画：递增 tick 序号并记录时间（session 渲染循环每帧调用）。

    ★ 不触发订阅者重渲染（review 修复）：订阅者回调经 hooks ``_schedule``
    落到 ``session._request_render``（置 ``_bottom_redraw_requested`` = force，
    打破 30Hz 节流）——若每帧 advance 都通知，将形成
    「渲染→advance→force→立即再渲染」的无节流忙循环。本框架渲染循环为
    **全程 30Hz**（空闲也持续渲染），动画组件每帧都会被重渲染，
    ``frame``/``time`` 由挂钟时间推导 → 无需 tick 通知即可平滑推进。
    ``_listeners`` 仅用于 ``has_active_animations()``（渲染循环动画探测）。
    """
    global _tick_seq, _last_tick_time
    _tick_seq += 1
    _last_tick_time = now if now is not None else time.monotonic()


def notify_animation_listeners() -> None:
    """显式通知动画订阅者（独立于 advance；供外部驱动/测试使用）。

    订阅者回调异常被吞掉并记 debug（单个动画组件异常不阻断其余组件）。
    """
    for listener in list(_listeners):
        try:
            listener()
        except Exception:
            _logger.debug("动画订阅回调异常", exc_info=True)


def reset_animation_state() -> None:
    """复位驱动状态（测试/会话复用；清空订阅者与计数）。"""
    global _tick_seq, _last_tick_time
    _tick_seq = 0
    _last_tick_time = 0.0
    _listeners.clear()


def has_active_animations() -> bool:
    """是否存在活跃动画订阅（供渲染循环 ``_needs_animation`` 探测）。"""
    return bool(_listeners)


__all__ = [
    "animation_snapshot",
    "subscribe_animation",
    "advance_animation",
    "notify_animation_listeners",
    "reset_animation_state",
    "has_active_animations",
]
