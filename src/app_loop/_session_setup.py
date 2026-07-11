"""应用主循环会话设置 — 从 app_loop.py 拆分

包含：SessionState / _RoundResult 数据类、会话初始化、回调工厂。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src._compat import dataclass

from ._utils import _non_system_messages

from ..core.session import ChatSession
from ..ui.colors import DIM, RESET
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
            non_system = _non_system_messages(session)
            from ..ui.tui._message_display import _display_messages
            _display_messages(non_system, session.agent, speed=0)
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
        # ★ 防御性检查：monitor 为 None 时跳过 drain 操作
        #   主路径中 monitor 由 _create_monitor 在 _register_session_handlers 之前创建，
        #   此检查作为兜底防御（异常路径/初始化顺序错误）
        if monitor is None:
            _logger.warning("_on_round_end: monitor 为 None，跳过 drain 操作")
            return

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
        queued, buffer_text = monitor.drain_stream_input()
        if queued is not None:
            loop_state["queued_input"] = queued
        elif buffer_text:
            clean = ''.join(c for c in buffer_text
                            if c.isprintable() or c in ('\n', '\t'))
            if clean:
                session.captured_prefill = clean

        # ★ 原有逻辑：保存非可打印控制字符
        captured = monitor.drain_captured_input()
        if captured:
            session.captured_prefill = captured

    return {"on_start": _on_round_start, "on_end": _on_round_end}


def _register_session_handlers(
    session,
    monitor: EscapeMonitor,
    loop_state: dict | None = None,
    chat_ui=None,
) -> None:
    """注册会话生命周期回调（round_start / round_end）"""
    if loop_state is None:
        loop_state = {}
    callbacks = _make_round_callbacks(session, monitor, loop_state, chat_ui)
    session.on("round_start", callbacks["on_start"])
    session.on("round_end", callbacks["on_end"])
