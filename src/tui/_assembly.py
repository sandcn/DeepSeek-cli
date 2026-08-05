"""TuiAssembly — ChatUIConsumer 子系统装配工厂（ink 渲染模型）。

创建 InkSession + AppModel + InkBridge + 组件树，替代
TuiEngine/TuiRenderer/_BottomBar/ChatRenderState 装配。

返回 TuiAssemblyResult 保持全部 slot 字段（兼容消费者/测试访问路径）：
  - rs            → AppModel
  - engine        → InkSession
  - bb            → InkBridge（_BottomBar 兼容桥）
  - dispatcher    → EventDispatcher(push_cmd=session.push_cmd)
  - renderer      → InkRenderer（session._ink_renderer，output_adapter 恒 None）
  - cmpl_handler  → _CmplHandler(InkBridge, CompletionEngine, request_redraw)
  - input_instance→ Input
  - subagent_controller → SubAgentPanelController(push_cmd=session.push_cmd)
  - components    → _ComponentsNamespace(input)

装配层重构（2026-08-05）：五个 ``_create_*`` 装配子步骤实现已迁至独立模块
``_assembly_steps.py``（模块级函数，惰性 import 各自依赖）——本模块瘦身为
「结果容器 + assemble() 编排 + 兼容转发」（``_create_chat_domain`` 等转发
保留，测试/外部调用面兼容）。输出历史接线已收敛为 ``InkSession.set_line_tracker``
公开方法（不再直写 session 私有字段）。

维护警示（P3-5 追加）：本模块被 ``src.tui.consumer`` 包 __init__ 间接依赖，
新增被 _assembly 依赖的模块时须保持 Layer 0 / 无父包依赖。
"""

from __future__ import annotations

from src.tui import _assembly_steps
from src.tui._components import _ComponentsNamespace


# ═══════════════════════════════════════════════════════════
# TuiAssemblyResult — 装配结果数据类
# ═══════════════════════════════════════════════════════════

class TuiAssemblyResult:
    """ChatUI 子系统装配结果容器。"""

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


class TuiAssembly:
    """ChatUI 子系统装配工厂（ink 渲染模型）——薄编排器。

    装配步骤实现委托 ``_assembly_steps``（模块级函数）；``_create_*`` 静态
    方法保留为**兼容转发**（既有测试/外部调用面经 ``TuiAssembly._create_*``
    访问路径不变）。
    """

    @staticmethod
    def _create_infrastructure():
        """创建基础设施：line_tracker（转发 _assembly_steps）。"""
        return _assembly_steps.create_infrastructure()

    @staticmethod
    def _create_shared():
        """创建共享依赖：model / config（转发 _assembly_steps）。"""
        return _assembly_steps.create_shared()

    @staticmethod
    def _create_chat_domain():
        """创建输入实例（转发 _assembly_steps；含无 TTY 兜底）。"""
        return _assembly_steps.create_chat_domain()

    @staticmethod
    def _create_framework(model, tui_config, line_tracker, input_instance):
        """创建框架：session + bridge + renderer（转发 _assembly_steps）。"""
        return _assembly_steps.create_framework(
            model, tui_config, line_tracker, input_instance,
        )

    @staticmethod
    def _create_chat_domain_assembly(tui_config, session, bridge):
        """创建聊天域组装（转发 _assembly_steps）。"""
        return _assembly_steps.create_chat_domain_assembly(
            tui_config, session, bridge,
        )

    @staticmethod
    def assemble() -> TuiAssemblyResult:
        """装配所有子系统（ink 渲染模型）。

        方向3 步骤16：移除 ``on_display_messages`` 死参数——显示路径已统一由
        ``DisplayMsgsCmd → apply._do_display_messages`` 承载，无回调注入需求。
        装配步骤实现见 ``_assembly_steps``（2026-08-05 装配层重构）。
        """
        line_tracker = _assembly_steps.create_infrastructure()
        tui_config, model = _assembly_steps.create_shared()
        input_instance = _assembly_steps.create_chat_domain()
        session, bridge, renderer = _assembly_steps.create_framework(
            model, tui_config, line_tracker, input_instance,
        )
        dispatcher, cmpl_handler, subagent_controller = (
            _assembly_steps.create_chat_domain_assembly(
                tui_config, session, bridge,
            )
        )
        components = _ComponentsNamespace(input_instance)

        return TuiAssemblyResult(
            rs=model, engine=session, bb=bridge, dispatcher=dispatcher,
            renderer=renderer, cmpl_handler=cmpl_handler,
            input_instance=input_instance,
            subagent_controller=subagent_controller,
            components=components,
        )


__all__ = ["TuiAssembly", "TuiAssemblyResult"]
