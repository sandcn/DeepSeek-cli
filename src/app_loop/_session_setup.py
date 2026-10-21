"""应用主循环会话设置 — 从 app_loop.py 拆分

包含：SessionState / _RoundResult 数据类、会话初始化、回调工厂。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src._compat import dataclass

from ._utils import _non_system_messages

from ._session_factory import create_session
from ..core.constants import DIM, RESET
from ..core.stats import reset_agent_input_baseline, reset_token_speed
from ..api.escape_monitor import EscapeMonitor

_logger = logging.getLogger(__name__)


def _notify_chat_completed(messages, elapsed) -> None:
    """发送「对话完成」通知：内核 notifications 服务优先，回退直接调用。

    「一切皆插件」：应用层不直接依赖通知实现——经 ``ctx.notifications``
    服务（provider 可替换 / 可禁用），内核缺失时回退 ``src.notifications``。
    """
    try:
        from ..core.adapters.kernel_runtime import active_notifications

        service = active_notifications()
    except Exception:
        service = None
    if service is not None:
        service.notify_chat_completed(messages, elapsed=elapsed)
        return
    from ..notifications import notify_chat_completed

    notify_chat_completed(messages, elapsed=elapsed)


# ── 会话状态 ──

def _terminal_title_sync_enabled() -> bool:
    """标题被接受后是否同步终端窗口标题（``session_title.sync_terminal_title``）。"""
    try:
        from ..config import get_rc
        from ..config.defaults import DEFAULTS

        default = bool((DEFAULTS.get("session_title") or {}).get("sync_terminal_title", True))
        raw = get_rc().get("session_title")
    except Exception:
        return True
    if isinstance(raw, dict) and "sync_terminal_title" in raw:
        value = raw["sync_terminal_title"]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on", "y", "t")
        if isinstance(value, (int, float)):
            return bool(value)
    return default


@dataclass(slots=True)
class SessionState:
    """会话状态 — 替代 TypedDict，提供运行时类型安全"""
    model: str = ""
    retry: bool = False
    prefill: str = ""


@dataclass(slots=True)
class _RoundResult:
    """单轮交互返回值"""
    should_exit: bool = False
    result: Any = None


# ── 会话初始化 ──

def _setup_session(loaded_data: dict | None = None, chat_ui=None) -> tuple:
    """初始化会话并加载历史消息（内核 sessions 服务优先）"""
    # 新会话 / 加载会话：Agent 新增输入的基线从头开始（首次请求计全量），
    # 避免沿用上一会话末尾的 input 得到一个无意义的差值。
    reset_agent_input_baseline()
    session = create_session(event_agent=True)

    state: SessionState = SessionState(model=session.model)

    if loaded_data:
        data = session.load(loaded_data["id"])
        if data:
            model = data.get("model", session.model)
            session.model = model
            state.model = model
            # 恢复会话后同步终端窗口标题（OSC 序列，无 TTY 静默失败）
            title = (data.get("title") or "").strip()
            if title:
                from ..tui._screen import set_window_title
                set_window_title(title)
            non_system = _non_system_messages(session)
            # 输出路径统一（方向C 步骤4）：--load 启动恢复消息经路径 A 渲染
            # （ChatUIConsumer.display_messages → DisplayMsgsCmd 管线，经 render_lock 保护）。
            # _setup_session 在交互模式必有 chat_ui；chat_ui=None 时跳过并 debug 日志
            # （非 ChatUI 上下文，如单次模式）。
            if chat_ui is not None:
                chat_ui.display_messages(non_system, speed=0)
                # P3-7：--load 启动恢复消息后 flush，确保首屏渲染命令在
                # _setup_session 返回前排空（display_messages 走 DisplayMsgsCmd
                # 管线，flush 驱动 engine 队列排空，避免首屏顺序错乱）。
                chat_ui.flush()
            else:
                _logger.debug(
                    "_setup_session: chat_ui 为 None，跳过恢复消息显示（非 ChatUI 上下文）",
                )
            if session.retry_pending and chat_ui is not None:
                chat_ui.write_line(f"  {DIM}  最后一条是用户消息，将自动继续生成回复…{RESET}")

    return session, state


# ── Monitor 回调工厂 ──

def _make_round_callbacks(
    session,
    monitor: EscapeMonitor,
    loop_state: dict,
    chat_ui=None,
) -> dict:
    """创建 round_start / round_end 回调函数

    loop_state: 与 InteractiveLoop 共享的字典，用于 round_end 回调
                将流式期间的排队输入传递给主循环。
    chat_ui: ChatUIConsumer 实例，用于管理底部栏和渲染协调。
    """
    def _on_round_start():
        # ★ 重置轮次耗时（⏱从 0 开始计时）
        # /loop 模式下跳过重置，让耗时跨轮累加
        if not loop_state.get("_loop_mode"):
            reset_token_speed()
        # ★ 激活底部栏状态行刷新（⏱耗时│总tok│实时tok/s）
        if chat_ui is not None:
            chat_ui.bottom_bar.enable_status()

    def _on_round_end(interrupted=False, delta=None, **kw):
        # ★ 防御性检查：chat_ui 为 None 时跳过 drain 操作
        if chat_ui is None or not hasattr(chat_ui, '_components') or chat_ui._components.input is None:
            _logger.warning("_on_round_end: input 不可用，跳过 drain 操作")
            return

        input_ = chat_ui._components.input

        # ★ 自动闭合空工具 box（● 工具）：后台任务等非工具上下文的输出可能
        #   创建只有标题行、永不闭合的空工具卡——每轮结束统一以完成态闭合
        #   （标题行状态图标翻转 ✔），避免空卡永久 ● running 悬挂。
        try:
            model = chat_ui.get_model()
            if model is not None and hasattr(model, "close_empty_tool_boxes"):
                closed = model.close_empty_tool_boxes()
                if closed > 0:
                    _logger.debug("_on_round_end: 自动闭合 %d 个空工具 box", closed)
        except Exception:
            _logger.debug("_on_round_end: 闭合空工具 box 失败", exc_info=True)

        # /loop 模式下不冻结状态行、不发桌面通知，保持状态行活跃
        if not loop_state.get("_loop_mode"):
            # ★ 冻结底部栏状态行（定格最终数值），同时获取耗时供通知复用
            notify_elapsed = kw.get("elapsed", 0.0)
            if chat_ui is not None:
                chat_ui.bottom_bar.disable_status()
                chat_ui.request_bottom_redraw()
                status_elapsed = chat_ui.bottom_bar.get_status_elapsed()
                if status_elapsed > 0:
                    notify_elapsed = status_elapsed
            # 桌面通知
            _notify_chat_completed(session.messages, notify_elapsed)

        # ★ 排出流式输入：queued（Enter提交）优先 → 跳过下轮输入提示
        #   buffer_text（未提交）→ 作为 prefill
        queued, buffer_text = input_.drain_all()
        if queued is not None:
            loop_state["queued_input"] = queued
        elif buffer_text:
            clean = ''.join(c for c in buffer_text
                            if c.isprintable() or c in ('\n', '\t'))
            if clean:
                session.captured_prefill = clean

        # ★ 原有逻辑：保存非可打印控制字符
        captured = input_.drain_captured()
        if captured:
            session.captured_prefill = captured

    def _on_title_updated(title="", source="", **kw):
        """标题被接受（含异步生成完成）→ 立即写入终端窗口标题。

        dsh 的标题是异步生成的：主对话可能早已开始回答，标题晚到；这里让
        终端窗口标签在标题就绪的**当下**更新，而不是等 30 秒周期保存。
        """
        text = str(title or "").strip()
        if not text or not _terminal_title_sync_enabled():
            return
        try:
            from ..tui._screen import set_window_title

            set_window_title(text)
        except Exception:
            _logger.debug("更新终端窗口标题失败（非关键）", exc_info=True)

    def _on_title_generating(generating=False, **kw):
        """标题生成开始/结束 → 状态栏「✎ 标题生成中」显示或消失。

        用户需求 2026-10-20：取标题是异步辅助任务（不阻塞对话、无流式输出），
        运行中必须对用户可见。状态栏段读 ``model.status.title_generating``，
        这里由 ``title_generating`` 事件驱动（生成结束 / 失败 / 取消都会复位）。
        """
        try:
            model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        except Exception:
            model = None
        if model is None:
            return
        try:
            model.status.title_generating = bool(generating)
            if not generating:
                # ★ 2026-10-20（用户需求：标题消耗的 tok / tok/s 要记录到 status
                #   行）：标题是静默后台流式调用，空闲期生成结束后状态栏在宽限期
                #   内继续展示「总tok / tok/s」，让这次标题的消耗可见。
                model.status.title_last_end_ts = time.monotonic()
            request = getattr(chat_ui, "request_bottom_redraw", None)
            if callable(request):
                request()
        except Exception:
            _logger.debug("更新标题生成状态失败（非关键）", exc_info=True)

    def _on_loop_prompt_generating(generating=False, **kw):
        """续跑提词生成开始/结束 → 状态栏「✎ 提词生成中」与浮窗状态。

        用户需求 2026-10-21：``/loop`` 的下一轮提词由 LLM 在等待后台任务前
        生成，属静默辅助调用；生成期间状态栏展示标记，结束后在宽限期内
        继续展示本次消耗的 tok / tok/s（与标题生成同一套口径）。
        """
        try:
            model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        except Exception:
            model = None
        if model is None:
            return
        try:
            active = bool(generating)
            model.status.loop_prompt_generating = active
            loop_status = getattr(model, "loop_status", None)
            if loop_status is not None:
                loop_status.generating = active
            if not active:
                model.status.loop_prompt_last_end_ts = time.monotonic()
            request = getattr(chat_ui, "request_bottom_redraw", None)
            if callable(request):
                request()
        except Exception:
            _logger.debug("更新续跑提词生成状态失败（非关键）", exc_info=True)

    def _on_loop_prompt_generated(should_continue=None, prompt="", error="", **kw):
        """续跑提词生成完成 → 浮窗「下一轮提词」立即刷新。

        ``should_continue`` 为 False 表示 LLM 判定任务已完成（loop 即将结束），
        此时不展示下一轮提词，避免浮窗显示一条永远不会用到的文本。
        """
        try:
            model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        except Exception:
            model = None
        if model is None:
            return
        try:
            loop_status = getattr(model, "loop_status", None)
            if loop_status is not None:
                loop_status.generating = False
                keep = should_continue is not False and not error
                loop_status.next_prompt = str(prompt or "").strip() if keep else ""
            request = getattr(chat_ui, "request_bottom_redraw", None)
            if callable(request):
                request()
        except Exception:
            _logger.debug("更新下一轮提词失败（非关键）", exc_info=True)

    return {
        "on_start": _on_round_start,
        "on_end": _on_round_end,
        "on_title": _on_title_updated,
        "on_title_generating": _on_title_generating,
        "on_loop_prompt_generating": _on_loop_prompt_generating,
        "on_loop_prompt_generated": _on_loop_prompt_generated,
    }


def _register_session_handlers(
    session,
    monitor: EscapeMonitor,
    loop_state: dict | None = None,
    chat_ui=None,
) -> None:
    """注册会话生命周期回调（round_start / round_end）。

    每次调用前先移除旧回调（如果 loop_state 中缓存了引用），
    防止恢复路径中重复注册导致回调累加。

    2026-08-19：会话就绪时向 chat_ui 注入 agent 消息列表访问器——轨迹视图
    （Ctrl+H）以真实会话消息（system/user/assistant+tool_calls/tool 返回）
    为数据源（非 TUI 渲染过的聊天块）。
    """
    if loop_state is None:
        loop_state = {}
    # ★ 轨迹视图消息源注入（幂等：重复注册覆盖为最新 session）
    if chat_ui is not None and hasattr(chat_ui, "set_message_source"):
        try:
            chat_ui.set_message_source(lambda: session.messages)
        except Exception:
            _logger.debug("注入轨迹消息源异常", exc_info=True)
    # ★ 轨迹检查器沙盒关联（2026-10）：注入文件沙盒访问器——选中 tool 记录
    #   时右栏显示该工具调用产生的文件变更（按消息索引 + 工具名匹配）。
    if chat_ui is not None and hasattr(chat_ui, "set_sandbox_source"):
        try:
            from ..core.sandbox_manager import get_sandbox_manager

            chat_ui.set_sandbox_source(get_sandbox_manager)
        except Exception:
            _logger.debug("注入轨迹沙盒源异常", exc_info=True)
    # ★ 会话日志视图实时刷新器注入（幂等：重复注册覆盖为最新 session）——
    #   F12 / ``/logs`` 打开后视图渲染期每帧调用（签名未变零重建），会话日志
    #   增长（用户/助手消息、工具结果、轮次步骤、结构变更）时自动重建并写回
    #   ``model.logs_view``；**流式输出期间亦可打开并实时跟进**（数据刷新不
    #   依赖命令线程处理，命令在生成期间本就会被排队）。
    if chat_ui is not None:
        try:
            model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        except Exception:
            model = None
        if model is not None and hasattr(model, "logs_view"):
            try:
                from ..core.commands._logs_cmd import make_logs_refresher

                def _apply_logs_state(data, _model=model):
                    state = getattr(_model, "logs_view", None)
                    if state is None:
                        return
                    for key, value in data.items():
                        setattr(state, key, value)
                    state.visible = True

                model.logs_refresher = make_logs_refresher(session, _apply_logs_state)
            except Exception:
                _logger.debug("注入会话日志刷新器异常", exc_info=True)
    # ★ 文件沙盒视图实时刷新器注入（幂等：重复注册覆盖为最新 session）——
    #   F11 / ``/changes`` 打开后视图渲染期每帧调用（数据签名
    #   未变零重建），沙盒记录增长（工具写文件、回滚、清空）时自动重建并写回
    #   各沙盒视图状态；**流式输出期间亦可打开并实时跟进**。
    if chat_ui is not None:
        try:
            model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        except Exception:
            model = None
        if model is not None and hasattr(model, "sandbox_view"):
            try:
                from ..core.commands._sandbox_cmd import (
                    consume_sandbox_actions,
                    make_sandbox_refresher,
                )

                def _apply_sandbox_state(data, _model=model):
                    files = data.get("files") or []
                    messages = data.get("messages") or []
                    records = data.get("records") or []
                    sections = data.get("sections") or []
                    cv = getattr(_model, "changes_view", None)
                    if cv is not None:
                        cv.entries = files
                        cv.messages = messages
                        cv.sections = sections
                        cv.visible = True
                    sv = getattr(_model, "sandbox_view", None)
                    if sv is not None:
                        sv.sections = sections
                        sv.visible = True
                    hv = getattr(_model, "sandbox_history_view", None)
                    if hv is not None:
                        hv.entries = messages
                        hv.visible = True
                    rv = getattr(_model, "sandbox_records_view", None)
                    if rv is not None:
                        rv.entries = records
                        rv.visible = True

                def _drain_sandbox_actions(sandbox, _model=model):
                    return consume_sandbox_actions(_model, sandbox)

                model.sandbox_refresher = make_sandbox_refresher(
                    session, _apply_sandbox_state, action_handler=_drain_sandbox_actions,
                )
            except Exception:
                _logger.debug("注入文件沙盒刷新器异常", exc_info=True)
    callbacks = _make_round_callbacks(session, monitor, loop_state, chat_ui)
    # 清除旧回调（如果存在），防止重复注册累加
    old_callbacks = loop_state.get("_registered_callbacks")
    if old_callbacks is not None:
        session.off("round_start", old_callbacks["on_start"])
        session.off("round_end", old_callbacks["on_end"])
        session.off("title_updated", old_callbacks["on_title"])
        session.off("title_generating", old_callbacks["on_title_generating"])
        old_generating = old_callbacks.get("on_loop_prompt_generating")
        if old_generating is not None:
            session.off("loop_prompt_generating", old_generating)
        old_generated = old_callbacks.get("on_loop_prompt_generated")
        if old_generated is not None:
            session.off("loop_prompt_generated", old_generated)
    loop_state["_registered_callbacks"] = callbacks
    session.on("round_start", callbacks["on_start"])
    session.on("round_end", callbacks["on_end"])
    session.on("title_updated", callbacks["on_title"])
    session.on("title_generating", callbacks["on_title_generating"])
    session.on("loop_prompt_generating", callbacks["on_loop_prompt_generating"])
    session.on("loop_prompt_generated", callbacks["on_loop_prompt_generated"])
