"""消费者 API — ChatUIConsumer 兼容实现（薄外观）。

ChatUIConsumer 作为薄外观（Thin Facade），将职责委托给三个独立组件：
  - TuiAssembly（_assembly.py）— 子系统装配
  - TuiLifecycle（_lifecycle.py）— 生命周期管理
  - TuiInputOrchestrator（_input_orchestrator.py）— 输入等待编排

设计模式: 外观（Facade）— ChatUIConsumer 保持所有公开 API 签名不变。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    # 仅供 mypy/pyright 类型检查（运行时注解均为字符串形式，不求值）。
    # 技术债清理（方向4）：移除纯未使用符号（AppModel/事件类型类——
    # 字符串注解 ``"InkSession"`` 等仅引用下列五个框架类型）。
    from src.tui.ink.session import InkSession
    from src.tui._ink_bridge import InkBridge
    from src.tui._dispatcher import EventDispatcher
    from src.tui._input import Input
    from src.tui._completion import _CmplHandler

from src.tui._const import (
    RenderCmd,
    UserMsgCmd, NotificationCmd, ErrorCmd,
    WriteLineCmd, DisplayMsgsCmd, ClearMsgsCmd,
)
from src.renderer.locks import render_lock
from src.tui.state.consumer_registry import (
    _register_consumer,
    _unregister_consumer,
)
from src.tui._assembly import TuiAssembly, TuiAssemblyResult
from src.tui._components import _ComponentsNamespace
from src.tui._lifecycle import TuiLifecycle
from src.tui._input_orchestrator import TuiInputOrchestrator

_logger = logging.getLogger(__name__)


def _assemble_subsystems() -> TuiAssemblyResult:
    """装配 TUI 子系统（内核 ``ctx.ui`` 服务优先，回退 ``TuiAssembly``）。

    「一切皆插件」：终端 UI 子系统装配经 ``ctx.ui.assemble()`` 暴露，构造方
    经内核服务装配，可按插件替换装配实现；内核缺失（单元测试、独立调用）
    时回退 ``TuiAssembly.assemble()``。
    """
    try:
        from ..kernel.runtime import active_service

        service = active_service("ui")
        assemble = getattr(service, "assemble", None) if service is not None else None
        if callable(assemble):
            return assemble()
    except Exception:
        _logger.debug("ui 服务 assemble 探测失败，回退默认装配", exc_info=True)
    return TuiAssembly.assemble()


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（薄外观）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — TUI 统一入口（薄外观）。

    将职责委托给三个独立组件：
      - TuiAssembly：组装子系统
      - TuiLifecycle：start/stop/suspend/resume
      - TuiInputOrchestrator：wait_for_user_input

    公开方法与旧版完全兼容。
    """

    def __init__(self, event_bus=None, message_source=None):
        """初始化 ChatUIConsumer。

        Args:
            event_bus: DisplayEventBus 实例。为 None 时获取默认实例。
            message_source: agent 消息列表访问器 ``() -> list[dict]``——注入
                AppModel 供轨迹视图（Ctrl+H）以真实会话消息为数据源
                （system/user/assistant+tool_calls/tool 返回）；None 可稍后经
                ``set_message_source`` 注入（会话创建晚于 UI 装配）。
        """
        if event_bus is None:
            from src.tui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        self._bus = event_bus

        # ── 通过 TuiAssembly 装配子系统 ──
        # 方向3 步骤16：assemble(on_display_messages=) 死参数已移除，
        # 显示路径统一由 DisplayMsgsCmd → apply._do_display_messages 承载。
        result: TuiAssemblyResult = _assemble_subsystems()
        self._rs = result.rs
        self._engine: "InkSession" = result.engine
        self._bb: "InkBridge" = result.bb
        self._dispatcher: "EventDispatcher" = result.dispatcher
        self._renderer = result.renderer
        self._cmpl_handler: "_CmplHandler" = result.cmpl_handler
        self._input: "Input" = result.input_instance
        self._subagent_controller = result.subagent_controller
        self._components = result.components

        # ── 委托组件 ──
        self._lifecycle = TuiLifecycle(
            engine=self._engine, bus=self._bus, bb=self._bb,
            rs=self._rs, dispatcher=self._dispatcher,
            subagent_controller=self._subagent_controller,
        )
        self._input_orchestrator = TuiInputOrchestrator(self._input)
        # 轨迹视图消息源（2026-08-19）：装配后注入（会话可能晚于 UI 创建）
        if message_source is not None:
            self.set_message_source(message_source)

    def set_message_source(self, source) -> None:
        """注入 agent 消息列表访问器（轨迹视图数据源）。

        source 签名: ``() -> list[dict]``——返回真实会话消息列表
        （system/user/assistant+tool_calls/tool 返回）；None 可清除注入
        （回退 TUI 块构建路径）。装配后调用（会话创建晚于 UI 装配——
        主循环/单次模式经 ``_register_session_handlers`` 注入）。重复注入
        幂等（最新 source 生效）。
        """
        self._rs.message_source = source

    def set_sandbox_source(self, source) -> None:
        """注入文件沙盒访问器（轨迹检查器关联文件变更用）。

        source 签名: ``() -> SandboxManager | None``——返回全局文件沙盒
        管理器；轨迹检查器据此把选中 tool 记录关联到该工具调用产生的
        文件变更（``FileChangeRecord.message_index`` + 工具名匹配）。
        None 可清除注入（不显示关联小节）。装配后调用（会话创建晚于 UI
        装配）。重复注入幂等（最新 source 生效）。
        """
        self._rs.sandbox_source = source

    @classmethod
    def for_testing(cls, components, event_bus=None) -> "ChatUIConsumer":
        """创建用于测试的 ChatUIConsumer 实例。

        支持两种 ``components`` 输入（2026-08-05 简化说明：旧
        ``_ChatUIComponents`` 类已删除，对象风格入口保留——测试用
        ``SimpleNamespace`` 传组件）：
          - 对象风格（hasattr ``rs``）：提取属性
          - ``dict``：按 key 取值

        Args:
            components: 预创建的组件容器或字典。
            event_bus: DisplayEventBus 实例。

        Returns:
            新的 ChatUIConsumer 实例。
        """
        if event_bus is None:
            from src.tui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        instance = cls.__new__(cls)
        instance._bus = event_bus

        # 对象风格（旧 _ChatUIComponents / SimpleNamespace）与 dict 双入口
        if hasattr(components, 'rs'):
            instance._rs = components.rs
            instance._engine = components.engine
            instance._bb = components.bottom_bar
            instance._dispatcher = components.dispatcher
            instance._renderer = components.tui_renderer
            instance._cmpl_handler = components.cmpl_handler
            instance._input = components.input
        else:
            instance._rs = components.get('rs')
            instance._engine = components.get('engine')
            instance._bb = components.get('bottom_bar')
            instance._dispatcher = components.get('dispatcher')
            instance._renderer = components.get('tui_renderer')
            instance._cmpl_handler = components.get('cmpl_handler')
            instance._input = components.get('input')

        instance._subagent_controller = None
        instance._components = _ComponentsNamespace(instance._input)
        instance._lifecycle = TuiLifecycle(
            engine=instance._engine, bus=instance._bus, bb=instance._bb,
            rs=instance._rs, dispatcher=instance._dispatcher,
        )
        instance._input_orchestrator = TuiInputOrchestrator(instance._input)
        return instance

    # ── 生命周期（委托 TuiLifecycle） ─────────────

    @property
    def _started(self):
        return self._lifecycle.is_started

    @property
    def _handlers_bound(self):
        return self._lifecycle.handlers_bound

    @property
    def _bound_handlers(self):
        return self._lifecycle.bound_handlers

    @_bound_handlers.setter
    def _bound_handlers(self, value):
        self._lifecycle.bound_handlers = value

    @property
    def _state_lock(self):
        return self._lifecycle._state_lock

    def start(self) -> None:
        """启动 ChatUI 消费者（委托 TuiLifecycle + 注册消费者）。

        ★ P2-5（并发安全）：注册与 ``_started`` 检查移入 ``_state_lock``
        临界区（``_state_lock`` 为 RLock——本类持锁委托 ``_lifecycle.start()``
        可重入）——修复前检查在锁外，并发重复调用会重复注册消费者
        （consumer_registry refcount 错乱）。异常回滚保留（注册后 lifecycle
        start 失败 → 注销并 re-raise）。
        """
        with self._state_lock:
            if not self._started:
                _register_consumer(self)
                # 方向2（注册回滚）：lifecycle.start 异常不泄漏消费者注册表——
                # 回滚注册并 re-raise（不静默丢输出）。
                try:
                    self._lifecycle.start()
                except Exception:
                    _unregister_consumer()
                    raise
            # else 分支：已启动则直接返回（_lifecycle.start() 幂等 no-op，无需重复调用）

    def stop(self) -> None:
        """停止 ChatUI 消费者（委托 TuiLifecycle + 注销消费者）。

        ★ P2-5（并发安全）：注销与 ``_started`` 检查移入 ``_state_lock``
        临界区（RLock 可重入）——修复前检查在锁外，并发重复调用会重复注销
        消费者（refcount 负向错乱）。
        """
        with self._state_lock:
            try:
                if self._started:
                    try:
                        self._lifecycle.stop()
                    finally:
                        # 无论 lifecycle.stop 是否抛异常，都必须注销消费者，
                        # 防止消费者注册表泄漏、get_active_chat_ui() 返回已停止实例。
                        _unregister_consumer()
            finally:
                # 补全引擎后台线程 + 数据源监听器随会话关闭（修复前
                # warmup 启动的 daemon loader 线程与 listener 永久存活）。
                # 即使从未 start（装配后预热线程已启动）也必须在 stop 时关闭。
                cmpl_close = getattr(self._cmpl_handler, "close", None)
                if callable(cmpl_close):
                    try:
                        cmpl_close()
                    except Exception:
                        import logging
                        logging.getLogger(__name__).debug(
                            "cmpl_handler.close 异常", exc_info=True,
                        )

    def suspend(self) -> None:
        """暂停渲染引擎（委托 TuiLifecycle）。"""
        self._lifecycle.suspend()

    def resume(self) -> None:
        """恢复渲染引擎（委托 TuiLifecycle）。"""
        self._lifecycle.resume()

    # ── 公开方法 ──────────────────────────────────

    def on_user_message(self, text: str) -> None:
        self._engine.push_cmd(UserMsgCmd(text=text))

    def on_notification(self, text: str) -> None:
        self._engine.push_cmd(NotificationCmd(text=text))

    def on_error(self, message: str) -> None:
        if not message:
            return
        self._engine.push_cmd(ErrorCmd(message=message))

    def register_event_handler(self, event_type: type, handler_method: Callable) -> None:
        """注册自定义事件处理器（委托 TuiLifecycle）。"""
        self._lifecycle.register_event_handler(event_type, handler_method)

    def request_bottom_redraw(self) -> None:
        self._engine.request_bottom_redraw()

    def flush_input_router(self, timeout: float = 2.0) -> bool:
        """同步等待渲染线程发布新 input router（模态弹窗清理后防旧 router 吞输入）。

        ★ 2026-08-19（editmsg「很多上文时按回车不能编辑对应消息」根因修复，
        user_select 同类窗口预防）：模态弹窗（UserSelectPopup 等）清理后、
        渲染线程发布新 input router 前，旧 router（含已卸载弹窗控件 handler +
        use_modal 吞噬）会把用户后续按键（Enter 等）消费掉——事件被吞、
        ``_enter()`` 不执行。弹窗 finally 清理后调用本方法同步等待新 router
        发布，再放行后续输入处理。详见 ``InkSession.flush_input_router``。
        """
        try:
            return self._engine.flush_input_router(timeout)
        except AttributeError:
            return False

    def get_input_component(self):
        """返回输入组件实例（兼容旧 _components.input 访问路径）。"""
        return self._components.input

    def get_model(self):
        """返回 AppModel 实例（React Ink 化 user_select 弹窗状态读写）。"""
        return self._rs

    def get_input(self):
        """返回输入组件实例（供 core 层插件使用）。"""
        return self._input

    def write_line(self, text: str) -> None:
        self._engine.push_cmd(WriteLineCmd(text=text))

    def set_exit_note(self, text: str) -> None:
        """登记退出提示（TUI 停止时输出在界面内容下方一行，不清屏）。

        ★ 2026-10-20（用户需求）：退出提示（「再见   恢复: python chat.py
        --load …」）不能再作为内容行写进 TUI（会随界面一起被收尾覆盖），
        改由 InkSession 在 ``stop()``（渲染线程结束后）经
        ``InkRenderer.emit_exit_note`` 输出到全部 TUI 内容的下方一行。
        旧引擎/测试桩无此能力时回退 ``write_line``（行为等同修复前）。
        """
        setter = getattr(self._engine, "set_exit_note", None)
        if callable(setter):
            setter(text)
            return
        self.write_line(text)

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        self._engine.push_cmd(DisplayMsgsCmd(messages=messages, speed=speed))

    def clear_messages(self) -> None:
        """清空消息区显示（/editmsg /deitmsg 编辑后重渲染前使用）。

        推入 ``ClearMsgsCmd``（LOW 优先级，与 ``DisplayMsgsCmd``/``WriteLineCmd``
        同批按序处理）——先删除消息区旧显示，再重新渲染剩余消息，避免编辑后
        旧消息与重渲染副本叠加上屏。内部委托 ``model.reset_display()``，
        保留底部栏状态与输入缓冲（不丢输入）。
        """
        self._engine.push_cmd(ClearMsgsCmd())

    def wait_for_user_input(
        self, monitor, prefill: str = "", timeout: float | None = None,
        input_=None,
    ) -> str:
        """阻塞等待用户输入（委托 TuiInputOrchestrator）。"""
        return self._input_orchestrator.wait_for_user_input(
            monitor, prefill=prefill, timeout=timeout, input_=input_,
        )

    def setup_completion(self, input_) -> None:
        """设置 Tab 补全回调到 Input 实例。"""
        input_.set_completion_callback(self._cmpl_handler.on_tab)
        input_.set_dismiss_completion_callback(self._cmpl_handler.on_dismiss)
        input_.set_completion_navigate_callback(self._cmpl_handler.on_navigate)
        input_.set_auto_completion_callback(self._cmpl_handler.on_auto)
        # ★ 异步补全：注入当前输入文本提供者——数据源后台加载完成时就绪
        #   监听器按**最新**输入重算并刷新弹窗（而非过期文本）。
        set_provider = getattr(self._cmpl_handler, "set_text_provider", None)
        get_text = getattr(input_, "get_current_text", None)
        if callable(set_provider) and callable(get_text):
            set_provider(get_text)

    @property
    def input(self):
        return self._input

    @property
    def bottom_bar(self):
        return self._bb

    # ── 通用浮动窗口（2026-10-21 用户需求） ─────────

    def open_floating_window(self, window) -> bool:
        """打开（或替换同 id）浮动窗口。

        Args:
            window: ``src.tui.app.floating.FloatingWindowSpec``——窗口内部
                可放任意 React Ink 组件（hooks/标准控件可用）。

        Returns:
            True=已登记；False=无效规格或底层桥不支持（测试桩）。
        """
        fn = getattr(self._bb, "open_floating_window", None)
        if not callable(fn):
            return False
        return bool(fn(window))

    def update_floating_window(self, window_id: str, **changes) -> bool:
        """更新已打开窗口的规格字段（如位置/内容组件）。"""
        fn = getattr(self._bb, "update_floating_window", None)
        if not callable(fn):
            return False
        return bool(fn(window_id, **changes))

    def close_floating_window(self, window_id: str) -> bool:
        """关闭指定浮动窗口（原内容在下一帧自动还原）。"""
        fn = getattr(self._bb, "close_floating_window", None)
        if not callable(fn):
            return False
        return bool(fn(window_id))

    def clear_floating_windows(self) -> None:
        """关闭全部浮动窗口（幂等）。"""
        fn = getattr(self._bb, "clear_floating_windows", None)
        if callable(fn):
            fn()

    # ── /pet 宠物浮窗（/pet 命令使用） ───────────────

    def _floating_window_ids(self) -> set[str]:
        """当前已登记浮窗 id 集合（strip 归一化；无模型时为空集）。"""
        model = getattr(self, "_rs", None)
        windows = getattr(model, "floating_windows", None) or []
        return {
            str(getattr(window, "id", "")).strip()
            for window in windows
            if str(getattr(window, "id", "")).strip()
        }

    def open_pet_floating_window(self) -> bool:
        """打开（或替换）爱吃大白饭的蓝色大肥鱼宠物浮窗。

        规格见 ``src.tui.app.pet.pet_window_spec``：无边框、透明背景、右侧
        40 列浮窗；22 种动作含干饭/咀嚼/打嗝/追饭粒/吃撑躺平/跳舞/吃 tok。
        返回底层桥是否登记成功。

        ``/loop`` 同时打开时把循环浮窗移到左侧；``/usage`` 已占右侧时宠物
        也开在左侧——浮窗之间不互相遮挡。
        """
        from src.tui.app.pet import pet_window_spec

        ids = self._floating_window_ids()
        if "loop" in ids:
            self.update_floating_window("loop", x="left")
        spec = pet_window_spec()
        if "usage" in ids:
            spec = spec.copy_with(x="left")
        return self.open_floating_window(spec)

    def close_pet_floating_window(self) -> bool:
        """关闭宠物浮窗（窗口不存在时返回 False）。

        宠物关闭后若 ``/loop`` 仍打开：``/usage`` 占右侧时循环浮窗保持左侧，
        否则恢复到默认右侧。
        """
        from src.tui.app.pet import PET_WINDOW_ID

        closed = self.close_floating_window(PET_WINDOW_ID)
        ids = self._floating_window_ids()
        if closed and "loop" in ids:
            self.update_floating_window(
                "loop", x="left" if "usage" in ids else "right",
            )
        return closed

    def is_pet_floating_window_open(self) -> bool:
        """宠物浮窗是否正在显示（供 ``/pet`` 做开关切换）。"""
        from src.tui.app.pet import PET_WINDOW_ID

        return PET_WINDOW_ID in self._floating_window_ids()

    # ── /usage 用量浮窗（/usage 命令使用） ─────────────

    def open_usage_floating_window(self, provider) -> bool:
        """打开右侧用量浮窗（``/usage``）。

        Args:
            provider: 无参可调用对象，返回 ``build_usage_sections`` 的统计
                区块列表；浮窗每帧重建时调用（实时刷新）。

        Returns:
            True=已登记；False=底层桥不支持 / 无效规格。

        右侧让给用量浮窗：已打开的 ``/loop`` / ``/pet`` 临时移到左侧，关闭
        用量浮窗后由 ``close_usage_floating_window`` 恢复原布局。
        """
        from src.tui.app.floating import usage_window_spec

        ids = self._floating_window_ids()
        for window_id in ("loop", "pet"):
            if window_id in ids:
                self.update_floating_window(window_id, x="left")
        return self.open_floating_window(usage_window_spec(provider))

    def close_usage_floating_window(self) -> bool:
        """关闭右侧用量浮窗并恢复 ``/loop`` / ``/pet`` 的左右布局。"""
        from src.tui.app.floating import USAGE_WINDOW_ID

        closed = self.close_floating_window(USAGE_WINDOW_ID)
        if not closed:
            return False
        ids = self._floating_window_ids()
        if "pet" in ids:
            # 宠物回右侧；循环浮窗优先让到左侧（与 /pet 既有布局一致）。
            self.update_floating_window("pet", x="right")
            if "loop" in ids:
                self.update_floating_window("loop", x="left")
        elif "loop" in ids:
            self.update_floating_window("loop", x="right")
        return True

    def is_usage_floating_window_open(self) -> bool:
        """用量浮窗是否正在显示（供 ``/usage`` 做开关切换）。"""
        from src.tui.app.floating import USAGE_WINDOW_ID

        return USAGE_WINDOW_ID in self._floating_window_ids()

    # ── /loop 循环浮窗（/loop 命令使用） ─────────────

    def open_loop_floating_window(
        self, total: int, prompt: str, runs_per_round: int = 1,
    ) -> None:
        """打开 ``/loop`` 循环浮窗并重置循环进度。

        浮窗锚定输入区最上方的分隔线右端、向上展开（带边框），显示
        总批次数 / 当前批次 / 批内运行次数 / 当前提词 / 本次已运行时间
        （动态刷新）/ 下一轮生成提词与生成状态；进度经 ``set_loop_status``
        更新，循环结束（含中断）经 ``close_loop_floating_window`` 关闭——
        关闭后原内容由下一帧整帧重建自动还原。``total=0`` 仅作防御
        （新语义要求批次数为正整数，不会传入 0）。
        """
        from src.tui.app.floating import loop_window_spec

        try:
            total_n = max(0, int(total))
        except (TypeError, ValueError, OverflowError):
            total_n = 0
        try:
            per_round = max(1, int(runs_per_round))
        except (TypeError, ValueError, OverflowError):
            per_round = 1
        setter = getattr(self._bb, "set_loop_status", None)
        if callable(setter):
            setter(
                active=True,
                total=total_n,
                prompt=str(prompt or ""),
                next_prompt="",
                generating=False,
                round=0,
                run=0,
                runs_per_round=per_round,
                # 计时锚点清零：本次尚未开始（浮窗显示「--」，第一次运行时
                # 由 set_loop_status 写入本次开始时间戳）。
                round_started_at=0.0,
                run_started_at=0.0,
            )
        spec = loop_window_spec()
        if "pet" in self._floating_window_ids() or "usage" in self._floating_window_ids():
            # 右侧被宠物 / 用量浮窗占用时，循环浮窗让到左侧，避免互相遮挡。
            spec = spec.copy_with(x="left")
        self.open_floating_window(spec)

    def set_loop_status(self, **fields) -> None:
        """更新 ``/loop`` 进度（total/round/prompt/next_prompt/generating/
        active/round_started_at/run_started_at）。"""
        fn = getattr(self._bb, "set_loop_status", None)
        if callable(fn):
            fn(**fields)

    def close_loop_floating_window(self) -> None:
        """关闭 ``/loop`` 浮窗并重置循环状态（幂等；循环结束/中断调用）。"""
        self.close_floating_window("loop")
        resetter = getattr(self._bb, "reset_loop_status", None)
        if callable(resetter):
            resetter()

    @property
    def output_adapter(self):
        """返回 None（非全屏流动模型无 OutputAdapter；2026-08-05 死代码清理）。

        旧实现委托 ``self._renderer.output_adapter``（``_InkRendererFacade``
        占位恒 None，无生产消费方）——占位类已删除，本属性直接返回 None
        保持公共 API 兼容（test_consumer.py 已同步更新断言）。
        """
        return None

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._engine.set_panel_refresh_callback(callback)

    def setup_bottom_bar(self) -> None:
        with render_lock:
            self._bb.setup()

    def teardown_bottom_bar(self) -> None:
        self._bb.teardown()

    def ensure_cursor_upper(self) -> None:
        self._engine.ensure_cursor_upper()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        effective_pos = len(text) if cursor_pos < 0 else cursor_pos
        # ink 模型：输入状态注入 AppModel + 重渲染
        self._engine.update_input(text, effective_pos)

    def flush(self, timeout: float | None = 5.0) -> None:
        self._engine.flush(timeout=timeout)

    def push_cmd(self, cmd: RenderCmd) -> None:
        self._engine.push_cmd(cmd)

    def push_splash(self) -> None:
        """推送启动欢迎卡（提交块）——在模型名已知后调用。

        必须在会话装配完成后推送：欢迎卡需展示真实模型/模式信息，
        过早推送只能回退到版本号。
        """
        from src.tui._const import SplashCmd

        self._engine.push_cmd(SplashCmd())
