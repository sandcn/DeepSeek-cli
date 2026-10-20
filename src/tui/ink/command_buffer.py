"""CommandBuffer — 自建渲染命令缓冲（P1-2 架构修复）。

背景：修复前 ``_SessionQueueMixin`` 直接操作 ``queue.PriorityQueue`` 的内部
结构（``mutex`` / ``queue`` 底层堆数组）实现合并、覆盖、腾位、排空，并手工
维护 ``unfinished_tasks``——与 stdlib 内部契约强耦合，已出现「任意下标 pop
破坏堆序（BUG-31）」「task_done 遗漏导致 join 永久等待」等缺陷。

本模块提供**显式契约**的有序命令缓冲：

  - 优先级 + 插入序（同优先级 FIFO）；
  - 同类型「最新值覆盖」（``replace_same_kind``，保持原位置/序）；
  - 流式命令尾部合并（``append_stream``，仅合并最新入队同类）；
  - 满队列时腾位（``evict_low``，仅移除低优先级项）；
  - 排空保留内容命令（``drain``，按命令 id 集合）；
  - ``get_nowait`` / ``task_done`` / ``join`` 与 ``queue.Queue`` 语义一致，
    但完全由本模块维护，不再触碰 stdlib 私有字段。

为兼容既有测试/观测，``queue`` 属性返回底层条目快照
``[(priority, seq, cmd), ...]``，``unfinished_tasks`` 为公开只读属性。
"""

from __future__ import annotations

import heapq
import itertools
import queue
import threading
from typing import Any, Callable, Iterable


class CommandBuffer:
    """优先级 + 插入序的渲染命令缓冲（线程安全）。"""

    def __init__(self, maxsize: int = 10000) -> None:
        self._maxsize = max(0, int(maxsize))
        self._heap: list[tuple[int, int, Any]] = []
        self._seq = itertools.count()
        self._unfinished = 0
        self._mutex = threading.Lock()
        self._not_empty = threading.Condition(self._mutex)
        self._not_full = threading.Condition(self._mutex)
        self._all_done = threading.Condition(self._mutex)

    # ── 观测（兼容 queue.Queue 子集） ─────────────────

    def qsize(self) -> int:
        with self._mutex:
            return len(self._heap)

    def empty(self) -> bool:
        with self._mutex:
            return not self._heap

    @property
    def full(self) -> bool:
        with self._mutex:
            return self._maxsize > 0 and len(self._heap) >= self._maxsize

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def unfinished_tasks(self) -> int:
        with self._mutex:
            return self._unfinished

    @property
    def mutex(self) -> threading.Lock:
        """兼容既有测试的可重入保护锁（勿在持锁时调用 put/get）。"""
        return self._mutex

    @property
    def queue(self) -> list[tuple[int, int, Any]]:
        """底层条目快照（仅供观测/测试；调用方不得原地修改）。"""
        with self._mutex:
            return list(self._heap)

    # ── 入队 ─────────────────────────────────────────

    def put(
        self,
        cmd: Any,
        priority: int,
        blocking: bool = False,
        timeout: float | None = None,
    ) -> bool:
        """入队。返回 False 表示队列满（blocking 时超时）。

        成功入队使 ``unfinished_tasks`` +1；调用方消费后必须 ``task_done``。
        """
        with self._not_full:
            if self._maxsize > 0:
                if not blocking:
                    if len(self._heap) >= self._maxsize:
                        return False
                elif not self._wait_not_full(timeout):
                    return False
            heapq.heappush(self._heap, (priority, next(self._seq), cmd))
            self._unfinished += 1
            self._not_empty.notify()
            return True

    def _wait_not_full(self, timeout: float | None) -> bool:
        if timeout is None:
            while self._maxsize > 0 and len(self._heap) >= self._maxsize:
                self._not_full.wait()
            return True
        remaining = max(0.0, timeout)
        while self._maxsize > 0 and len(self._heap) >= self._maxsize:
            if remaining <= 0:
                return False
            self._not_full.wait(remaining)
            remaining = max(0.0, remaining - 0.05)
        return True

    def get_nowait(self) -> tuple[int, int, Any]:
        with self._not_empty:
            if not self._heap:
                raise queue.Empty
            item = heapq.heappop(self._heap)
            self._not_full.notify()
            return item

    def task_done(self) -> None:
        with self._all_done:
            if self._unfinished <= 0:
                raise ValueError("task_done() called too many times")
            self._unfinished -= 1
            if self._unfinished == 0:
                self._all_done.notify_all()

    def join(self, timeout: float | None = None) -> bool:
        """等待全部已入队命令被 task_done；超时返回 False。"""
        with self._all_done:
            if timeout is None:
                while self._unfinished > 0:
                    self._all_done.wait()
                return True
            return self._all_done.wait_for(lambda: self._unfinished == 0, timeout)

    # ── 合并 / 覆盖 / 腾位 / 排空 ─────────────────────

    def replace_same_kind(
        self,
        cmd_id: int,
        cmd: Any,
        get_id: Callable[[Any], int],
    ) -> bool:
        """就地替换队列中首个同类型命令（保留原 priority/seq）。"""
        with self._mutex:
            for i, item in enumerate(self._heap):
                if get_id(item[2]) == cmd_id:
                    self._heap[i] = (item[0], item[1], cmd)
                    return True
            return False

    def append_stream(
        self,
        cmd_id: int,
        cmd: Any,
        get_id: Callable[[Any], int],
        get_text: Callable[[Any], str],
        replace: Callable[[Any, str], Any],
        max_chars: int,
    ) -> bool:
        """把文本追加到「最新入队」的同类流式命令（不新增条目）。"""
        if not self._heap:
            return False
        with self._mutex:
            idx = max(range(len(self._heap)), key=lambda i: self._heap[i][1])
            item = self._heap[idx]
            if get_id(item[2]) != cmd_id:
                return False
            merged = (get_text(item[2]) or "") + (get_text(cmd) or "")
            if len(merged) > max_chars:
                return False
            try:
                new_cmd = replace(item[2], merged)
            except Exception:
                return False
            self._heap[idx] = (item[0], item[1], new_cmd)
            return True

    def evict_low(self, threshold: int, get_id: Callable[[Any], int]) -> Any | None:
        """移除至多一个 priority >= threshold 的命令（腾位），返回被移除命令。"""
        with self._not_full:
            for i, item in enumerate(self._heap):
                if item[0] >= threshold:
                    removed = self._heap.pop(i)
                    heapq.heapify(self._heap)
                    if self._unfinished > 0:
                        self._unfinished -= 1
                    self._not_full.notify()
                    if self._unfinished == 0:
                        self._all_done.notify_all()
                    return removed[2]
            return None

    def drain(
        self,
        keep_ids: Iterable[int] | None = None,
        get_id: Callable[[Any], int] | None = None,
    ) -> tuple[int, int]:
        """排空队列；``keep_ids`` 非空时保留这些命令（原地不动）。

        Returns:
            (dropped, kept) 条数。
        """
        keep = set(keep_ids or ())
        with self._not_full:
            if not keep or get_id is None:
                dropped = len(self._heap)
                kept = 0
                self._heap.clear()
            else:
                kept_items = [it for it in self._heap if get_id(it[2]) in keep]
                dropped = len(self._heap) - len(kept_items)
                kept = len(kept_items)
                self._heap[:] = kept_items
                heapq.heapify(self._heap)
            if dropped:
                self._unfinished = max(0, self._unfinished - dropped)
                self._not_full.notify_all()
                if self._unfinished == 0:
                    self._all_done.notify_all()
        return dropped, kept


__all__ = ["CommandBuffer"]
