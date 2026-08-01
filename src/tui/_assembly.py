"""TuiAssembly — ChatUIConsumer 子系统装配工厂（ink 渲染模型）。

创建 InkSession + AppModel + InkBridge + 组件树，替代
TuiEngine/TuiRenderer/_BottomBar/ChatRenderState 装配。

返回 TuiAssemblyResult 保持全部 slot 字段（兼容消费者/测试访问路径）：
  - rs            → AppModel
  - engine        → InkSession
  - bb            → InkBridge（_BottomBar 兼容桥）
  - dispatcher    → EventDispatcher(push_cmd=session.push_cmd)
  - renderer      → _InkRendererFacade（output_adapter 兼容占位）
  - cmpl_handler  → _CmplHandler(InkBridge, CompletionEngine, request_redraw)
  - input_instance→ Input
  - subagent_controller → SubAgentPanelController(push_cmd=session.push_cmd)
  - components    → _ComponentsNamespace(input)

维护警示（P3-5 追加）：本模块被 ``src.tui.consumer`` 包 __init__ 间接依赖，
新增被 _assembly 依赖的模块时须保持 Layer 0 / 无父包依赖。
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

from src.tui._input import Input
from src.tui._completion import _CmplHandler
from src.tui._completion_engine import CompletionEngine
from src.tui._ink_bridge import InkBridge
from src.tui._stdout_tracker import _StdoutLineTracker
from src.tui._screen import register_sigwinch_callback
from src.tui._const import is_agent_source
from src.tui._config import TuiConfig
from src.tui._subagent_panel import SubAgentPanelController
from src.tui._components import _ComponentsNamespace
from src.config.defaults import INPUT_HISTORY_FILE
from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui.app.app import build_app_element
from src.tui.ink.session import InkSession
from src.tui._dispatcher import EventDispatcher

_logger = logging.getLogger(__name__)


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


class _InkRendererFacade:
    """旧 renderer 兼容面（output_adapter 占位）。

    非全屏流动模型无 OutputAdapter；output_adapter 返回 None（无生产消费方）。
    """

    __slots__ = ("_session",)

    def __init__(self, session):
        self._session = session

    @property
    def output_adapter(self):
        return None


# ═══════════════════════════════════════════════════════════
# TuiAssembly — 装配工厂
# ═══════════════════════════════════════════════════════════

class TuiAssembly:
    """ChatUI 子系统装配工厂（ink 渲染模型）。"""

    @staticmethod
    def _create_infrastructure():
        """创建基础设施：line_tracker（输出历史）。"""
        line_tracker = _StdoutLineTracker(sys.__stdout__)
        # 非全屏模型无 DECSTBM：scroll_end 置大值使完整行跟踪生效
        line_tracker.set_scroll_end(10**9)
        return line_tracker

    @staticmethod
    def _create_shared():
        """创建共享依赖：model / config / session。"""
        tui_config = TuiConfig.defaults()
        model = AppModel()
        return tui_config, model

    @staticmethod
    def _create_chat_domain():
        """创建输入实例。"""
        input_instance = Input(
            fd=sys.stdin.fileno(),
            history_file=INPUT_HISTORY_FILE,
        )
        return input_instance

    @staticmethod
    def _create_framework(model, tui_config, line_tracker, input_instance):
        """创建框架：session + bridge + renderer facade。"""
        session = InkSession(
            model=model,
            apply_cmd=apply_cmd,
            build_tree=build_app_element,
            config=tui_config,
        )
        # 输出历史：新增内容行回调 tracker.track
        session._ink_renderer.set_line_callback(line_tracker.track)
        # ★ 注入 Input：render 循环的 _phase_process_input 需调用 process_events()
        #   读取 stdin——未注入则输入完全无效（用户无法输入）。
        session.set_input(input_instance)
        # 输入 echo → 模型输入状态
        input_instance.set_echo_callback(session.update_input)
        # 方向D 步骤14：Ctrl+R 反向历史搜索（配置门控，默认 False 保持 switch_model）
        input_instance.set_reverse_search_enabled(tui_config.reverse_search_enabled)
        input_instance.set_reverse_search_callback(
            _make_reverse_search_cb(model, session)
        )
        # 方向D 步骤16：Esc 取消输入（配置门控，默认 False 保持中断语义）；
        # 活跃状态回调（生成中不取消输入，走既有中断）
        input_instance.set_esc_cancel_input(tui_config.esc_cancel_input)
        input_instance.set_active_status_callback(_make_active_status_cb(model))
        # SIGWINCH → 刷新宽度 + 重绘
        register_sigwinch_callback(_make_sigwinch_cb(session))
        bridge = InkBridge(model, session)
        renderer = _InkRendererFacade(session)
        return session, bridge, renderer

    @staticmethod
    def _create_chat_domain_assembly(tui_config, session, bridge):
        """创建聊天域组装：dispatcher / cmpl_handler / subagent_controller。"""
        from src.tui.consumer.chat_config import ChatConfig
        chat_config = ChatConfig.defaults()
        dispatcher = EventDispatcher(
            push_cmd=session.push_cmd,
            filter_fn=is_agent_source,
            main_label=chat_config.main_label,
            max_error_length=tui_config.max_error_length,
        )
        cmpl_handler = _CmplHandler(
            bridge, CompletionEngine(),
            request_redraw=session.request_bottom_redraw,
        )
        subagent_controller = SubAgentPanelController(
            push_cmd=session.push_cmd,
        )
        return dispatcher, cmpl_handler, subagent_controller

    @staticmethod
    def assemble(
        on_display_messages: Callable | None = None,
    ) -> TuiAssemblyResult:
        """装配所有子系统（ink 渲染模型）。"""
        line_tracker = TuiAssembly._create_infrastructure()
        tui_config, model = TuiAssembly._create_shared()
        input_instance = TuiAssembly._create_chat_domain()
        session, bridge, renderer = TuiAssembly._create_framework(
            model, tui_config, line_tracker, input_instance,
        )
        dispatcher, cmpl_handler, subagent_controller = (
            TuiAssembly._create_chat_domain_assembly(tui_config, session, bridge)
        )
        components = _ComponentsNamespace(input_instance)

        return TuiAssemblyResult(
            rs=model, engine=session, bb=bridge, dispatcher=dispatcher,
            renderer=renderer, cmpl_handler=cmpl_handler,
            input_instance=input_instance,
            subagent_controller=subagent_controller,
            components=components,
        )


def _make_sigwinch_cb(session):
    """构建 SIGWINCH 回调（刷新宽度 + 请求重绘）。"""

    def _on_sigwinch(cols, rows):
        # P2-9：不再裸吞异常——记录 debug 日志（SIGWINCH 刷新异常属非关键
        # 降级，不阻断信号处理）。
        try:
            session._width_cache.force_refresh()
            session.request_bottom_redraw()
        except Exception:
            _logger.debug("SIGWINCH 刷新异常", exc_info=True)

    return _on_sigwinch


def _make_reverse_search_cb(model, session):
    """构建反向历史搜索状态同步回调（更新 model.history_search + 重绘）。

    方向D 步骤14：InputDispatcher 在 render 线程调用本回调，将搜索状态写入
    AppModel 供 input-area 渲染搜索覆盖行；退出搜索（active=False）时置 None。
    """

    def _cb(query, matches, index, active):
        if active:
            from src.tui.app.model import HistorySearchState
            model.history_search = HistorySearchState(
                query=query, matches=matches, index=index, active=True,
            )
        else:
            model.history_search = None
        session.request_bottom_redraw()

    return _cb


def _make_active_status_cb(model):
    """构建活跃状态回调（方向D 步骤16：Esc 取消输入判定用）。

    返回 ``lambda: model.status.status_active``——生成中（True）时 Esc 不取消
    输入（走既有中断）；空闲（False）时若启用且缓冲非空则清空输入取消编辑。
    """

    def _cb():
        return model.status.status_active

    return _cb


__all__ = ["TuiAssembly", "TuiAssemblyResult"]
