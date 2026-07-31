"""TuiAssembly — ChatUIConsumer 子系统装配工厂。

从 ChatUIConsumer._assemble() 提取为独立类，单一职责：
创建并注入所有子组件依赖。

设计原则：
  - 所有依赖通过构造器显式创建，消除 get_default() 隐式调用
  - 返回 TuiAssemblyResult dataclass 包含所有组件
  - 无生命周期管理职责（由 TuiLifecycle 承担）

方向B 步骤3（2026-07-31）：
  - ``_ComponentsNamespace`` 迁出至 ``_components.py``，消除 ``_consumer ↔ _assembly`` 循环
    （_consumer 模块级 import _assembly；_assembly 从 _components 导入，不再触碰 _consumer）
  - 函数内 import 除真实循环依赖外全部上提为模块级（P3-5 措辞修正：
    ChatConfig 保留函数内 import 为真实循环依赖，见下）
  - ``assemble()`` 拆分为 5 个私有工厂方法（基础设施 / 共享依赖 / 聊天域基础 / 框架 / 聊天域组装），
    组件创建顺序与依赖注入关系与旧版完全一致

维护警示（P3-5 追加）：本模块被 ``src.tui.consumer`` 包 __init__ 间接依赖
（consumer/__init__ → _consumer → _assembly），且 `_assembly` 处于
`src.tui` 包部分初始化阶段可导入的路径上——**新增被 _assembly 依赖的模块
时须保持 Layer 0 / 无父包依赖**（不得依赖 `src.tui` 包内尚未完成初始化的
模块或经包 __init__ 回环），否则会破坏 `src.tui` 包部分初始化。
"""

from __future__ import annotations

import sys
from typing import Callable

# ── 模块级导入（方向B 步骤3：函数内 import 上提；P3-5 措辞修正） ──────────
# 注意：src.tui.consumer.chat_config 的 ChatConfig 保留函数内 import——
# 触发原因：src.tui.consumer/__init__.py 模块级 import _consumer（ChatUIConsumer），
# 而 _consumer 模块级 import _assembly；若 _assembly 模块级 import consumer.chat_config
# 会经包 __init__ 构成 _assembly → consumer → _consumer → _assembly 环（见 2026-07-31 方向B 步骤3）。
from src.tui._bottom_bar import _BottomBar
from src.tui._input import Input
from src.tui._renderer import TuiEngine, TuiRenderer, EventDispatcher
from src.tui._completion import _CmplHandler
from src.tui.state.render_state import ChatRenderState
from src.tui._cursor_tracker import CursorTracker
from src.tui._completion_engine import CompletionEngine
from src.tui._output import RenderOutput
from src.tui._stdout_tracker import _StdoutLineTracker
from src.tui._animator import AnimatorContext
from src.tui._screen import TerminalWidthCache
from src.tui._const import is_agent_source
from src.tui._config import TuiConfig
from src.tui._subagent_panel import SubAgentPanelController
from src.tui._components import _ComponentsNamespace
from src.config.defaults import INPUT_HISTORY_FILE
from rich.console import Console
from src.renderer.output import OutputAdapter
from src.terminal import get_safe_console_config


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
        'components',
    )

    def __init__(
        self,
        rs=None, engine=None, bb=None, dispatcher=None,
        renderer=None, cmpl_handler=None, input_instance=None,
        subagent_controller=None, components=None,
    ):
        self.rs = rs
        self.engine = engine
        self.bb = bb
        self.dispatcher = dispatcher
        self.renderer = renderer
        self.cmpl_handler = cmpl_handler
        self.input_instance = input_instance
        self.subagent_controller = subagent_controller
        self.components = components


# ═══════════════════════════════════════════════════════════
# TuiAssembly — 装配工厂
# ═══════════════════════════════════════════════════════════

class TuiAssembly:
    """ChatUI 子系统装配工厂。

    负责创建所有子组件实例并注入共享依赖。
    可通过继承或组合扩展自定义装配逻辑。

    方向B 步骤3（2026-07-31）：``assemble()`` 已拆分为 5 个私有工厂方法，
    按依赖顺序串联：

      - ``_create_infrastructure`` → console / output_adapter / render_output / line_tracker
      - ``_create_shared`` → animator / width_cache / tui_config / rs / cursor_tracker
      - ``_create_chat_domain`` → bb / input_instance（聊天域基础，不依赖 engine）
      - ``_create_framework`` → renderer / engine（依赖 bb / input_instance）
      - ``_create_chat_domain_assembly`` → chat_config / dispatcher / cmpl_handler / subagent_controller
        （聊天域组装，依赖 engine 的 push_cmd / request_bottom_redraw）

    工厂方法的组件创建顺序与依赖注入关系保持旧版 ``assemble()`` 完全一致。
    """

    @staticmethod
    def _create_infrastructure():
        """创建框架基础设施：console → output_adapter → render_output → line_tracker。"""
        console = Console(**get_safe_console_config(), file=sys.__stdout__)
        output_adapter = OutputAdapter(console)
        # 统一输出端口：装饰 OutputAdapter，叠加受控紧急路径 + 显式行跟踪
        render_output = RenderOutput(output_adapter)
        # 显式行跟踪器（不再全局劫持 sys.__stdout__）：内容写回调 + scroll_end
        line_tracker = _StdoutLineTracker(sys.__stdout__)
        render_output.set_line_tracker(line_tracker)
        return console, output_adapter, render_output, line_tracker

    @staticmethod
    def _create_shared():
        """创建共享依赖实例：animator / width_cache / tui_config / rs / cursor_tracker。"""
        animator = AnimatorContext.get_default()
        width_cache = TerminalWidthCache.get_default()
        tui_config = TuiConfig.defaults()
        rs: "ChatRenderState" = ChatRenderState()
        cursor_tracker = CursorTracker()
        return animator, width_cache, tui_config, rs, cursor_tracker

    @staticmethod
    def _create_chat_domain(cursor_tracker, animator, width_cache, line_tracker):
        """创建聊天域基础组件：bb + input_instance（不依赖 engine）。"""
        bb: "_BottomBar" = _BottomBar(
            cursor_tracker=cursor_tracker,
            animator=animator,
            width_cache=width_cache,
        )
        bb.set_tracker(line_tracker)
        # ── 统一输入管理 ──
        input_instance: "Input" = Input(
            fd=sys.stdin.fileno(),
            history_file=INPUT_HISTORY_FILE,
            cursor_tracker=cursor_tracker,
        )
        bb.set_input(input_instance)
        return bb, input_instance

    @staticmethod
    def _create_framework(
        rs, output_adapter, bb, on_display_messages,
        cursor_tracker, render_output, input_instance, tui_config,
    ):
        """创建框架组件：renderer → engine（依赖 bb / input_instance）。"""
        renderer: "TuiRenderer" = TuiRenderer(
            rs, output_adapter, bb,
            on_display_messages=on_display_messages,
            cursor_tracker=cursor_tracker,
            render_output=render_output,
        )
        engine: "TuiEngine" = TuiEngine(
            renderer, bb,
            cursor_tracker=cursor_tracker,
            input_instance=input_instance,
            config=tui_config,
            render_output=render_output,
        )
        # 统一输出管线：渲染状态（reasoning/content）共享 RenderOutput
        # （装饰 OutputAdapter + 显式行跟踪回调）
        rs.set_output_adapter(render_output)
        return renderer, engine

    @staticmethod
    def _create_chat_domain_assembly(tui_config, engine, bb):
        """创建聊天域组装组件（依赖 engine）：dispatcher / cmpl_handler / subagent_controller。

        P3-5：返回值 ``chat_config`` 无消费方（仅用于构造 dispatcher），
        改为局部变量（不再返回）。
        """
        # 函数内 import：ChatConfig 经 src.tui.consumer.chat_config 导入会触发
        # consumer/__init__.py → _consumer → _assembly 循环（见模块头注释，
        # P3-5 措辞修正：真实循环依赖，保留函数内 import）
        from src.tui.consumer.chat_config import ChatConfig
        chat_config = ChatConfig.defaults()
        dispatcher: "EventDispatcher" = EventDispatcher(
            push_cmd=engine.push_cmd,
            filter_fn=is_agent_source,
            main_label=chat_config.main_label,
            max_error_length=tui_config.max_error_length,
        )
        cmpl_handler: "_CmplHandler" = _CmplHandler(
            bb, CompletionEngine(),
            request_redraw=engine.request_bottom_redraw,
        )
        # 连接 SIGWINCH 重绘回调
        bb.set_request_redraw_cb(engine.request_bottom_redraw)
        # SubAgent 面板
        subagent_controller = SubAgentPanelController(
            push_cmd=engine.push_cmd,
        )
        return dispatcher, cmpl_handler, subagent_controller

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
        # ── 框架基础设施 ──
        _, output_adapter, render_output, line_tracker = (
            TuiAssembly._create_infrastructure()
        )

        # ── 创建共享依赖实例 ──
        animator, width_cache, tui_config, rs, cursor_tracker = (
            TuiAssembly._create_shared()
        )

        # ── 聊天域基础组件（bb + input_instance） ──
        bb, input_instance = TuiAssembly._create_chat_domain(
            cursor_tracker, animator, width_cache, line_tracker,
        )

        # ── 框架组件（renderer + engine） ──
        renderer, engine = TuiAssembly._create_framework(
            rs, output_adapter, bb, on_display_messages,
            cursor_tracker, render_output, input_instance, tui_config,
        )

        # ── 聊天域组装（dispatcher + cmpl_handler + subagent_controller） ──
        dispatcher, cmpl_handler, subagent_controller = (
            TuiAssembly._create_chat_domain_assembly(tui_config, engine, bb)
        )

        # ── 向后兼容的 _components 命名空间 ──
        components = _ComponentsNamespace(input_instance)

        return TuiAssemblyResult(
            rs=rs, engine=engine, bb=bb, dispatcher=dispatcher,
            renderer=renderer, cmpl_handler=cmpl_handler,
            input_instance=input_instance,
            subagent_controller=subagent_controller,
            components=components,
        )


__all__ = ["TuiAssembly", "TuiAssemblyResult"]
