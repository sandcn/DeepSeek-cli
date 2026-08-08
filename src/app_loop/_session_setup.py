"""应用主循环会话设置 — 从 app_loop.py 拆分

包含：SessionState / _RoundResult 数据类、会话初始化、回调工厂。
"""

from __future__ import annotations

import logging
from typing import Any

from src._compat import dataclass

from ._utils import _non_system_messages

from ..core.session import ChatSession
from ..core.constants import DIM, RESET
from ..api.stats import reset_token_speed
from ..api.escape_monitor import EscapeMonitor
from ..notifications import notify_chat_completed

_logger = logging.getLogger(__name__)


# ── 会话状态 ──

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
    """初始化会话并加载历史消息"""
    from ._single import _make_event_agent
    session = ChatSession(agent=_make_event_agent())
    session.initialize()

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

        # ★ Phase B：回合末强制结束未完成群组（成员因异常/取消未逐一 close）
        #   ——置未关闭成员 done 后最终化，避免群组卡永久 ● running 悬挂。
        if model is not None and hasattr(model, "flush_tool_groups"):
            try:
                model.flush_tool_groups()
            except Exception:
                _logger.debug("_on_round_end: flush_tool_groups 失败", exc_info=True)

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
            notify_chat_completed(session.messages, elapsed=notify_elapsed)

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

    return {"on_start": _on_round_start, "on_end": _on_round_end}


def _register_session_handlers(
    session,
    monitor: EscapeMonitor,
    loop_state: dict | None = None,
    chat_ui=None,
) -> None:
    """注册会话生命周期回调（round_start / round_end）。

    每次调用前先移除旧回调（如果 loop_state 中缓存了引用），
    防止恢复路径中重复注册导致回调累加。
    """
    if loop_state is None:
        loop_state = {}
    callbacks = _make_round_callbacks(session, monitor, loop_state, chat_ui)
    # 清除旧回调（如果存在），防止重复注册累加
    old_callbacks = loop_state.get("_registered_callbacks")
    if old_callbacks is not None:
        session.off("round_start", old_callbacks["on_start"])
        session.off("round_end", old_callbacks["on_end"])
    loop_state["_registered_callbacks"] = callbacks
    session.on("round_start", callbacks["on_start"])
    session.on("round_end", callbacks["on_end"])
