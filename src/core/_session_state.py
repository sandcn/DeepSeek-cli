"""SessionState — 数据容器，持有 ChatSession 的可变状态。

职责：
- 作为单个真相源（Single Source of Truth）持有会话可变状态
- 提供 Hook 注册/注销/触发方法
- 提供 pending_messages 弹出方法

减少 ChatSession 中直接定义的状态字段数量，将其收拢到此数据容器中。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SessionState
# ═══════════════════════════════════════════════════════════════

@dataclass
class SessionState:
    """会话可变状态容器。

    此 dataclass 仅持有数据，不包含业务逻辑。
    Hook 方法和 pop_pending_messages 作为便捷委托方法保留在此处。
    """

    # ── 会话标识 ──────────────────────────────────────────
    session_id: str | None = None

    # ── 重试状态 ──────────────────────────────────────────
    retry_pending: bool = False

    # ── 排队消息缓冲区 ────────────────────────────────────
    pending_messages: list[str] = field(default_factory=list)

    # ── Hook 系统 ─────────────────────────────────────────
    hooks: dict[str, list[Callable]] = field(
        default_factory=lambda: defaultdict(list)
    )

    # ── LLM 生成期间捕获文本 ──────────────────────────────
    captured_prefill: str = ""

    # ── WebUI 页面刷新保护 ────────────────────────────────
    orphaned_task: asyncio.Task | None = None

    # ── run_round 并发锁（延迟初始化，避免绑定特定事件循环） ──
    _round_lock: asyncio.Lock | None = field(default=None, repr=False)

    @property
    def round_lock(self) -> asyncio.Lock:
        lock = getattr(self, '_round_lock', None)
        if lock is None:
            lock = asyncio.Lock()
            self._round_lock = lock
        return lock

    @round_lock.setter
    def round_lock(self, value: asyncio.Lock) -> None:
        self._round_lock = value

    # ── 序列化支持 ──────────────────────────────────────

    def __getstate__(self):
        """移除不可序列化字段后返回状态字典。"""
        state = self.__dict__.copy()
        state.pop('_round_lock', None)
        state.pop('orphaned_task', None)
        state.pop('hooks', None)
        return state

    def __setstate__(self, state):
        """恢复状态并重新创建不可序列化字段。"""
        self.__dict__.update(state)
        # round_lock 不再在此创建 — 由 round_lock property 首次访问时按需延迟初始化
        self.orphaned_task = None
        self.hooks = defaultdict(list)

    # ── 排队消息操作 ──────────────────────────────────────

    def pop_pending_messages(self) -> list[str]:
        """弹出并返回所有排队的用户消息。

        注意：此方法本身不是线程安全的。
        - pending_messages 的 append 在 run_round（持 round_lock）中执行
        - 调用方应在持有 round_lock 的上下文中调用此方法
        - 返回的列表是独立副本，修改不影响内部状态
        """
        msgs = list(self.pending_messages)
        self.pending_messages.clear()
        return msgs

    # ── Hook 系统 ─────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """注册事件回调。"""
        self.hooks[event].append(callback)

    def off(self, event: str, callback: Callable) -> None:
        """移除事件回调。"""
        handlers = self.hooks.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def _emit(self, event: str, **data) -> None:
        """触发事件，依次调用所有注册的回调。"""
        for cb in self.hooks.get(event, []):
            try:
                cb(**data)
            except Exception:
                _logger.exception("SessionState hook '%s' 异常", event)
