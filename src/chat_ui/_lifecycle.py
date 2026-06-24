"""chat_ui 生命周期模块 — ChatUILifecycle 管理 ChatUIConsumer 的启动/停止/暂停/恢复。

从 _consumer.py 提取，封装生命周期方法及其依赖的 _HANDLER_MAP 事件绑定逻辑。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._consumer import ChatUIConsumer
    from ._engine import TuiEngine
    from ._dispatcher import EventDispatcher
    from ._terminal_io import TerminalIO
    from ._render_state import _RenderState
    from ._protocols import BottomBarProtocol

_logger = logging.getLogger(__name__)


class ChatUILifecycle:
    """ChatUIConsumer 生命周期管理器。

    职责：
    - 启动/停止流程（事件订阅/取消、引擎启动/停止、全局注册/注销）
    - 暂停/恢复流程（交互式工具场景）
    - 子系统批量创建（create_subsystems 静态工厂）

    _started 状态由本类独立管理，consumer 通过 started 属性读取。
    """

    def __init__(self, consumer: "ChatUIConsumer"):
        self._consumer = consumer
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    # ── 子系统工厂 ────────────────────────────────

    @staticmethod
    def create_subsystems(consumer: "ChatUIConsumer") -> dict:
        """创建所有子系统实例并返回属性名→实例的映射字典。

        由 consumer.__init__ 调用。consumer 引用用于：
        - 获取 event_bus（传入 EventDispatcher？不，disp 通过 engine.push_cmd 绑定）
        - 仅用于类型标注一致性
        """
        import sys as _sys
        from ._render_state import _RenderState
        from ._renderer import TuiRenderer
        from ._engine import TuiEngine
        from ._dispatcher import EventDispatcher
        from ._terminal_io import TerminalIO
        from ._completion import _CmplHandler
        from ._subsystem_factory import (
            create_bottom_bar, create_cursor_tracker, create_completion_engine,
        )
        from rich.console import Console
        from ..api.renderer.output import OutputAdapter
        from ..terminal import get_safe_console_config
        from ..ui.tui._message_display import _display_messages
        from ._lock import output_lock

        rs = _RenderState()
        cursor_tracker = create_cursor_tracker()
        bottom_bar = create_bottom_bar(cursor_tracker=cursor_tracker)
        tio = TerminalIO(lock=output_lock)

        console = Console(**get_safe_console_config(), file=_sys.__stdout__)
        output_adapter = OutputAdapter(console)

        tui_renderer = TuiRenderer(
            rs, output_adapter, bottom_bar,
            on_display_messages=_display_messages,
            cursor_tracker=cursor_tracker,
            terminal_io=tio,
        )

        engine = TuiEngine(
            tui_renderer, bottom_bar,
            cursor_tracker=cursor_tracker,
            terminal_io=tio,
        )

        disp = EventDispatcher(push_cmd=engine.push_cmd)
        rs.set_output_adapter(output_adapter)

        completion_engine = create_completion_engine()
        cmpl = _CmplHandler(
            bottom_bar, completion_engine,
            request_redraw=engine.request_bottom_redraw,
        )

        return {
            "_rs": rs,
            "_cursor_tracker": cursor_tracker,
            "_bottom_bar": bottom_bar,
            "_tio": tio,
            "_tui_renderer": tui_renderer,
            "_engine": engine,
            "_disp": disp,
            "_cmpl": cmpl,
            "_completion_engine": completion_engine,
        }

    # ── 生命周期方法 ──────────────────────────────

    def start(self, state_lock, bound_handlers_ref: list, bus, disp,
              engine: "TuiEngine", register_fn) -> None:
        """启动 ChatUIConsumer。

        订阅 11 种 DisplayEvent、先取消已有绑定避免重复注册、
        启动渲染线程、注册为活跃消费者。幂等操作。

        Args:
            state_lock: threading.Lock，保护 _started 读写
            bound_handlers_ref: 单元素列表，元素为 dict[type, handler] 或 None
            bus: DisplayEventBus 实例
            disp: EventDispatcher 实例
            engine: TuiEngine 实例
            register_fn: _register_consumer 函数引用
        """

        with state_lock:
            if self._started:
                return

            bound_handlers = bound_handlers_ref[0]
            if bound_handlers is None:
                bound_handlers = {}
                bound_handlers_ref[0] = bound_handlers
                from ..ui.events.event_types import (
                    ReasoningChunkEvent, ContentChunkEvent, PhaseDoneEvent,
                    ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent,
                    ToolSummaryEvent, ParseInfoEvent, ParseInfoDoneEvent,
                    OutputEvent, ModelPhaseEvent,
                )
                from ._dispatcher import _HANDLER_MAP
                _event_type_map = {
                    "ReasoningChunkEvent": ReasoningChunkEvent,
                    "ContentChunkEvent": ContentChunkEvent,
                    "PhaseDoneEvent": PhaseDoneEvent,
                    "ToolStartedEvent": ToolStartedEvent,
                    "ToolDoneEvent": ToolDoneEvent,
                    "ToolOutputChunkEvent": ToolOutputChunkEvent,
                    "ParseInfoEvent": ParseInfoEvent,
                    "ParseInfoDoneEvent": ParseInfoDoneEvent,
                    "OutputEvent": OutputEvent,
                    "ModelPhaseEvent": ModelPhaseEvent,
                    "ToolSummaryEvent": ToolSummaryEvent,
                }
                for key, (_, handler_name) in _HANDLER_MAP.items():
                    event_type = _event_type_map[key]
                    handler = getattr(disp, handler_name)
                    bound_handlers[event_type] = handler

            # 防御性取消已有订阅
            for event_type in bound_handlers:
                try:
                    bus.unsubscribe(bound_handlers[event_type], event_type=event_type)
                except Exception:
                    _logger.debug("start: unsubscribe %s 失败", event_type.__name__, exc_info=True)

            # 订阅所有事件
            for event_type in bound_handlers:
                bus.subscribe(bound_handlers[event_type], event_type=event_type)

            register_fn(self._consumer)
            engine.start()
            self._started = True

    def stop(self, state_lock, bound_handlers_ref: list, bus, engine: "TuiEngine",
             rs: "_RenderState", bottom_bar: "BottomBarProtocol", output_lock,
             unregister_fn) -> None:
        """停止 ChatUIConsumer。

        取消所有事件订阅、排空命令队列、停止渲染引擎、
        注销活跃消费者、清理渲染状态和底部栏。

        Args:
            state_lock: threading.Lock
            bound_handlers_ref: 单元素列表，元素为 dict[type, handler] 或 None
            bus: DisplayEventBus
            engine: TuiEngine
            rs: _RenderState
            bottom_bar: BottomBarProtocol
            output_lock: 输出锁
            unregister_fn: _unregister_consumer 函数引用
        """

        with state_lock:
            if not self._started:
                return

            bound_handlers = bound_handlers_ref[0]
            if bound_handlers is not None:
                for event_type in bound_handlers:
                    try:
                        bus.unsubscribe(bound_handlers[event_type], event_type=event_type)
                    except Exception:
                        _logger.debug("stop: unsubscribe %s 失败", event_type.__name__, exc_info=True)

            engine.flush()
            engine.stop()
            unregister_fn()
            with output_lock:
                rs.close_all()
                bottom_bar.teardown()
            self._started = False

    def suspend(self, state_lock, engine: "TuiEngine", bottom_bar: "BottomBarProtocol",
                output_lock) -> None:
        """暂停渲染引擎，供交互式工具独占终端。

        停止 render 线程并拆除底部栏，释放终端控制权。
        必须已启动（_started = True）才有效。

        Args:
            state_lock: threading.Lock
            engine: TuiEngine
            bottom_bar: BottomBarProtocol
            output_lock: 输出锁
        """
        with state_lock:
            if not self._started:
                return
            engine.flush()
            engine.stop()
            with output_lock:
                bottom_bar.teardown()

    def resume(self, state_lock, engine: "TuiEngine", bottom_bar: "BottomBarProtocol",
               tio: "TerminalIO", output_lock) -> None:
        """恢复渲染引擎，重建底部栏。

        重新获取终端尺寸、重绘底部栏并启动 render 线程。
        必须已启动（_started = True）且引擎未运行。

        Args:
            state_lock: threading.Lock
            engine: TuiEngine
            bottom_bar: BottomBarProtocol
            tio: TerminalIO
            output_lock: 输出锁
        """
        from ._const import _ANSI_CURSOR_BOTTOM
        from ..ui._blessed import get_terminal

        with state_lock:
            if not self._started:
                return
            if engine._render_running:
                return
            with output_lock:
                try:
                    term = get_terminal()
                    tio.write(term.move_xy(0, term.height - 1))
                except Exception:
                    _logger.debug("resume 光标定位失败, 使用 ANSI 回退", exc_info=True)
                    tio.write(_ANSI_CURSOR_BOTTOM)
                tio.flush()
                bottom_bar.setup()
                engine.start()
