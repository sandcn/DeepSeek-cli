"""_animation — useAnimation 共享动画驱动（React Ink v7 等价）。

官方 ``useAnimation`` 语义：所有动画组件**共用一个定时器**——一次定时器
推进只触发一轮渲染，多个动画组件合并渲染。本框架渲染循环为 30Hz
（``TuiConfig.render_interval``），故驱动直接挂在渲染循环上：
``advance_animation()`` 每帧调用一次（session 注入），递增 tick 序号。

组件侧 ``useAnimation`` 依据「当前时间 - 起始时间」推导 ``frame``/``time``/
``delta``——渲染被节流时 delta 自然增大（官方语义「Accounts for throttled
renders」）。

★ 多会话隔离（P0 架构修复）：驱动状态（tick / 上次时间 / 订阅者集合）挂到
``HookContext``——此前为模块级全局变量，两个渲染会话共享同一 tick 与订阅者
集合（``has_active_animations`` 会因其它会话的动画组件返回 True）。
所有 API 接受可选 ``ctx``：None 时用「当前激活上下文」（渲染期/渲染线程
调用正确），显式传入用于非渲染期调用方。

依赖：标准库 + ``_hook_context``（无 ink 内部依赖，避免循环）。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from ._hook_context import HookContext, current_context

_logger = logging.getLogger(__name__)


def _ctx(ctx: "HookContext | None") -> HookContext:
    return ctx if ctx is not None else current_context()


def animation_snapshot(ctx: "HookContext | None" = None) -> int:
    """当前 tick 序号（useSyncExternalStore get_snapshot）。"""
    return _ctx(ctx).animation_tick


def subscribe_animation(
    listener: Callable[[], None], ctx: "HookContext | None" = None,
) -> Callable[[], None]:
    """订阅动画 tick 变更；返回取消订阅函数。"""
    target = _ctx(ctx)
    target.animation_listeners.add(listener)
    return lambda: target.animation_listeners.discard(listener)


def advance_animation(now: float | None = None, ctx: "HookContext | None" = None) -> None:
    """推进一帧动画：递增 tick 序号并记录时间（session 渲染循环每帧调用）。

    ★ 不触发订阅者重渲染（review 修复）：订阅者回调经 hooks ``_schedule``
    落到 ``session._request_render``（置 ``_bottom_redraw_requested`` = force，
    打破节流）——若每帧 advance 都通知，将形成「渲染→advance→force→立即再
    渲染」的无节流忙循环。本框架渲染循环**恒定 30Hz**（``render_interval``
    = 1/30s，不可改变），动画组件每帧都会被重渲染，``frame``/``time``
    由挂钟时间推导 → 无需 tick 通知即可平滑推进。
    ``animation_listeners`` 供 ``has_active_animations()`` 查询（动画订阅
    探测 API；渲染线程恒定渲染后不再作为渲染决策依据）。
    """
    target = _ctx(ctx)
    target.animation_tick += 1
    target.animation_last_time = now if now is not None else time.monotonic()


def notify_animation_listeners(ctx: "HookContext | None" = None) -> None:
    """显式通知动画订阅者（独立于 advance；供外部驱动/测试使用）。

    订阅者回调异常被吞掉并记 debug（单个动画组件异常不阻断其余组件）。
    """
    for listener in list(_ctx(ctx).animation_listeners):
        try:
            listener()
        except Exception:
            _logger.debug("动画订阅回调异常", exc_info=True)


def reset_animation_state(ctx: "HookContext | None" = None) -> None:
    """复位驱动状态（测试/会话复用；清空订阅者与计数）。"""
    _ctx(ctx).reset_animation()


def has_active_animations(ctx: "HookContext | None" = None) -> bool:
    """是否存在活跃动画订阅（动画订阅探测 API；渲染线程恒定 30Hz 渲染，
    不再依赖此探测决定是否渲染）。"""
    return bool(_ctx(ctx).animation_listeners)


__all__ = [
    "animation_snapshot",
    "subscribe_animation",
    "advance_animation",
    "notify_animation_listeners",
    "reset_animation_state",
    "has_active_animations",
]
