"""TuiLifecycle — ChatUIConsumer 生命周期管理。

从 ChatUIConsumer 提取 start/stop/suspend/resume 生命周期方法，
以及事件订阅绑定/解绑逻辑。

单一职责：
  - start() / stop() / suspend() / resume()
  - DisplayEvent 订阅/取消订阅
  - 引擎启动/停止
  - 消费者注册/注销
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.tui._renderer import TuiEngine, EventDispatcher
    from src.tui._bottom_bar import _BottomBar
    from src.tui.state.render_state import ChatRenderState
    from src.tui.events.event_bus import DisplayEventBus

from src.tui._const import SplashCmd
from src.tui._locks import render_lock
from src.tui._screen import cursor_goto

_logger = logging.getLogger(__name__)


class TuiLifecycle:
    """ChatUI 生命周期管理器。

    管理 DisplayEvent 订阅、render 引擎线程、消费者注册。
    """

    def __init__(
        self,
        engine: "TuiEngine",
        bus: "DisplayEventBus",
        bb: "_BottomBar",
        rs: "ChatRenderState",
        dispatcher: "EventDispatcher",
        subagent_controller=None,
    ):
        self._engine = engine
        self._bus = bus
        self._bb = bb
        self._rs = rs
        self._dispatcher = dispatcher
        self._subagent_controller = subagent_controller

        self._bound_handlers: dict[type, Callable] | None = None
        self._state_lock = threading.Lock()
        self._started = False
        self._handlers_bound = False

    # ── 公开方法 ──────────────────────────────────

    def start(self) -> None:
        """启动生命周期。

        订阅 DisplayEvent，启动 render 引擎，展示品牌屏。
        """
        with self._state_lock:
            if self._started:
                return
            if self._bound_handlers is None:
                self._bound_handlers = {}
                for event_type, handler in self._dispatcher.list_handlers().items():
                    self._bound_handlers[event_type] = handler
            if self._handlers_bound:
                for event_type in self._bound_handlers:
                    try:
                        self._bus.unsubscribe(
                            self._bound_handlers[event_type], event_type=event_type,
                        )
                    except Exception:
                        _logger.debug(
                            "start: unsubscribe %s 失败",
                            event_type.__name__, exc_info=True,
                        )
            for event_type in self._bound_handlers:
                self._bus.subscribe(
                    self._bound_handlers[event_type], event_type=event_type,
                )
            self._handlers_bound = True
            self._engine.start()
            self._engine.push_cmd(SplashCmd())
            self._started = True

    def stop(self) -> None:
        """停止生命周期。

        取消事件订阅，停止引擎，清理渲染状态。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._bound_handlers is not None:
                for event_type in self._bound_handlers:
                    try:
                        self._bus.unsubscribe(
                            self._bound_handlers[event_type], event_type=event_type,
                        )
                    except Exception:
                        _logger.debug(
                            "stop: unsubscribe %s 失败",
                            event_type.__name__, exc_info=True,
                        )
            self._engine.flush()
            self._engine.stop()
            with render_lock:
                self._rs.close_all()
                self._bb.teardown()
            self._started = False
            self._bound_handlers = None

    def suspend(self) -> None:
        """暂停渲染引擎，供交互式工具独占终端。"""
        with self._state_lock:
            if not self._started:
                return
            self._engine.flush()
            self._engine.stop()
            with render_lock:
                self._bb.teardown()

    def resume(self) -> None:
        """恢复渲染引擎，重建底部栏。"""
        with self._state_lock:
            if not self._started:
                return
            if self._engine._render_running:
                return
            with render_lock:
                try:
                    from src.tui._screen import _get_terminal_size
                    _, height = _get_terminal_size()
                    sys.__stdout__.write(cursor_goto(height, 1))
                except Exception:
                    _logger.debug(
                        "resume 光标定位失败, 使用 ANSI 回退", exc_info=True,
                    )
                    sys.__stdout__.write("\033[9999;1H")
                sys.__stdout__.flush()
                self._bb._active = False
                self._bb.setup()
                self._engine.start()

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def handlers_bound(self) -> bool:
        return self._handlers_bound

    def register_event_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器。"""
        self._dispatcher.register_handler(event_type, handler_method)
        with self._state_lock:
            if self._started and self._handlers_bound:
                self._bus.subscribe(handler_method, event_type=event_type)
                if self._bound_handlers is not None:
                    self._bound_handlers[event_type] = handler_method


__all__ = ["TuiLifecycle"]
