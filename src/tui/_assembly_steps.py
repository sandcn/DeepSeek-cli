"""_assembly_steps — TuiAssembly 装配子步骤（独立模块，2026-08-05 重构）。

装配层重构：将 ``TuiAssembly`` 的五个 ``_create_*`` 步骤实现迁出为**模块级
函数**（独立模块）——装配工厂（``_assembly.py``）瘦身为「结果容器 +
assemble() 编排 + 兼容转发」，各装配步骤在独立模块中按需惰性 import 自身
依赖（模块级 import 面最小化）。

步骤划分（对应原 TuiAssembly._create_* 职责，行为零变化）：
  - ``create_infrastructure``        — line_tracker（输出历史）
  - ``create_shared``                — (tui_config, model)
  - ``create_chat_domain``           — Input 输入实例（含无 TTY 兜底）
  - ``create_framework``             — (session, bridge, renderer)
  - ``create_chat_domain_assembly``  — (dispatcher, cmpl_handler, subagent_controller)

辅助回调工厂（``_make_reverse_search_cb`` / ``_make_active_status_cb`` /
``_make_sigwinch_cb`` + ``_active_session``）随步骤迁至本模块。

依赖约束：本模块为装配专用（Layer 0 / 无父包依赖），函数内惰性 import。
"""

from __future__ import annotations

import logging
import sys

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 装配子步骤（模块级函数）
# ═══════════════════════════════════════════════════════════

def create_infrastructure():
    """创建基础设施：line_tracker（输出历史）。"""
    from src.tui._stdout_tracker import _StdoutLineTracker
    line_tracker = _StdoutLineTracker(sys.__stdout__)
    # 非全屏模型无 DECSTBM：scroll_end 置大值使完整行跟踪生效
    line_tracker.set_scroll_end(10**9)
    return line_tracker


def create_shared():
    """创建共享依赖：model / config。"""
    from src.tui._config import TuiConfig
    from src.tui.app.model import AppModel
    tui_config = TuiConfig.defaults()
    model = AppModel()
    return tui_config, model


def create_chat_domain():
    """创建输入实例。

    方向2（无 TTY 兜底）：CI/管道/测试环境 stdin 无 ``fileno()``（抛
    io.UnsupportedOperation/AttributeError）——回退 ``fd=0``（无数据即
    返回），装配不崩溃（修复前直接崩溃）。
    """
    from src.tui._input import Input
    from src.config.defaults import INPUT_HISTORY_FILE
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        fd = 0
        _logger.warning("stdin 无 fileno（无 TTY 环境），回退 fd=0")
    input_instance = Input(
        fd=fd,
        history_file=INPUT_HISTORY_FILE,
    )
    return input_instance


def create_framework(model, tui_config, line_tracker, input_instance):
    """创建框架：session + bridge + renderer。"""
    from src.tui.ink.session import InkSession
    from src.tui.app.apply import apply_cmd
    from src.tui.app.app import build_app_element
    from src.tui._ink_bridge import InkBridge
    from src.tui._screen import register_sigwinch_callback

    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=tui_config,
    )
    # ★ 2026-08-05 装配层重构：输出历史接线收敛为公开方法 set_line_tracker
    #   （取代对 ``session._ink_renderer.set_line_callback`` + 私有字段
    #   ``session._line_tracker`` 的直写）——TuiLifecycle stop 流程经
    #   ``session._line_tracker`` 调用 close()（flush 剩余行 + 停止 daemon
    #   刷盘定时器）。
    session.set_line_tracker(line_tracker)
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
    # Claude TUI parity 步骤 3.1：Ctrl+L 清屏（session.clear_screen；
    # 未注入时 dispatcher 记 debug 跳过，测试兼容）
    input_instance.set_clear_screen_callback(session.clear_screen)
    # SIGWINCH → 刷新宽度 + 重绘
    register_sigwinch_callback(_make_sigwinch_cb(session))
    bridge = InkBridge(model, session)
    # ★ 2026-08-05 死代码清理：renderer slot 直接指向 session 的真实
    #   渲染器（InkRenderer）——旧 ``_InkRendererFacade`` 占位类已删除
    #   （其唯一职责 output_adapter 恒 None 无生产消费方，ChatUIConsumer.
    #   output_adapter 改为直接返回 None）。
    renderer = session._ink_renderer
    return session, bridge, renderer


def create_chat_domain_assembly(tui_config, session, bridge):
    """创建聊天域组装：dispatcher / cmpl_handler / subagent_controller。"""
    from src.tui.consumer.chat_config import ChatConfig
    from src.tui._completion import _CmplHandler
    from src.tui._completion_engine import CompletionEngine
    from src.tui._dispatcher import EventDispatcher
    from src.tui._const import is_agent_source
    from src.tui.subagent import SubAgentPanelController  # 聚合门面统一入口

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
    subagent_controller = SubAgentPanelController.get_default()
    # ★ 方向5（单例统一）：装配复用单例并注入 push_cmd 回调（消除双实例
    #   ——事件订阅/状态在单例上累积，不因装配重建丢失）。
    subagent_controller.set_push_cmd(session.push_cmd)
    return dispatcher, cmpl_handler, subagent_controller


# ═══════════════════════════════════════════════════════════
# 辅助回调工厂
# ═══════════════════════════════════════════════════════════

#: 当前活动会话（SIGWINCH 回调读取；``_make_sigwinch_cb`` 更新）。
#:   方向1 修复：回调使用**稳定模块级函数**（身份恒定），``assemble`` 时仅
#:   更新本引用——修复前每次 assemble 创建新闭包，``register_sigwinch_callback``
#:   按身份去重（``cb not in _sigwinch_callbacks``）失败，旧闭包越积越多，
#:   每次 resize 触发 N 个回调且持陈旧 session 引用（内存泄漏）。
#:   ★ P3-7（全局引用限制）：模块级可变引用——多 TUI 实例并存时不支持各自
#:     独立 SIGWINCH（最后一个 ``assemble`` 的会话持有回调，早期实例 resize
#:     时刷新错误的 session）；当前生产为单实例生命周期（app_loop 顺序装配/
#:     停止），可接受，不做多实例支持。
_active_session = None


def _sigwinch_cb_impl(cols, rows):
    """稳定 SIGWINCH 回调实现（模块级函数，身份恒定可去重）。"""
    session = _active_session
    if session is None:
        return
    # P2-9：不再裸吞异常——记录 debug 日志（SIGWINCH 刷新异常属非关键
    # 降级，不阻断信号处理）。
    try:
        session._width_cache.force_refresh()
        session.request_bottom_redraw()
    except Exception:
        _logger.debug("SIGWINCH 刷新异常", exc_info=True)


def _make_sigwinch_cb(session):
    """记录活动会话并返回稳定回调（模块级函数，身份恒定）。"""
    global _active_session
    _active_session = session
    return _sigwinch_cb_impl


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


__all__ = [
    "create_infrastructure",
    "create_shared",
    "create_chat_domain",
    "create_framework",
    "create_chat_domain_assembly",
    "_make_reverse_search_cb",
    "_make_active_status_cb",
    "_make_sigwinch_cb",
    "_active_session",
]
