"""SessionState — 数据容器，持有 ChatSession 的可变状态。

职责：
- 作为单个真相源（Single Source of Truth）持有会话可变状态
- 持有 CoreEventBus 实例作为事件总线
- 提供 pending_messages 弹出方法

减少 ChatSession 中直接定义的状态字段数量，将其收拢到此数据容器中。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ...events import CoreEventBus


# ═══════════════════════════════════════════════════════════════
# SessionState
# ═══════════════════════════════════════════════════════════════

@dataclass
class SessionState:
    """会话可变状态容器。

    此 dataclass 仅持有数据，不包含业务逻辑。
    pop_pending_messages 作为便捷委托方法保留在此处。
    """

    # ── 会话标识 ──────────────────────────────────────────
    session_id: str | None = None

    # ── 重试状态 ──────────────────────────────────────────
    retry_pending: bool = False

    # ── 排队消息缓冲区 ────────────────────────────────────
    pending_messages: list[str] = field(default_factory=list)

    # ── Hook 系统 ─────────────────────────────────────────
    hooks: CoreEventBus = field(default_factory=CoreEventBus)

    # ── LLM 生成期间捕获文本 ──────────────────────────────
    captured_prefill: str = ""

    # ── 会话清理保护 ──────────────────────────────────────
    orphaned_task: asyncio.Task | None = None

    # ── run_round 并发锁 ──────────────────────────────────
    round_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── 排队消息操作 ──────────────────────────────────────

    def pop_pending_messages(self) -> list[str]:
        """弹出并返回所有排队的用户消息。"""
        msgs = list(self.pending_messages)
        self.pending_messages.clear()
        return msgs


