"""会话状态机 — 形式化 ChatSession 的状态转换

消除布尔状态组合（_retry_pending 等），
用有限状态机统一管理会话生命周期。

状态图:
                                    initialize()
       ┌───────┐   ───────────────→ ┌────────┐
       │  INIT │                    │  IDLE  │ ←────── save()
       └───┬───┘                    └───┬────┘
           │                            │
           │                            │ run_round()
           │                            ▼
           │                        ┌────────┐
           │     retry()            │ RUNNING│
           │ ←──────────────────────└───┬────┘
           │                            │
           │                    ┌───────┴────────┐
           │                    │                │
           │                    ▼                ▼
           │              ┌──────────┐    ┌──────────┐
           │              │COMPLETED │    │INTERRUPTED│
           │              └────┬─────┘    └─────┬────┘
           │                   │                │
           │                   │ save()         │ resume / save
           │                   ▼                │
           │               ┌───────┐            │
           └──────────────→│  IDLE │←───────────┘
                            └───────┘
"""

from __future__ import annotations

import enum
import logging
import threading
import types
from typing import Callable

_logger = logging.getLogger(__name__)


class SessionState(enum.Enum):
    """会话状态枚举"""
    INIT = "init"               # 初始状态，尚未 initialize
    IDLE = "idle"               # 就绪，等待用户输入
    RUNNING = "running"         # 正在执行一轮对话
    COMPLETED = "completed"     # 对话完成（正常结束）
    INTERRUPTED = "interrupted"  # 被中断（等待恢复或 retry）


class InvalidTransitionError(Exception):
    """非法状态转换异常"""

    def __init__(self, state: SessionState, action: str):
        self.state = state
        self.action = action
        super().__init__(f"状态 '{state.value}' 不允许操作 '{action}'")


# ── 转换定义 ────────────────────────────────────────────────
# (from_state, action) → to_state
_TRANSITIONS = types.MappingProxyType({
    # INIT → IDLE (初始化完成)
    (SessionState.INIT, "initialize"): SessionState.IDLE,

    # IDLE → RUNNING (开始新一轮对话)
    (SessionState.IDLE, "run_round"): SessionState.RUNNING,

    # RUNNING → COMPLETED (正常完成)
    (SessionState.RUNNING, "complete"): SessionState.COMPLETED,
    # RUNNING → INTERRUPTED (被中断)
    (SessionState.RUNNING, "interrupt"): SessionState.INTERRUPTED,

    # COMPLETED → IDLE (保存)
    (SessionState.COMPLETED, "save"): SessionState.IDLE,
    # COMPLETED → RUNNING (重试不受限)
    (SessionState.COMPLETED, "retry"): SessionState.RUNNING,

    # INTERRUPTED → IDLE (保存后回到空闲)
    (SessionState.INTERRUPTED, "save"): SessionState.IDLE,
    # INTERRUPTED → RUNNING (重试/恢复)
    (SessionState.INTERRUPTED, "retry"): SessionState.RUNNING,

    # IDLE → IDLE (保存本身不改变活跃状态)
    (SessionState.IDLE, "save"): SessionState.IDLE,
    # IDLE → RUNNING (重新执行上一轮)
    (SessionState.IDLE, "retry"): SessionState.RUNNING,

    # 任意非 RUNNING 状态 → IDLE (清空对话)
    # ★ P0 修复: 移除 RUNNING→clear 转换，防止 LLM 生成期间消息被清空的竞态。
    #   需要清空时先 interrupt() 再 clear()。
    (SessionState.IDLE, "clear"): SessionState.IDLE,
    (SessionState.COMPLETED, "clear"): SessionState.IDLE,
    (SessionState.INTERRUPTED, "clear"): SessionState.IDLE,
})


class SessionStateMachine:
    """会话状态机 — 管理 ChatSession 的生命周期状态转换。

    线程安全（RLock），支持 on_enter 回调。
    on_exit / on_transition 已移除（YAGNI），如需使用可重新添加。
    """

    def __init__(self):
        self._state: SessionState = SessionState.INIT
        self._lock = threading.RLock()
        self._enter_handlers: dict[SessionState, list[Callable]] = {}

    # ── 属性 ──────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        """当前状态"""
        return self._state

    @property
    def name(self) -> str:
        """当前状态的字符串名称"""
        return self._state.value

    def is_(self, *states: SessionState) -> bool:
        """当前是否在指定状态之一"""
        return self._state in states

    # ── 注册回调 ──────────────────────────────────────────

    def on_enter(self, state: SessionState, callback: Callable) -> None:
        """注册进入某状态时的回调"""
        self._enter_handlers.setdefault(state, []).append(callback)

    # on_exit / on_transition 已移除（YAGNI），如需使用可重新添加
    # 参见 state_machine.py 历史版本或 git log

    # ── 转换 ──────────────────────────────────────────────

    def can(self, action: str) -> bool:
        """检查当前状态下是否允许执行某操作"""
        return (self._state, action) in _TRANSITIONS

    def transition(self, action: str, **context) -> SessionState:
        """执行状态转换

        Args:
            action: 操作名称
            **context: 传递给回调的上下文参数

        Returns:
            转换后的新状态

        Raises:
            InvalidTransitionError: 当前状态下不允许该操作
        """
        with self._lock:
            key = (self._state, action)
            if key not in _TRANSITIONS:
                raise InvalidTransitionError(self._state, action)

            old_state = self._state
            new_state = _TRANSITIONS[key]

            # 持锁期间：验证合法性 + 收集 on_enter 回调 + 执行状态变更
            enter_callbacks = list(self._enter_handlers.get(new_state, []))

            # 执行状态变更
            self._state = new_state

        # 释放锁后：无锁状态下执行 on_enter 回调
        for cb in enter_callbacks:
            try:
                cb(old_state, new_state, action=action, **context)
            except Exception:
                _logger.exception("状态机 on_enter 回调异常: %s → %s", old_state.value, new_state.value)

        _logger.debug("状态转换: %s → %s (action=%s)", old_state.value, new_state.value, action)

        return new_state

    # ── 便利方法 ──────────────────────────────────────────

    def initialize(self) -> None:
        """INIT → IDLE"""
        self.transition("initialize")

    def start_round(self) -> None:
        """开始一轮对话 (IDLE/COMPLETED/INTERRUPTED → RUNNING)"""
        self.transition("run_round")

    def complete_round(self) -> None:
        """完成一轮对话 (RUNNING → COMPLETED)"""
        self.transition("complete")

    def interrupt(self) -> None:
        """中断对话 (RUNNING → INTERRUPTED)"""
        self.transition("interrupt")

    def save(self) -> None:
        """保存后回到空闲 (COMPLETED/INTERRUPTED/IDLE → IDLE)"""
        self.transition("save")

    def retry(self) -> None:
        """重试 (COMPLETED/INTERRUPTED → RUNNING)"""
        self.transition("retry")

    def clear(self) -> None:
        """清空对话 → IDLE"""
        self.transition("clear")

    # ── 重置 ──────────────────────────────────────────────

    def reset(self) -> None:
        """重置到 INIT 状态"""
        with self._lock:
            old_state = self._state
            new_state = SessionState.INIT
            enter_callbacks = list(self._enter_handlers.get(new_state, []))
            self._state = new_state

        # 释放锁后执行回调（与 transition() 模式一致）
        for cb in enter_callbacks:
            try:
                cb(old_state, new_state, action="reset")
            except Exception:
                _logger.exception("状态机 reset on_enter 回调异常")

    # ── 字符串表示 ────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<SessionStateMachine: {self._state.value}>"



