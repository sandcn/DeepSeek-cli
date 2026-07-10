"""ChatUI 组件工厂 — 从 _consumer.py 提取的创建逻辑。

职责：创建 ChatUIConsumer 所需的全部子系统实例并装配。
将工厂逻辑与消费者 API 分离，降低 ChatUIConsumer 的耦合度。
"""

from __future__ import annotations

import sys
import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui._cursor_tracker import CursorTracker
    from ..ui._bottom_bar import _BottomBar
    from ..ui.events.event_bus import DisplayEventBus
    from ..renderer.output import OutputAdapter
    from ._renderer import TuiRenderer, _RenderState
    from ._engine import TuiEngine
    from ._dispatcher import EventDispatcher
    from ._completion import _CmplHandler
    from ._protocols import RenderEngine


@dataclasses.dataclass
class _ChatUIComponents:
    """ChatUIConsumer 的所有子系统实例容器。

    属性：
        rs: 渲染器生命周期
        cursor_tracker: 全局光标追踪
        bottom_bar: 底部固定输入栏
        output_adapter: 输出适配器（Rich Console 包装）
        tui_renderer: 组件化渲染分发
        engine: render 线程 + 命令队列
        dispatcher: 事件过滤+入队
        cmpl_handler: Tab 补全交互
    """
    rs: _RenderState
    cursor_tracker: CursorTracker
    bottom_bar: _BottomBar
    output_adapter: OutputAdapter
    tui_renderer: TuiRenderer
    engine: RenderEngine
    dispatcher: EventDispatcher
    cmpl_handler: _CmplHandler


def _create_chat_ui_components(event_bus=None) -> _ChatUIComponents:
    """创建并装配 ChatUIConsumer 所需的全部子系统。

    Args:
        event_bus: DisplayEventBus 实例。为 None 时获取默认实例。

    Returns:
        包含全部子系统的 _ChatUIComponents 实例。
    """
    if event_bus is None:
        from ..ui.events.event_bus import DisplayEventBus
        event_bus = DisplayEventBus.get_default()

    from ..ui._cursor_tracker import CursorTracker
    from ..ui._bottom_bar import _BottomBar
    from ..ui._completion import CompletionEngine
    from rich.console import Console
    from ..renderer.output import OutputAdapter
    from ..terminal import get_safe_console_config

    from ._renderer import TuiRenderer, _RenderState
    from ._engine import TuiEngine
    from ._dispatcher import EventDispatcher
    from ._completion import _CmplHandler
    from ..ui.tui._message_display import _display_messages

    rs = _RenderState()
    cursor_tracker = CursorTracker()
    bottom_bar = _BottomBar(cursor_tracker=cursor_tracker)

    console = Console(**get_safe_console_config(), file=sys.__stdout__)
    output_adapter = OutputAdapter(console)

    tui_renderer = TuiRenderer(
        rs, output_adapter, bottom_bar,
        on_display_messages=_display_messages,
        cursor_tracker=cursor_tracker,
    )
    engine: RenderEngine = TuiEngine(
        tui_renderer, bottom_bar,
        cursor_tracker=cursor_tracker,
    )
    dispatcher = EventDispatcher(push_cmd=engine.push_cmd)
    rs.set_output_adapter(output_adapter)
    cmpl_handler = _CmplHandler(
        bottom_bar, CompletionEngine(),
        request_redraw=engine.request_bottom_redraw,
    )

    return _ChatUIComponents(
        rs=rs,
        cursor_tracker=cursor_tracker,
        bottom_bar=bottom_bar,
        output_adapter=output_adapter,
        tui_renderer=tui_renderer,
        engine=engine,
        dispatcher=dispatcher,
        cmpl_handler=cmpl_handler,
    )
