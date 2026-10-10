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

辅助回调工厂（``_make_reverse_search_cb`` / ``_make_active_status_cb``）随步骤
迁至本模块。SIGWINCH 回调为 ``InkSession._on_sigwinch`` 实例方法（架构改进
方向 C：装配层经 ``register_sigwinch_callback(cb, token=session)`` 注册，
stop 时注销——替代旧模块级 ``_active_session`` 全局引用）。

依赖约束：本模块为装配专用（Layer 0 / 无父包依赖），函数内惰性 import。
"""

from __future__ import annotations

import logging
import sys
import time

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
    tui_config = TuiConfig.defaults().with_overrides(
        drop_path_normalize=_rc_drop_path_normalize(),
        bracketed_paste=_rc_bracketed_paste(),
    )
    model = AppModel()
    return tui_config, model


def _rc_drop_path_normalize() -> bool:
    """读取 RC 配置 ``tui_drop_path_normalize``（拖放路径规范化开关）。

    配置读取失败（RC 不可用/键缺失）时回退 ``TuiConfig`` 默认值——装配
    不因配置层异常而中断。
    """
    from src.tui._config import TuiConfig
    try:
        from src.config import TUI_DROP_PATH_NORMALIZE
        return bool(TUI_DROP_PATH_NORMALIZE)
    except Exception:
        _logger.debug("读取 tui_drop_path_normalize 失败，使用默认值", exc_info=True)
        return TuiConfig.defaults().drop_path_normalize


def _rc_bracketed_paste() -> bool:
    """读取 RC 配置 ``tui_bracketed_paste``（括号粘贴模式开关）。

    配置读取失败（RC 不可用/键缺失）时回退 ``TuiConfig`` 默认值。
    """
    from src.tui._config import TuiConfig
    try:
        from src.config import TUI_BRACKETED_PASTE
        return bool(TUI_BRACKETED_PASTE)
    except Exception:
        _logger.debug("读取 tui_bracketed_paste 失败，使用默认值", exc_info=True)
        return TuiConfig.defaults().bracketed_paste


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


def _placeholder_fading(session) -> bool:
    """输入区占位符是否处于渐显期（时间驱动内容，须逐帧渲染）。

    占位符渐显状态记录在输入区 fiber 的 ``_placeholder_fade_key``
    （``(占位符文本, 起始 monotonic)``，见 ``app/_popup_builder``）——渐显期
    （elapsed < fade_duration）内每帧颜色推进，不可复用上一帧。
    """
    fiber = getattr(session, "_input_fiber", None)
    key = getattr(fiber, "_placeholder_fade_key", None)
    if not key:
        return False
    try:
        from src.tui.app import _fx
        duration = _fx._default_fx_params()[0]
        return duration > 0 and (time.monotonic() - key[1]) < duration
    except Exception:
        _logger.debug("占位符渐显判定异常（按活跃处理）", exc_info=True)
        return True


def _make_idle_frame_predicate(model, session):
    """构造「空闲帧复用」预测函数（保守判定，2026-10-07 性能优化）。

    仅在**所有**时间驱动显示源都不活跃时返回 True（无流式状态/工具运行/
    解析进度/补全弹窗/模态视图/子代理卡片/占位符渐显）；任一活跃或判定
    异常时返回 False → 本拍正常重建渲染（零视觉回归——无法确认静态时
    宁可多渲染一帧）。帧率不受影响：会话对外仍恒定 30Hz，仅跳过静态帧的
    组件树重建开销。
    """
    def _is_static() -> bool:
        try:
            status = getattr(model, "status", None)
            if status is not None and getattr(status, "status_active", False):
                return False
            if getattr(model, "parse_line", None) is not None:
                return False
            if getattr(model, "fullscreen", "") or getattr(model, "bottom_view", ""):
                return False
            if getattr(model, "history_search", None) is not None:
                return False
            if getattr(model, "user_selects", None):
                return False
            if getattr(model, "tool_boxes", None):
                return False
            if getattr(model, "subagent_lines", None):
                return False
            completion = getattr(model, "completion", None)
            if completion is not None and getattr(completion, "visible", False):
                return False
            if _placeholder_fading(session):
                return False
            return True
        except Exception:
            _logger.debug("空闲帧预测异常（本帧不复用）", exc_info=True)
            return False

    return _is_static


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
    # 拖放文件路径规范化（2026-10-07，用户需求：输入框支持拖动文件输入文件
    # 路径）——终端拖放注入的路径文本规范化后插入；RC 键
    # ``tui_drop_path_normalize``（默认开启）经 create_shared 注入 TuiConfig。
    input_instance.set_drop_path_normalize(tui_config.drop_path_normalize)
    # 括号粘贴（2026-10-07，TUI React Ink 改进：对齐官方 Ink v7）——启动时
    # 启用终端括号粘贴模式，粘贴内容以 ESC[200~ … ESC[201~ 整段到达
    # （不被拆成逐字符按键；多行粘贴不误触发 Enter）；suspend/stop 时还原、
    # resume 时重新启用（TerminalModeManager 幂等管理）。RC 键
    # ``tui_bracketed_paste``（默认开启）经 create_shared 注入 TuiConfig。
    session.set_terminal_modes(bracketed_paste=tui_config.bracketed_paste)
    # ★ 空闲帧复用（2026-10-07 性能优化）：注入「静态状态」预测——宿主静态
    #   时本拍复用上一帧（帧号照常推进，恒定 30Hz 节拍不变），省空转 CPU。
    session.set_idle_frame_predicate(_make_idle_frame_predicate(model, session))
    # Claude TUI parity 步骤 3.1：Ctrl+L 清屏（session.clear_screen；
    # 未注入时 dispatcher 记 debug 跳过，测试兼容）
    input_instance.set_clear_screen_callback(session.clear_screen)
    # ★ 2026-08-19：Ctrl+H 轨迹视图开关（DSH 风格左台账 + 右检查器）——
    #   翻转 model.fullscreen（"trace" ↔ ""）+ 请求重绘（非全屏流动模型文档
    #   随重绘重建；fullscreen 时 App 按全屏视图注册表整屏渲染 TraceView，
    #   不显示聊天消息区）。2026-08-17 通用化：toggle 由通用工厂
    #   ``_make_fullscreen_toggle_cb`` 构建（view_id 参数化，其他全屏视图
    #   可绑定其他快捷键复用）。
    input_instance.set_trace_toggle_callback(
        _make_trace_toggle_cb(model, session)
    )
    # ★ 2026-10-07：F1 / Ctrl+/ 帮助速查视图开关（模态全屏视图，通用工厂
    #   view_id="help"）——分组命令列表 + 快捷键速查；打开期间模态独占输入。
    input_instance.set_help_toggle_callback(
        _make_fullscreen_toggle_cb(model, session, "help")
    )
    # ★ 2026-10：F12 会话日志 / 投影浏览器视图开关（模态全屏视图）——直接
    #   翻转 model.fullscreen（**不经命令队列**：``/logs`` 命令在 AI 生成
    #   期间会被排队，F12 需在流式输出中也能立即打开），打开时经装配注入的
    #   ``model.logs_refresher`` 构建视图数据（视图渲染期继续按会话日志签名
    #   实时刷新）。
    input_instance.set_logs_toggle_callback(
        _make_logs_toggle_cb(model, session)
    )
    # ★ 2026-10：F11 文件变更审查器视图开关（模态全屏视图）——直接翻转
    #   model.fullscreen（**不经命令队列**：``/changes`` 命令在 AI 生成期间
    #   会被排队，F11 需在流式输出中也能立即打开），打开时经装配注入的
    #   ``model.sandbox_refresher`` 构建视图数据（视图渲染期继续按沙盒数据
    #   签名实时刷新）。
    input_instance.set_changes_toggle_callback(
        _make_changes_toggle_cb(model, session)
    )
    # SIGWINCH → 刷新宽度 + 重绘（架构改进方向 C：实例方法 + token 去重注册，
    # 替代旧模块级 ``_active_session`` 全局引用——多 TUI 实例各持自身回调，
    # stop 时由 session 注销，消除全局可变引用与陈旧会话刷新错乱）
    register_sigwinch_callback(session._on_sigwinch, token=session)
    bridge = InkBridge(model, session)
    # ★ 2026-08-05 死代码清理：renderer slot 直接指向 session 的真实
    #   渲染器（InkRenderer）——旧 ``_InkRendererFacade`` 占位类已删除
    #   （其唯一职责 output_adapter 恒 None 无生产消费方，ChatUIConsumer.
    #   output_adapter 改为直接返回 None）。
    renderer = session.renderer
    # ★ 2026-10-06（用户需求：启动时锚定「当前光标行 + 1」）：注入终端光标行
    #   查询——首个全量帧消费一次，把文档起始行锚定到光标下方（不再跳到屏幕
    #   底部）；非 TTY / 终端不支持 CPR / 超时时查询返回 None，自动回退
    #   「文档底部贴屏幕底部」的既有锚定（渲染正确性优先）。
    from src.tui._screen import query_cursor_row
    _set_provider = getattr(renderer, "set_cursor_row_provider", None)
    if _set_provider is not None:
        try:
            _set_provider(query_cursor_row)
        except Exception:
            _logger.debug("注入光标行查询失败", exc_info=True)
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
        bridge, CompletionEngine(async_mode=True),
        request_redraw=session.request_bottom_redraw,
    )
    # ★ 异步补全预热：后台线程加载命令/会话/模型/主题/配置键数据源——首次
    #   Tab 补全零等待；数据源未就绪时补全弹窗显示「加载中…」占位，就绪后
    #   经 _CmplHandler 监听器动态刷新（见 src/tui/_completion.py）。
    engine = cmpl_handler._engine
    warmup = getattr(engine, "warmup", None)
    if callable(warmup):
        try:
            warmup()
        except Exception:
            _logger.debug("补全数据源预热失败", exc_info=True)
    subagent_controller = SubAgentPanelController.get_default()
    # ★ 方向5（单例统一）：装配复用单例并注入 push_cmd 回调（消除双实例
    #   ——事件订阅/状态在单例上累积，不因装配重建丢失）。
    subagent_controller.set_push_cmd(session.push_cmd)
    return dispatcher, cmpl_handler, subagent_controller


# ═══════════════════════════════════════════════════════════
# 辅助回调工厂
# ═══════════════════════════════════════════════════════════

# ★ 架构改进方向 C（2026-08-16）：模块级 ``_active_session`` 全局引用已删除
#   ——SIGWINCH 回调收敛为 ``InkSession._on_sigwinch`` 实例方法，装配层经
#   ``register_sigwinch_callback(cb, token=session)`` 按 token 去重注册，
#   ``InkSession.stop()`` 注销。多 TUI 实例各持自身回调互不干扰，消除
#   P3-7 已知限制（最后一个 assemble 的会话持有回调、早期实例 resize 时
#   刷新错误的 session）。

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


def _make_fullscreen_toggle_cb(model, session, view_id: str):
    """构建**通用**模态全屏视图开关回调（翻转 model.fullscreen + 重绘）。

    2026-08-17（用户需求：轨迹 Trace 输入接管通用化）：通用工厂——传入
    view_id 即可为任意全屏视图构建「快捷键开关」回调：已在该视图时关闭
    （置 ""），否则切换到该视图。InputDispatcher 在 render 线程调用——
    翻转 ``model.fullscreen`` 并请求重绘（App 据此在正常界面与全屏视图间
    切换；整屏渲染 / 模态输入接管 / 光标隐藏全部由通用机制自动生效）。

    ★ 窗口约束（2026-08-17 review 方向）：回调置位 fullscreen 后，router
    在下一帧渲染（reconciler 每帧重建）生效——当前帧内剩余输入仍走旧
    router（≤1 帧渲染周期 ≈100ms，架构固有窗口，与所有状态切换一致）。

    Args:
        model: AppModel 实例。
        session: InkSession（request_bottom_redraw）。
        view_id: 目标全屏视图 id（如 "trace"——须在 ``FULLSCREEN_VIEWS``
            注册表存在；未注册时记 warning 仍执行——App 回退正常界面安全，
            状态残留由 toggle 可覆盖）。

    ★ 校验为**创建时快照**（2026-08-17 review 方向）：注册表校验仅在工厂
    调用时执行一次，运行期 ``FULLSCREEN_VIEWS`` 增删不反映到已创建回调
    （删除条目后 toggle 仍写入该 view_id——App 回退正常界面、残留由 toggle
    覆盖，与未知 id 设计语义一致；当前无运行期动态注册场景）。
    """
    # ★ review 方向 P3：调用方误用（view_id 未注册）防御提示——惰性 import
    #   防循环依赖（app → trace_view → ... ；assembly 层仅运行期引用）。
    try:
        from src.tui.app.app import FULLSCREEN_VIEWS
        if view_id not in FULLSCREEN_VIEWS:
            _logger.warning(
                "fullscreen toggle view_id=%r 未注册（FULLSCREEN_VIEWS 键：%s）",
                view_id, sorted(FULLSCREEN_VIEWS),
            )
    except Exception:
        _logger.debug("fullscreen toggle 注册表校验异常", exc_info=True)

    def _cb():
        if getattr(model, "fullscreen", "") == view_id:
            model.fullscreen = ""
        else:
            model.fullscreen = view_id
        session.request_bottom_redraw()

    return _cb


def _make_trace_toggle_cb(model, session):
    """构建 Ctrl+H 轨迹视图开关回调（``_make_fullscreen_toggle_cb`` 的
    view_id="trace" 实例——兼容入口，测试/装配调用面不变）。

    2026-08-19：InputDispatcher 在 render 线程调用本回调——翻转
    ``model.trace_open``（= ``model.fullscreen``）并请求重绘（App 据此在
    消息区与 TraceView 间切换）。选中索引（``trace_selected``，-1=跟随尾部）
    跨开关保留——重新打开时回到上次浏览位置；聊天内容清空（Ctrl+L
    reset_display）时模型侧已复位为 -1。
    """
    return _make_fullscreen_toggle_cb(model, session, "trace")


def _make_logs_toggle_cb(model, session):
    """构建 F12 会话日志 / 投影浏览器视图开关回调。

    与 F1 帮助视图（``_make_fullscreen_toggle_cb``）同构，但打开时先调用装配
    注入的 ``model.logs_refresher(force=True)`` 构建视图数据——**不经命令
    队列**（``/logs`` 命令在 AI 生成期间会被排队，F12 必须能立即打开，含
    流式输出期间）；数据后续由视图渲染期按会话日志签名实时刷新。未注入
    刷新器（无会话 / 测试桩）时仍打开视图（空数据，视图渲染期会再试）。
    """

    def _cb():
        if getattr(model, "fullscreen", "") == "logs":
            model.fullscreen = ""
        else:
            state = getattr(model, "logs_view", None)
            if state is not None:
                # 重开：复位上次关闭残留（done/选中/跟随），保证可见
                state.done = False
                state.action = ""
                state.visible = False
                state.selected = 0
                state.cursor = 0
                state.scroll = 0
                state.follow_tail = True
            refresher = getattr(model, "logs_refresher", None)
            if callable(refresher):
                try:
                    refresher(force=True)
                except Exception:
                    _logger.debug("F12 会话日志数据构建失败", exc_info=True)
            model.fullscreen = "logs"
        session.request_bottom_redraw()

    return _cb


#: 文件沙盒视图族 id（F11 开关的作用域）——任一视图打开时 F11 均为「关闭」。
_SANDBOX_VIEW_IDS = ("changes", "sandbox", "sandbox_history", "sandbox_records")


def _make_changes_toggle_cb(model, session):
    """构建 F11 文件变更审查器视图开关回调。

    与 F12 会话日志视图（``_make_logs_toggle_cb``）同构，但打开时先调用装配
    注入的 ``model.sandbox_refresher(force=True)`` 构建视图数据——**不经命令
    队列**（``/changes`` 命令在 AI 生成期间会被排队，F11 必须能立即打开，含
    流式输出期间）；数据后续由视图渲染期按沙盒数据签名实时刷新。未注入
    刷新器（无会话 / 测试桩）时仍打开视图（空数据，视图渲染期会再试）。

    ★ 开关语义（2026-10-10）：当前 ``fullscreen`` 属于**文件沙盒视图族**
    （changes / sandbox / sandbox_history / sandbox_records）时，F11 一律
    **关闭**（回到聊天界面）——用户在任一沙盒视图内按一次 F11 即可关闭。
    （视图自身也会消费 F11 关闭；本回调是「事件未被视图消费」时的兜底。）
    """

    def _cb():
        current = getattr(model, "fullscreen", "") or ""
        if current in _SANDBOX_VIEW_IDS:
            model.fullscreen = ""
        else:
            state = getattr(model, "changes_view", None)
            # 重开：复位上次关闭的残留（终态/选中/搜索/确认/模式）——单一真源
            # ``sandbox_common.reset_sandbox_view_state``（与沙盒概览视图
            # 「h/l 打开子视图」共用同一张复位表，避免两处字段漂移）。
            try:
                from src.tui.app.sandbox_common import (
                    VIEW_STATE_ATTRS,
                    reset_sandbox_view_state,
                )

                reset_sandbox_view_state(
                    state, VIEW_STATE_ATTRS.get("changes", "changes_view"),
                )
            except Exception:
                _logger.debug("F11 沙盒视图状态复位失败（忽略）", exc_info=True)
            refresher = getattr(model, "sandbox_refresher", None)
            if callable(refresher):
                try:
                    refresher(force=True)
                except Exception:
                    _logger.debug("F11 文件变更审查器数据构建失败", exc_info=True)
            # 兜底：无论刷新器是否可用，打开即置可见（否则沙盒管理器缺失 /
            # 刷新器未注入时 visible 保持 False → 视图渲染空占位）。
            if state is not None:
                state.visible = True
            model.fullscreen = "changes"
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
    "_make_trace_toggle_cb",
    "_make_logs_toggle_cb",
    "_make_changes_toggle_cb",
    "_make_fullscreen_toggle_cb",
]
