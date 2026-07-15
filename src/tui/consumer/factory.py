"""ChatUI 组件工厂 — 从 _consumer.py 提取的创建逻辑。

职责：创建 ChatUIConsumer 所需的全部子系统实例并装配。
将工厂逻辑与消费者 API 分离，降低 ChatUIConsumer 的耦合度。

【inline 模式 · 2026-07-16 重构】
默认创建 InlineOutputTarget 并注入 TuiRenderer，切换为 non-fullscreen inline 模式。
output_target=None 时回退到 Rich Console OutputAdapter（全屏模式，向后兼容）。
"""

from __future__ import annotations

import sys
import dataclasses
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..widgets.cursor_tracker import CursorTracker
    from ..widgets.bottom_bar import _BottomBar
    from ..events.event_bus import DisplayEventBus
    from ...renderer.output import OutputAdapter
    from ...tui_framework.terminal.output_target import IOutputTarget
    from .renderer import TuiRenderer, _RenderState
    from .engine import TuiEngine
    from .dispatcher import EventDispatcher
    from .completion import _CmplHandler
    from .protocols import RenderEngine


@dataclasses.dataclass
class _ChatUIComponents:
    """ChatUIConsumer 的所有子系统实例容器。

    属性：
        rs: 渲染器生命周期
        cursor_tracker: 全局光标追踪（inline 模式下不再追踪全局光标）
        bottom_bar: 底部固定输入栏（inline 模式）
        output_adapter: 输出适配器（Rich Console 包装，回退模式）
        tui_renderer: 组件化渲染分发（已注入 output_target）
        engine: render 线程 + 命令队列
        dispatcher: 事件过滤+入队
        cmpl_handler: Tab 补全交互
        output_target: 框架 IOutputTarget（InlineOutputTarget，inline 模式默认）
        extra_widgets: 可选的新框架 Widget 列表

    ## output_target 参数说明

    ``output_target`` 用于控制渲染模式：
    - 为 InlineOutputTarget 实例（默认）：inline 非全屏模式，
      输出为纯文本流，无 ANSI 光标保存/恢复序列。
    - 为 None：回退到 Rich Console OutputAdapter（全屏模式），
      帧渲染由 TerminalTarget 负责（DECSTBM + SCOSC/DECRC）。
    """
    rs: _RenderState
    cursor_tracker: CursorTracker
    bottom_bar: _BottomBar
    output_adapter: OutputAdapter
    tui_renderer: TuiRenderer
    engine: RenderEngine
    dispatcher: EventDispatcher
    cmpl_handler: _CmplHandler
    output_target: Any = None
    extra_widgets: list[Any] = dataclasses.field(default_factory=list)

    def get_mode(self) -> str:
        """获取当前渲染模式。

        根据 output_target 类型判断：
        - output_target 支持 inline：返回 ``"inline"``（默认）
        - output_target 为 None：返回 ``"fullscreen"``（回退模式）

        Returns:
            ``"inline"`` 或 ``"fullscreen"``。
        """
        if self.output_target is not None:
            if hasattr(self.output_target, 'supports_inline') and self.output_target.supports_inline:
                return "inline"
        return "fullscreen"


def _create_chat_ui_components(
    event_bus=None,
    output_target: Any = None,
    extra_widgets: list[Any] | None = None,
) -> _ChatUIComponents:
    """创建并装配 ChatUIConsumer 所需的全部子系统。

    【inline 模式 · 2026-07-16】
    默认创建 InlineOutputTarget 并注入 TuiRenderer，切换为非全屏 inline 模式。

    模式控制：
    - output_target 使用默认值 → 自动创建 InlineOutputTarget（inline 模式）
    - output_target 显式传入 None → 回退到全屏 Rich Console 模式

    Args:
        event_bus: DisplayEventBus 实例。为 None 时获取默认实例。
        output_target: 可选的 IOutputTarget 实现。省略时自动创建 InlineOutputTarget。
        extra_widgets: 可选的新框架 Widget 列表（如 Input/Button/Select 等）。

    Returns:
        包含全部子系统的 _ChatUIComponents 实例。
    """
    if event_bus is None:
        from ..events.event_bus import DisplayEventBus
        event_bus = DisplayEventBus.get_default()

    from ..widgets.cursor_tracker import CursorTracker
    from ..widgets.bottom_bar import _BottomBar
    from ..widgets.completion import CompletionEngine
    from rich.console import Console
    from ...renderer.output import OutputAdapter
    from ...terminal import get_safe_console_config
    from ...tui_framework.terminal.output_target import InlineOutputTarget

    from .renderer import TuiRenderer, _RenderState
    from .engine import TuiEngine
    from .dispatcher import EventDispatcher
    from .completion import _CmplHandler
    from ..pipeline.message_display import _display_messages

    rs = _RenderState()
    cursor_tracker = CursorTracker()
    bottom_bar = _BottomBar(cursor_tracker=cursor_tracker)

    console = Console(**get_safe_console_config(), file=sys.__stdout__)
    output_adapter = OutputAdapter(console)

    # ★ inline 模式默认：output_target 参数默认值 None 时自动创建 InlineOutputTarget
    if output_target is None:
        output_target = InlineOutputTarget()

    tui_renderer = TuiRenderer(
        rs, output_adapter, bottom_bar,
        on_display_messages=_display_messages,
        cursor_tracker=cursor_tracker,
        output_target=output_target,
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
        output_target=output_target,
        extra_widgets=extra_widgets if extra_widgets is not None else [],
    )
