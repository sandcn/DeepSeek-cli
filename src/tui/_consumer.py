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
from src.renderer._locks import render_lock
from src.tui.state.consumer_registry import (
    _register_consumer,
    _unregister_consumer,
)
from src.tui._assembly import TuiAssembly, TuiAssemblyResult
from src.tui._components import _ComponentsNamespace
from src.tui._lifecycle import TuiLifecycle
from src.tui._input_orchestrator import TuiInputOrchestrator

_logger = logging.getLogger(__name__)


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
        result: TuiAssemblyResult = TuiAssembly.assemble()
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
            if self._started:
                try:
                    self._lifecycle.stop()
                finally:
                    # 无论 lifecycle.stop 是否抛异常，都必须注销消费者，
                    # 防止消费者注册表泄漏、get_active_chat_ui() 返回已停止实例。
                    _unregister_consumer()

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

    @property
    def input(self):
        return self._input

    @property
    def bottom_bar(self):
        return self._bb

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
