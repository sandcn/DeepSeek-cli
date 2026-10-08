"""通知 / 事件日志缓冲 — 供 TUI「通知 / 事件日志视图」读取。

记录三类事件（内存环形缓冲，上限 ``_MAX_ENTRIES``，进程内跨线程安全）：
  - ``notify``：已发送的桌面通知（``notify_chat_completed`` 成功后记录）；
  - ``notice``：运行期通知消息（TUI ``notification`` 块）；
  - ``error``：运行期错误消息（TUI ``error`` 块）。

设计：纯内存、无持久化（日志是运行期视图，重启即清），写入 O(1)，读取返回
快照拷贝。不依赖 UI 层（可被 core/tui 任一侧安全调用）。
"""

from __future__ import annotations

import threading
import time
from collections import deque

__all__ = ["record", "entries", "clear", "count", "MAX_ENTRIES"]

#: 缓冲上限（超出丢弃最旧条目）。
MAX_ENTRIES = 500

_lock = threading.Lock()
_entries: deque = deque(maxlen=MAX_ENTRIES)
_seq = 0


def record(kind: str, title: str, body: str = "", level: str = "info") -> dict:
    """追加一条日志并返回该条目（时间戳为 ``time.time()``）。"""
    global _seq
    with _lock:
        _seq += 1
        entry = {
            "seq": _seq,
            "kind": str(kind or "event"),
            "title": str(title or ""),
            "body": str(body or ""),
            "level": str(level or "info"),
            "time": time.time(),
        }
        _entries.append(entry)
        return entry


def entries() -> list:
    """当前日志条目快照（按记录顺序，最旧在前）。"""
    with _lock:
        return list(_entries)


def clear() -> None:
    """清空缓冲。"""
    with _lock:
        _entries.clear()


def count() -> int:
    """当前条目数。"""
    with _lock:
        return len(_entries)
