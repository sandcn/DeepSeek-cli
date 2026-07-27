"""ChatUI 组件工厂 — 从 _consumer.py 提取的创建逻辑。

职责：创建 ChatUIConsumer 所需的全部子系统实例并装配。
将工厂逻辑与消费者 API 分离，降低 ChatUIConsumer 的耦合度。

架构分层（2026-07-22 泛化）：
  _create_framework_components()  — 框架层：OutputAdapter + TuiRenderer + TuiEngine
  _create_chat_ui_components()    — 应用层：ChatRenderState + _BottomBar + EventDispatcher + _CmplHandler
"""

from __future__ import annotations

import sys
import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..widgets.cursor_tracker import CursorTracker
    from ..widgets.bottom_bar import _BottomBar
    from ..input import Input
    from ..events.event_bus import DisplayEventBus
    from ...renderer.output import OutputAdapter
    from ..engine.renderer import TuiRenderer
    from ..state.render_state import ChatRenderState
    from ..engine.engine import TuiEngine
    from ..engine.dispatcher import EventDispatcher
    from .completion import _CmplHandler
    from .protocols import RenderEngine

from .chat_config import ChatConfig


@dataclasses.dataclass
class _FrameworkComponents:
    """框架层子系统容器 — 独立于聊天域的渲染基础设施。

    由 _create_framework_components() 创建，可被任意 TUI 应用复用。
    属性：
        output_adapter: 输出适配器（Rich Console 包装）
        renderer: 组件化渲染分发（TuiRenderer 实例）
        engine: render 线程 + 命令队列
    """
    output_adapter: "OutputAdapter"
    renderer: "TuiRenderer"
    engine: "RenderEngine"


@dataclasses.dataclass
class _ChatUIComponents:
    """ChatUIConsumer 的所有子系统实例容器。

    属性：
        rs: ChatRenderState — 渲染器生命周期
        cursor_tracker: 全局光标追踪
        bottom_bar: 底部固定输入栏
        input: 统一输入管理（Input 门面类）
        output_adapter: 输出适配器（Rich Console 包装）
        tui_renderer: 组件化渲染分发
        engine: render 线程 + 命令队列
        dispatcher: 事件过滤+入队
        cmpl_handler: Tab 补全交互
    """
    rs: "ChatRenderState"
    cursor_tracker: "CursorTracker"
    bottom_bar: "_BottomBar"
    output_adapter: "OutputAdapter"
    tui_renderer: "TuiRenderer"
    engine: "RenderEngine"
    dispatcher: "EventDispatcher"
    cmpl_handler: "_CmplHandler"
    input: "Input | None" = None


def _create_framework_components(
    rs: "ChatRenderState",
    output_adapter: "OutputAdapter",
    bottom_bar: "_BottomBar",
    cursor_tracker: "CursorTracker",
    on_display_messages=None,
) -> _FrameworkComponents:
    """创建框架层子系统：TuiRenderer + TuiEngine。

    仅依赖框架级模块（engine/renderer/renderer_base），不直接导入聊天域模块。
    聊天域依赖（ChatRenderState / _BottomBar / _display_messages）通过参数注入。

    Args:
        rs: 渲染状态实例（聊天域依赖，通过参数注入）
        output_adapter: 输出适配器
        bottom_bar: 底部栏实例（聊天域依赖，通过参数注入）
        cursor_tracker: 光标追踪器
        on_display_messages: 消息展示回调（聊天域依赖，通过参数注入）

    Returns:
        包含 renderer 和 engine 的 _FrameworkComponents 实例。
    """
    from ..engine.renderer import TuiRenderer
    from ..engine.engine import TuiEngine

    renderer = TuiRenderer(
        rs, output_adapter, bottom_bar,
        on_display_messages=on_display_messages,
        cursor_tracker=cursor_tracker,
    )
    engine: "RenderEngine" = TuiEngine(
        renderer, bottom_bar,
        cursor_tracker=cursor_tracker,
    )

    return _FrameworkComponents(
        output_adapter=output_adapter,
        renderer=renderer,
        engine=engine,
    )


def _create_chat_ui_components(event_bus=None) -> _ChatUIComponents:
    """创建并装配 ChatUIConsumer 所需的全部子系统。

    分两步：
      1. _create_framework_components() — 框架层（OutputAdapter + TuiRenderer + TuiEngine）
      2. 聊天域装配（ChatRenderState + _BottomBar + EventDispatcher + _CmplHandler）

    两步可分别独立测试和替换。

    Args:
        event_bus: DisplayEventBus 实例。为 None 时获取默认实例。

    Returns:
        包含全部子系统的 _ChatUIComponents 实例。
    """
    if event_bus is None:
        from ..events.event_bus import DisplayEventBus
        event_bus = DisplayEventBus.get_default()

    # 确保 tui.core.StyleSheet 样式已注册（惰性注册，幂等安全）
    from ..engine.const import register_tui_styles
    register_tui_styles()

    from ..widgets.cursor_tracker import CursorTracker
    from ..widgets.bottom_bar import _BottomBar
    from ..widgets.completion import CompletionEngine
    from ..input import Input
    from ...config.defaults import INPUT_HISTORY_FILE
    from rich.console import Console
    from ...renderer.output import OutputAdapter
    from ...terminal import get_safe_console_config

    from ..state.render_state import ChatRenderState
    from ..engine.dispatcher import EventDispatcher
    from .completion import _CmplHandler
    from ..pipeline.message_display import _display_messages

    # ── 框架基础设施 ──
    console = Console(**get_safe_console_config(), file=sys.__stdout__)
    output_adapter = OutputAdapter(console)

    # ── 聊天域子系统 ──
    rs = ChatRenderState()
    cursor_tracker = CursorTracker()
    bottom_bar = _BottomBar(cursor_tracker=cursor_tracker)

    # ── 统一输入管理（Input 门面类） ──
    input_instance = Input(
        fd=sys.stdin.fileno(),
        history_file=INPUT_HISTORY_FILE,
        cursor_tracker=cursor_tracker,
    )
    bottom_bar.set_input(input_instance)

    # ── 框架组件创建（传入聊天域依赖） ──
    fw = _create_framework_components(
        rs=rs,
        output_adapter=output_adapter,
        bottom_bar=bottom_bar,
        cursor_tracker=cursor_tracker,
        on_display_messages=_display_messages,
    )

    # ── 聊天域装配 ──
    dispatcher = EventDispatcher(push_cmd=fw.engine.push_cmd, config=ChatConfig.defaults())
    rs.set_output_adapter(output_adapter)
    cmpl_handler = _CmplHandler(
        bottom_bar, CompletionEngine(),
        request_redraw=fw.engine.request_bottom_redraw,
    )

    return _ChatUIComponents(
        rs=rs,
        cursor_tracker=cursor_tracker,
        bottom_bar=bottom_bar,
        input=input_instance,
        output_adapter=fw.output_adapter,
        tui_renderer=fw.renderer,
        engine=fw.engine,
        dispatcher=dispatcher,
        cmpl_handler=cmpl_handler,
    )
