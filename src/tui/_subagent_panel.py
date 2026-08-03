"""SubAgent 面板控制器 — 消费 EventBus 事件，渲染 subagent 状态面板。

架构（与旧 ParallelDisplay 同等的终端渲染效果）：
  事件源（EventBusDisplayProxy / parallel_executor）
    → EventBus.publish(AgentAddedEvent, AgentStatusChanged, ModelPhaseEvent, ...)
    → SubAgentPanelController（本模块，外观）
    → 帧渲染（含摘要行/分隔线/树形连接/工具历史/spinner）
    → RenderCommand.SUBAGENT_FRAME 推送
    → TuiRenderer._do_subagent_frame()
    → BottomBar.set_subagent_frame() → 终端显示

渲染效果：
  ● 3 agents [████████░░░░] · 12.5k out · 1.2k/s · 15.3s · 2/3 done
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ├─ ⠋ [EXE] 分析代码结构  12.5k out  1.2k/s  15.3s
   │  …thinking  3.2s
   │  ● read_file /path/to/file.py  0.3s
   │  ✔ grep pattern src/  0.1s
   └─ ✔ [EXE] 生成测试  8.2k out  10.1s

方向C 步骤7 拆分说明（上帝类 → 三域分离）：
  - 状态建模 → ``src/tui/_subagent_state.py``（``_AgentSlot``/``_ToolRecord``/``StateStore``）
  - 帧渲染   → ``src/tui/_subagent_render.py``（``render_frame``/``build_agent_lines``/动效辅助）
  - 本模块   → 控制器外观（订阅管理 + 事件分发 + 推送），保持公开方法面不变。
  兼容：``_AgentSlot``/``_ToolRecord``/``_SPINNER_FRAMES``/``_get_tool_color``/``_C_*``
  仍经本模块 re-export（既有测试/插件访问路径不变）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List

from src.tui._const import (
    _C_ANSWERING,
    _C_BATCH,
    _C_BRANCH,
    _C_DIMMER,
    _C_DIMMEST,
    _C_DONE,
    _C_FAIL,
    _C_PARSING,
    _C_RESET,
    _C_RUNNING,
    _C_SUMMARY_DIM,
    RenderCmd,
    SubagentFrameCmd,
)
from src.tui._format import format_duration as _format_duration
from src.tui._format import format_tokens as _format_tokens
from src.tui._format import format_speed as _format_speed
from src.tui.events.event_types import (
    AgentAddedEvent, AgentStatusChanged, ModelPhaseEvent,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
    ParseInfoEvent, ParseInfoDoneEvent,
    UsageUpdatedEvent, MetricsUpdateEvent,
)

from src.tui._subagent_state import StateStore, _AgentSlot, _ToolRecord
from src.tui._subagent_render import (
    _SPINNER_FRAMES,
    _get_tool_color,
    build_agent_lines as _build_agent_lines_impl,
    render_frame as _render_frame_impl,
)

_logger = logging.getLogger(__name__)

# 兼容 re-export（既有测试/插件经 src.tui._subagent_panel 访问路径不变）：
#   _AgentSlot / _ToolRecord（test_subagent_panel 直接导入）
#   _SPINNER_FRAMES（test_subagent_panel 直接导入）
#   _get_tool_color（test_tool_mapping_single_source 经 sp._get_tool_color 访问）
#   _C_*（test 经 sp._C_RUNNING / sp._C_RESET 访问，模块级从 _const 导入）
#   _format_duration / _format_tokens / _format_speed（patch 路径兼容）
#   time（patch("src.tui._subagent_panel.time.monotonic") 兼容——标准库 time 模块引用）


# ═══════════════════════════════════════════════════════════
# SubAgentPanelController（外观：订阅管理 + 事件分发 + 推送）
# ═══════════════════════════════════════════════════════════

class SubAgentPanelController:
    """SubAgent 面板控制器（外观模式）。

    职责收敛（方向C 步骤7 拆分后）：
      1. 订阅管理：``ensure_active()``/``stop()`` 中 10 类事件订阅/取消订阅
         （``_SUBSCRIPTIONS`` 声明式表）
      2. 事件分发：10 个 ``_on_*`` 处理器——委托 ``StateStore`` 变更方法
         （锁内）+ 置脏 + ``_emit_frame()``（锁外节流推送）
      3. 帧推送：``_push_frame``（注入 push_cmd 或降级 chat_ui）
      - 状态建模在 ``_subagent_state``；帧渲染在 ``_subagent_render``。
    """

    _instance: "SubAgentPanelController | None" = None
    _class_lock = threading.Lock()
    # 帧渲染节流：100ms 间隔（10Hz）
    _EMIT_INTERVAL: float = 0.1
    # 声明式订阅表：事件类型 → 处理器方法名。
    # ensure_active()/stop() 遍历本表订阅/取消订阅，消除硬编码重复代码。
    _SUBSCRIPTIONS: tuple[tuple[type, str], ...] = (
        (AgentAddedEvent, "_on_agent_added"),
        (AgentStatusChanged, "_on_agent_status_changed"),
        (ModelPhaseEvent, "_on_model_phase"),
        (ToolParsingEvent, "_on_tool_parsing"),
        (ToolStartedEvent, "_on_tool_started"),
        (ToolDoneEvent, "_on_tool_done"),
        (ParseInfoEvent, "_on_parse_info"),
        (ParseInfoDoneEvent, "_on_parse_info_done"),
        (UsageUpdatedEvent, "_on_usage_updated"),
        (MetricsUpdateEvent, "_on_metrics"),
    )

    def __init__(self, max_history: int = 3,
                 push_cmd: Callable[[RenderCmd], None] | None = None):
        self._store = StateStore(max_history=max_history)
        # 兼容既有测试直接访问 _agents/_order/_state_lock（与 store 同一引用）
        self._agents: Dict[str, _AgentSlot] = self._store._agents
        self._order: List[str] = self._store._order
        # RLock: 允许事件处理器在持有锁时调用渲染函数而不死锁；_render_frame 内部也获取此锁
        self._state_lock = self._store._state_lock
        self._frame: int = 0
        self._last_emit_time: float = 0.0
        # PERF-2：面板脏标记（事件处理器更新状态后置位；渲染后复位）
        self._dirty: bool = False
        # 方向2（节流丢帧补推）：_emit_frame 节流跳过时置位；_panel_refresh
        # （每帧回调）检测到补推最新帧并清标志（修复前节流期事件丢弃，事件
        # 期间最新状态可能延迟）。
        self._pending_emit: bool = False
        # 上一推送帧（变更检测，避免空转推送）
        self._last_pushed_frame: List[str] | None = None
        self._active: bool = False
        self._cb_registered: bool = False
        self._chat_ui: Any = None
        self._push_cmd_cb: Callable[[RenderCmd], None] | None = push_cmd
        self.max_history: int = max_history

    @classmethod
    def get_default(cls) -> "SubAgentPanelController":
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_push_cmd(self, cb: Callable[[RenderCmd], None] | None) -> None:
        """注入/更新 push_cmd 回调（单例复用后装配调用；None 清除）。

        方向5（单例统一）：装配复用 ``get_default()`` 单例后经本方法注入
        push_cmd 回调（替代每次构造新实例——双实例导致事件订阅/状态分裂，
        装配重建时订阅/动画状态丢失）。``_push_frame`` 已优先使用本回调。
        """
        self._push_cmd_cb = cb

    # ── 生命周期 ────────────────────────────────────────

    def ensure_active(self) -> None:
        if self._active:
            return
        from .events import DisplayEventBus
        bus = DisplayEventBus.get_default()
        # P3-11：订阅循环 try/except 包裹并统一回滚已订阅项（与 stop() 防御
        # 风格对齐）——任一订阅失败时回滚已订阅项，保持订阅状态一致。
        subscribed: list[tuple[type, Callable]] = []
        try:
            for ev_type, method_name in self._SUBSCRIPTIONS:
                handler = getattr(self, method_name)
                bus.subscribe(handler, event_type=ev_type)
                subscribed.append((ev_type, handler))
            self._register_panel_refresh()
        except Exception:
            # 回滚已订阅项（尽力而为，日志不抛）
            for ev_type, handler in subscribed:
                try:
                    bus.unsubscribe(handler, event_type=ev_type)
                except Exception:
                    _logger.debug("ensure_active 回滚取消订阅异常", exc_info=True)
            raise
        self._active = True

    def stop(self, clear_panel: bool = True) -> None:
        if not self._active:
            return
        from .events import DisplayEventBus
        bus = DisplayEventBus.get_default()
        for ev_type, method_name in self._SUBSCRIPTIONS:
            try:
                bus.unsubscribe(getattr(self, method_name), event_type=ev_type)
            except Exception:
                _logger.debug("stop() 取消订阅异常", exc_info=True)
        if clear_panel:
            self._push_frame([])
            # 强制立即重绘底部栏，确保面板立即消失
            if self._chat_ui is not None:
                try:
                    self._chat_ui.request_bottom_redraw()
                except Exception:
                    _logger.debug("request_bottom_redraw 异常", exc_info=True)
        self._unregister_panel_refresh()
        self._store.clear()
        self._active = False

    # ── 事件处理器（委托 StateStore 变更 + 置脏 + 节流推送） ──

    def _emit_frame(self) -> None:
        """渲染并推送当前帧（受 _EMIT_INTERVAL 节流）。

        锁顺序保证（防死锁）：
          ┌─ _render_frame() 内部获取/释放 _state_lock（RLock 可重入）
          └─ _push_frame()  在此函数外层调用，_state_lock 已释放

        调用此函数时调用方不应持有 _state_lock。
        _render_frame 内部获取/释放锁，_push_frame 在锁外调用。
        违反此顺序可能导致 render 线程等待 _state_lock 时形成 ABBA 死锁。

        方向2（节流丢帧补推）：节流跳过时置位 ``_pending_emit``——由
        ``_panel_refresh``（每帧回调）在下一帧补推最新状态（修复前节流期间
        事件丢弃，事件期间最新状态可能延迟）。
        """
        now = time.time()
        if now - self._last_emit_time < self._EMIT_INTERVAL:
            self._pending_emit = True  # 节流，跳过本次渲染 + 标记待补推
            return
        self._last_emit_time = now
        self._pending_emit = False
        lines = self._render_frame()
        self._push_frame(lines)
        # P3-13：推送成功后同步 _last_pushed_frame（与 _panel_refresh 变更检测
        # 状态一致）——修复前仅 _panel_refresh 更新，动画期间 _emit_frame 推送
        # 后 _panel_refresh 可能重复推同帧。
        self._last_pushed_frame = list(lines)
        # PERF-2：渲染后复位脏标记（后续空闲不再渲染）
        self._dirty = False

    def _on_agent_added(self, event) -> None:
        self._store.add_agent(
            label=event.label,
            description=event.description,
            status=event.status,
            agent_type=getattr(event, 'agent_type', 'execute'),
        )
        self._dirty = True
        self._emit_frame()

    def _on_agent_status_changed(self, event) -> None:
        self._store.update_status(event.label, event.status)
        self._dirty = True
        self._emit_frame()

    def _on_model_phase(self, event) -> None:
        self._store.set_model_phase(event.label, event.phase, event.info)
        self._dirty = True
        self._emit_frame()

    def _on_tool_parsing(self, event) -> None:
        """ToolParsingEvent — 流式解析工具参数时创建/更新 parsing 记录。

        ★ BUG-T3：改走 _emit_frame() 节流（10Hz）——流式 parsing 高频事件
          不再绕过 _EMIT_INTERVAL 每事件全帧渲染（既有
          TestSubAgentPanelParsingThrottle 已锁定此行为）。
           锁顺序保证（防死锁）：
             _store 变更方法内部获取/释放 _state_lock（RLock 可重入）
             _emit_frame() → _render_frame() 内部取锁 → _push_frame() 锁外
            注意：_push_frame 绝不可在 with self._state_lock 块内调用，
            否则 render 线程在同一锁上阻塞时形成 ABBA 死锁。
        """
        self._store.update_tool_parsing(
            event.label, event.tool_name, event.arguments,
        )
        self._dirty = True
        self._emit_frame()

    def _on_parse_info(self, event) -> None:
        """ParseInfoEvent — ToolParseTracker 定时推送的解析摘要（rf,rf 51t 0.74s）。"""
        self._store.set_parse_info(
            event.label, event.tool_names, event.tokens, event.elapsed,
        )
        self._dirty = True
        self._emit_frame()

    def _on_parse_info_done(self, event) -> None:
        """ParseInfoDoneEvent — 工具解析完成，清除解析摘要和 phase。"""
        self._store.clear_parse_info(event.label)
        self._dirty = True
        self._emit_frame()

    def _on_tool_started(self, event) -> None:
        self._store.start_tool(event.label, event.tool_name, event.detail)
        self._dirty = True
        self._emit_frame()

    def _on_tool_done(self, event) -> None:
        self._store.done_tool(event.label, event.tool_name, event.success)
        self._dirty = True
        self._emit_frame()

    def _on_usage_updated(self, event) -> None:
        self._store.update_usage(
            event.label, event.usage, getattr(event, 'replace', False),
        )
        self._dirty = True
        self._emit_frame()

    def _on_metrics(self, event) -> None:
        """MetricsUpdateEvent → 增量更新实时 token 计数和速度。"""
        self._store.update_metrics(
            event.label,
            event.live_input_tokens,
            event.live_output_tokens,
            event.output_tokens,
            event.speed,
        )
        self._dirty = True
        self._emit_frame()

    # ── 面板刷新回调 ────────────────────────────────────

    def _register_panel_refresh(self) -> None:
        # ★ 方向5（push_cmd 注入路径动画回调修复）：不再因已注入 push_cmd
        #   直接 return——即使已注入，仍尝试经 get_active_chat_ui() 获取
        #   chat_ui 并注册 _panel_refresh 到 engine（ChatUIConsumer.
        #   set_panel_refresh_callback 委托 engine.set_panel_refresh_callback
        #   ——session 的 _panel_refresh_cb 每帧驱动动画 10Hz 推进 spinner）。
        #   chat_ui 为 None 时记 debug 跳过（非致命——push_cmd 推送路径仍
        #   正常，仅动画回调缺失）。
        from .consumer import get_active_chat_ui
        chat_ui = get_active_chat_ui()
        if chat_ui is not None:
            chat_ui.set_panel_refresh_callback(self._panel_refresh)
            self._cb_registered = True
            self._chat_ui = chat_ui
            return
        if self._push_cmd_cb is not None:
            _logger.debug(
                "_register_panel_refresh: push_cmd 已注入但 chat_ui 不可用，"
                "动画回调未注册（非致命）"
            )

    def _unregister_panel_refresh(self) -> None:
        self._cb_registered = False
        if self._chat_ui is not None:
            try:
                self._chat_ui.set_panel_refresh_callback(None)
            except Exception:
                _logger.debug("set_panel_refresh_callback(None) 异常", exc_info=True)
        self._chat_ui = None

    def _needs_animation(self) -> bool:
        """是否存在活跃/动画状态（running agent / running tool）需要重绘推进。

        PERF-2：空闲（无事件 + 无动画需求）时 ``_panel_refresh`` 短路跳过
        全量渲染（保持动画时仍按 10Hz 渲染）。委托 ``StateStore.needs_animation``。
        """
        return self._store.needs_animation()

    def _panel_refresh(self) -> None:
        if not self._cb_registered:
            self._register_panel_refresh()
        # ★ PERF-2：无事件、无动画需求且无待补推时跳过全量渲染（避免每帧
        #   重建整个面板）；_pending_emit 置位（节流期丢帧）时绕过短路补推。
        if not self._dirty and not self._needs_animation() and not self._pending_emit:
            return
        # ★ PERF-4：面板刷新节流——与 _emit_frame 共用 _last_emit_time/
        #   _EMIT_INTERVAL（10Hz）。修复前每渲染循环迭代都渲染+推送（流式
        #   期间命令持续唤醒循环 → 循环高频运转），subagent 活跃时 CPU 满。
        #   节流跳过时置 _pending_emit，下一允许拍补推最新帧（不丢状态）。
        now = time.time()
        if now - self._last_emit_time < self._EMIT_INTERVAL:
            self._pending_emit = True
            return
        self._last_emit_time = now
        self._pending_emit = False
        try:
            lines = self._render_frame()
            # ★ 变更检测：帧无变化时跳过推送（避免空转 keep-alive 推送使
            #   render 循环持续置脏 → 空闲 CPU 100%）
            if lines != self._last_pushed_frame:
                self._last_pushed_frame = list(lines)
                self._push_frame(lines)
        except Exception:
            # ★ BUG-54（review 方向）：渲染异常时**不复位脏标记**——修复前
            #   ``self._dirty = False`` 在 try 外无条件执行：异常后脏标记被清，
            #   且无动画状态（needs_animation False）时面板不再重试渲染 →
            #   卡在陈旧内容。保留脏标记使下一允许拍重试（_EMIT_INTERVAL 节流
            #   10Hz，不会无限高频重试）。
            _logger.debug("_panel_refresh 异常", exc_info=True)
            return
        self._frame += 1
        # PERF-2：渲染后复位脏标记（后续空闲不再渲染）
        self._dirty = False

    # ── 帧渲染委托（实现迁移至 _subagent_render） ────

    def _render_frame(self) -> List[str]:
        """渲染面板帧（委托 _subagent_render.render_frame）。

        agents/order 传入当前引用（兼容既有测试整体替换 ``ctrl._agents``
        的场景）；锁取自 store（RLock 可重入）。
        """
        return _render_frame_impl(
            self._store, self.max_history, self._agents, self._order,
        )

    def _build_agent_lines(self, slot: _AgentSlot, now: float,
                           is_last: bool) -> List[str]:
        """构建单个 Agent 显示行（委托 _subagent_render.build_agent_lines）。"""
        return _build_agent_lines_impl(slot, now, is_last, self.max_history)

    # ── 帧推送 ──────────────────────────────────────────

    def _push_frame(self, lines: List[str]) -> None:
        # 优先使用注入的 push_cmd 回调，避免 get_active_chat_ui() 循环依赖
        if self._push_cmd_cb is not None:
            try:
                self._push_cmd_cb(SubagentFrameCmd(frame_lines=lines))
                return
            except Exception:
                _logger.debug("_push_frame push_cmd_cb 异常", exc_info=True)
        # 降级：通过 get_active_chat_ui() 获取
        chat_ui = self._chat_ui
        if chat_ui is None:
            from .consumer import get_active_chat_ui
            chat_ui = get_active_chat_ui()
            self._chat_ui = chat_ui
        if chat_ui is None:
            return
        try:
            chat_ui.push_cmd(SubagentFrameCmd(frame_lines=lines))
        except Exception:
            _logger.debug("_push_frame chat_ui.push_cmd 异常", exc_info=True)
