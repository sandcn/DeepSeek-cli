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
import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.tui.ink.session import InkSession
    from src.tui._dispatcher import EventDispatcher
    from src.tui._ink_bridge import InkBridge
    from src.tui.app.model import AppModel
    from src.tui.events.event_bus import DisplayEventBus

from src.tui._const import SplashCmd
from src.renderer._locks import render_lock

_logger = logging.getLogger(__name__)


class TuiLifecycle:
    """ChatUI 生命周期管理器。

    管理 DisplayEvent 订阅、render 引擎线程、消费者注册。
    """

    def __init__(
        self,
        engine: "InkSession",
        bus: "DisplayEventBus",
        bb: "InkBridge",
        rs: "AppModel",
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

    @property
    def bound_handlers(self) -> dict[type, Callable] | None:
        """已绑定的事件处理器映射（公开读写方法，收敛私有字段访问）。"""
        return self._bound_handlers

    @bound_handlers.setter
    def bound_handlers(self, value: dict[type, Callable] | None) -> None:
        self._bound_handlers = value

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
            # 高频事件批处理评估（2026-07-31）：**不启用**。
            # 上游 StreamChunkHandler 已有 100ms 节流（≤10Hz），33ms 批处理窗口
            # 无实际合并收益；且批处理将「延迟分发的高频事件」与「同步直发的
            # 阶段切换事件」（PhaseDoneEvent CRITICAL 优先级 > ReasoningCmd HIGH）
            # 的顺序竞态放大为固定窗口——渲染线程先 close_reasoning()（state=CLOSED）
            # 后 Timer flush 的 ReasoningCmd 到达时 get_reasoning() 返回 None，
            # 导致推理文本静默丢失（ContentChunkEvent 同理导致 content 开新块）。
            # 批处理机制（_TimeWindowBatcher / register_batched_event）保留但不启用；
            # 若未来需要启用须先解决顺序保障。同步测试见
            # tests/test_tui/test_consumer.py::TestLifecycleBatchedRegistration
            # （断言 start() 后 bus._batched_events 为空）。
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
                self._safe_close_all(self._rs)
            # ★ 方向5：bb.teardown 为兼容层 no-op（_BottomBarCompatMixin），
            #   已删除——生命周期收敛为 engine.flush/stop 单一路径。
            self._started = False
            self._bound_handlers = None

    @staticmethod
    def _safe_close_all(rs) -> None:
        """关闭所有渲染通道（AppModel.flush_open_channels 或旧 close_all）。"""
        closer = getattr(rs, "flush_open_channels", None) or getattr(rs, "close_all", None)
        if closer is None:
            return
        try:
            closer()
        except Exception:
            _logger.debug("close_all/flush_open_channels 异常", exc_info=True)

    def suspend(self) -> None:
        """暂停渲染引擎，供交互式工具独占终端。

        方向5（生命周期收敛）：直接委托 ``engine.suspend()``（InkSession.
        suspend 已实现 flush + stop + ink_renderer.suspend + drain + 光标
        定位）；删除重复的 ``flush()``/``stop()`` 组合与 ``bb.teardown()``
        （``_BottomBarCompatMixin`` 的 setup/teardown 为兼容 no-op）。
        """
        with self._state_lock:
            if not self._started:
                return
            self._engine.suspend()

    def resume(self) -> None:
        """恢复渲染引擎。

        方向5（生命周期收敛）：直接委托 ``engine.resume()``（InkSession.
        resume 已实现 reset + 立即渲染 + 启动线程）；删除 ``bb.set_active/
        setup`` 调用（兼容层 no-op）。``is_render_running`` 检查保留
        （resume 幂等——已运行时不重复启动）。
        """
        with self._state_lock:
            if not self._started:
                return
            if self._engine.is_render_running():
                return
            self._engine.resume()

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def handlers_bound(self) -> bool:
        return self._handlers_bound

    def register_event_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器。

        P3-12 防御性：注册时先 ``bus.unsubscribe`` 旧 handler 再订阅新 handler，
        防止同类型重复注册导致旧 handler 泄漏（当前无生产调用方，防御性改动）。
        """
        self._dispatcher.register_handler(event_type, handler_method)
        with self._state_lock:
            if self._started and self._handlers_bound:
                old_handler = None
                if self._bound_handlers is not None:
                    old_handler = self._bound_handlers.get(event_type)
                if old_handler is not None:
                    try:
                        self._bus.unsubscribe(old_handler, event_type=event_type)
                    except Exception:
                        _logger.debug(
                            "register_event_handler: 取消订阅旧 handler 失败",
                            exc_info=True,
                        )
                self._bus.subscribe(handler_method, event_type=event_type)
                if self._bound_handlers is not None:
                    self._bound_handlers[event_type] = handler_method


__all__ = ["TuiLifecycle"]
