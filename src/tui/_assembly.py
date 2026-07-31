"""TuiAssembly — ChatUIConsumer 子系统装配工厂。

从 ChatUIConsumer._assemble() 提取为独立类，单一职责：
创建并注入所有子组件依赖。

设计原则：
  - 所有依赖通过构造器显式创建，消除 get_default() 隐式调用
  - 返回 TuiAssemblyResult dataclass 包含所有组件
  - 无生命周期管理职责（由 TuiLifecycle 承担）
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.tui._renderer import TuiEngine, TuiRenderer, EventDispatcher
    from src.tui._bottom_bar import _BottomBar
    from src.tui._input import Input
    from src.tui._completion import _CmplHandler
    from src.tui.state.render_state import ChatRenderState
    from src.tui._subagent_panel import SubAgentPanelController
    from src.tui._input_reader import InputReader

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# TuiAssemblyResult — 装配结果数据类
# ═══════════════════════════════════════════════════════════

class TuiAssemblyResult:
    """ChatUI 子系统装配结果容器。

    包含 ChatUIConsumer 所需的所有子组件实例。
    属性与 ChatUIConsumer 的内部属性名严格对应。
    """

    __slots__ = (
        'rs', 'engine', 'bb', 'dispatcher', 'renderer',
        'cmpl_handler', 'input_instance', 'subagent_controller',
        'reader', 'components',
    )

    def __init__(
        self,
        rs=None, engine=None, bb=None, dispatcher=None,
        renderer=None, cmpl_handler=None, input_instance=None,
        subagent_controller=None, reader=None, components=None,
    ):
        self.rs = rs
        self.engine = engine
        self.bb = bb
        self.dispatcher = dispatcher
        self.renderer = renderer
        self.cmpl_handler = cmpl_handler
        self.input_instance = input_instance
        self.subagent_controller = subagent_controller
        self.reader = reader
        self.components = components


# ═══════════════════════════════════════════════════════════
# TuiAssembly — 装配工厂
# ═══════════════════════════════════════════════════════════

class TuiAssembly:
    """ChatUI 子系统装配工厂。

    负责创建所有子组件实例并注入共享依赖。
    可通过继承或组合扩展自定义装配逻辑。
    """

    @staticmethod
    def assemble(
        on_display_messages: Callable | None = None,
    ) -> TuiAssemblyResult:
        """装配所有子系统。

        Args:
            on_display_messages: 消息显示回调（可选）。

        Returns:
            包含所有子组件的 TuiAssemblyResult。
        """
        from src.tui._bottom_bar import _BottomBar
        from src.tui._input import Input
        from src.tui._renderer import TuiEngine, TuiRenderer, EventDispatcher
        from src.tui._completion import _CmplHandler
        from src.tui.state.render_state import ChatRenderState
        from src.tui.consumer.chat_config import ChatConfig
        from src.tui._cursor_tracker import CursorTracker
        from src.tui._completion_engine import CompletionEngine
        from src.config.defaults import INPUT_HISTORY_FILE
        from rich.console import Console
        from src.renderer.output import OutputAdapter
        from src.terminal import get_safe_console_config
        from src.tui._animator import AnimatorContext
        from src.tui._screen import TerminalWidthCache
        from src.tui._config import TuiConfig

        # ── 框架基础设施 ──
        console = Console(**get_safe_console_config(), file=sys.__stdout__)
        output_adapter = OutputAdapter(console)

        # ── 创建共享依赖实例 ──
        animator = AnimatorContext.get_default()
        width_cache = TerminalWidthCache.get_default()
        tui_config = TuiConfig.defaults()

        # ── 聊天域子系统 ──
        rs: "ChatRenderState" = ChatRenderState()
        cursor_tracker = CursorTracker()
        bb: "_BottomBar" = _BottomBar(
            cursor_tracker=cursor_tracker,
            animator=animator,
            width_cache=width_cache,
        )

        # ── 统一输入管理 ──
        input_instance: "Input" = Input(
            fd=sys.stdin.fileno(),
            history_file=INPUT_HISTORY_FILE,
            cursor_tracker=cursor_tracker,
        )
        bb.set_input(input_instance)

        # ── 框架组件 ──
        renderer: "TuiRenderer" = TuiRenderer(
            rs, output_adapter, bb,
            on_display_messages=on_display_messages,
            cursor_tracker=cursor_tracker,
        )
        engine: "TuiEngine" = TuiEngine(
            renderer, bb,
            cursor_tracker=cursor_tracker,
            input_instance=input_instance,
            config=tui_config,
        )

        # ── 聊天域装配 ──
        chat_config = ChatConfig.defaults()
        dispatcher: "EventDispatcher" = EventDispatcher(
            push_cmd=engine.push_cmd,
            filter_fn=lambda source: source == "agent" or (source or "").startswith("agent-"),
            main_label=chat_config.main_label,
            max_error_length=tui_config.max_error_length,
        )
        rs.set_output_adapter(output_adapter)
        cmpl_handler: "_CmplHandler" = _CmplHandler(
            bb, CompletionEngine(),
            request_redraw=engine.request_bottom_redraw,
        )

        # 连接 SIGWINCH 重绘回调
        bb.set_request_redraw_cb(engine.request_bottom_redraw)

        # SubAgent 面板
        from src.tui._subagent_panel import SubAgentPanelController
        subagent_controller = SubAgentPanelController(
            push_cmd=engine.push_cmd,
        )

        # ── InputReader（可选） ──
        reader = None
        try:
            from src.tui._input_reader import InputReader
            reader = InputReader(fd=sys.stdin.fileno())
            input_instance.set_reader(reader)
            _logger.debug("InputReader 已创建")
        except Exception:
            _logger.debug("InputReader 创建失败，降级为直接 stdin 读取", exc_info=True)

        # ── 向后兼容的 _components 命名空间 ──
        from src.tui._consumer import _ComponentsNamespace
        components = _ComponentsNamespace(input_instance)

        return TuiAssemblyResult(
            rs=rs, engine=engine, bb=bb, dispatcher=dispatcher,
            renderer=renderer, cmpl_handler=cmpl_handler,
            input_instance=input_instance,
            subagent_controller=subagent_controller,
            reader=reader, components=components,
        )


__all__ = ["TuiAssembly", "TuiAssemblyResult"]
