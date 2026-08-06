"""历史写盘 — 共享串行后台 writer（单 daemon 线程 + 有界队列）。

模块边界（2026-08-05 架构优化）：从 ``_input_buffer.py`` 拆分——历史持久化
为独立职责（后台线程 + 队列），与输入缓冲编辑（InputBufferEditor）解耦。
本模块无输入状态依赖，仅依赖 ``src.api.escape_monitor._history`` 的写盘
适配器协议（``history_io.append``）。

设计（review 方向，2026-08-05 线程模型收敛）：替代原「每 Enter 创建 daemon
线程」实现——高频 Enter/脚本粘贴多行时线程创建开销与磁盘竞争；共享单
daemon 线程 + 有界队列（串行有序）。

权衡说明（保留原设计注释语义）：写盘仍为异步 daemon——崩溃时未落盘历史
丢失窗口与退出冲刷复杂度同原实现（退出冲刷由 lifecycle flush 兜底），
仅收敛线程模型（线程复用 + 队列有界），不改变「不批量化」的决策。
"""

from __future__ import annotations

import logging
import queue
import threading

_logger = logging.getLogger(__name__)


def _safe_disk_append(history_io, escaped: str) -> None:
    """后台线程历史写盘（锁外执行；失败仅记日志，不抛回调用线程）。

    方向3（Enter fsync 阻塞渲染修复）：``os.fsync``（Android Termux ext4
    10-100ms）在渲染线程持锁执行会冻结所有缓冲编辑——迁移到后台 daemon
    线程。daemon 线程随进程退出自动终止；退出冲刷由 lifecycle flush 兜底。
    """
    try:
        if not history_io.append(escaped):
            _logger.warning("历史文件异步追加写入失败")
    except Exception:
        _logger.debug("历史文件异步追加异常", exc_info=True)


# ═══════════════════════════════════════════════════════════
# 共享串行历史写盘（review 方向：替代每 Enter 创建 daemon 线程）
# ═══════════════════════════════════════════════════════════

class _HistoryDiskWriter:
    """共享后台历史写盘（单 daemon 线程 + 有界队列，串行有序）。

    替代原「每 Enter 创建 daemon 线程」实现（高频 Enter/脚本粘贴多行时线程
    创建开销与磁盘竞争；有界队列防历史写盘慢时内存无限累积）。

    权衡说明（保留原设计注释语义）：写盘仍为异步 daemon——崩溃时未落盘历史
    丢失窗口与退出冲刷复杂度同原实现（退出冲刷由 lifecycle flush 兜底），
    仅收敛线程模型（线程复用 + 队列有界），不改变「不批量化」的决策。

    线程安全（P2-9 修复）：``queue.Queue`` 内部锁 + ``_submit_lock`` 互斥锁
    保护 flush/submit/线程重建的原子性——原实现无锁：flush 置哨兵后另一
    线程 submit 新条目可能排在哨兵之后，后台线程遇哨兵退出导致新条目滞留
    （历史静默丢失）。现 flush 置哨兵前先 ``get_nowait()`` 排空队列（现有
    条目同步写盘），submit 经 ``_sentinel_count`` 检查到哨兵残留时改同步
    写盘，不再滞留。``submit`` 非阻塞（队列满时丢弃并记 warning——不阻塞
    输入路径，与「写盘失败仅记日志」一致）。
    """

    #: 待写队列上限（条）——超出丢弃（历史写盘慢时防内存无限）
    _MAX_PENDING = 256

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=self._MAX_PENDING)
        # P2-9：flush/submit/线程重建互斥锁（保护哨兵计数与队列操作的原子性）
        self._submit_lock = threading.Lock()
        #: 队列中哨兵（None）数量——submit 检查到哨兵残留时改同步写盘
        self._sentinel_count: int = 0
        self._thread: threading.Thread | None = None
        try:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="tui-history-disk-writer",
            )
            self._thread.start()
        except Exception:
            # P3-3：线程启动降级——极端资源耗尽（线程创建失败）时记录 warning，
            # 后续 submit 改同步写盘（不丢历史、不使模块 import 级崩溃）。
            _logger.warning(
                "历史写盘后台线程启动失败，降级为同步写盘", exc_info=True,
            )
            self._thread = None

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # 哨兵退出（flush 置哨兵；重建时清理残留）
                break
            history_io, escaped = item
            _safe_disk_append(history_io, escaped)

    def submit(self, history_io, escaped: str) -> None:
        with self._submit_lock:
            if self._thread is None:
                # 线程启动失败降级 → 同步写盘（不丢历史）
                _safe_disk_append(history_io, escaped)
                return
            self._ensure_thread_locked()
            if self._sentinel_count > 0:
                # P2-9：队列中已有退出哨兵（flush 进行中/已完成）——新条目
                # 入队会排在哨兵之后，后台线程遇哨兵退出后滞留（历史静默
                # 丢失）。改同步写盘（flush 期间不排队）。
                _safe_disk_append(history_io, escaped)
                return
            try:
                self._queue.put((history_io, escaped), block=False)
            except queue.Full:
                # ★ 2026-08-06：队列满丢弃升级 warning——高频 Enter/慢盘时
                #   超出 256 条的历史被静默丢弃（仅 debug）用户不可见，历史
                #   丢失应可观测。
                _logger.warning(
                    "历史写盘队列已满（%d），丢弃条目（历史可能丢失）",
                    self._MAX_PENDING,
                )

    def _ensure_thread_locked(self) -> None:
        """确保后台写盘线程存活（flush 退出后自动重建）。**持锁调用**。

        ★ 2026-08-06：``flush()`` 置哨兵使线程退出后，若模块级 writer 仍被
        使用（lifecycle.stop 后再 start / 多 ChatUIConsumer 实例共享），
        ``submit`` 会把条目放入队列但无消费者 → 历史写盘静默失效。submit
        前检查线程存活，已死则重建——重建前先排空旧队列并清除残留哨兵
        （None），剩余真实条目放入新队列（串行有序，不滞留）。
        """
        if self._thread is None:
            return
        if not self._thread.is_alive():
            # 重建前清理队列残留：哨兵（None）丢弃，真实条目保留到新队列
            pending: list[tuple] = []
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self._sentinel_count -= 1
                else:
                    pending.append(item)
            new_queue: queue.Queue = queue.Queue(maxsize=self._MAX_PENDING)
            for item in pending:
                try:
                    new_queue.put(item, block=False)
                except queue.Full:
                    _logger.warning("重建历史写盘线程时队列溢出，丢弃条目")
            self._queue = new_queue
            try:
                self._thread = threading.Thread(
                    target=self._run, daemon=True,
                    name="tui-history-disk-writer",
                )
                self._thread.start()
            except Exception:
                # P3-3：重建失败同样降级——待写条目同步写掉（不丢历史）
                _logger.warning(
                    "历史写盘后台线程重建失败，降级为同步写盘", exc_info=True,
                )
                self._thread = None
                for item in pending:
                    history_io, escaped = item
                    _safe_disk_append(history_io, escaped)

    def flush(self, timeout: float = 2.0) -> bool:
        """排空待写队列并等待后台线程写入完成（退出冲刷接线，2026-08-06）。

        P2-9 修复：置哨兵前先 ``get_nowait()`` 排空队列（现有条目由本方法
        同步写盘），保证置哨兵时队列为空——修复前队列中可能残留条目排在
        哨兵前（flush 不等它们写完）或哨兵后（submit 竞态滞留）。置哨兵后
        的后续 submit 经 ``_sentinel_count`` 检查改同步写盘，不滞留。
        幂等：线程已退出（重复调用）时直接返回 True。

        Returns:
            True — 队列已排空且线程正常退出；
            False — 超时（线程仍存活，少量条目可能未落盘）。
        """
        if self._thread is None:
            # 线程启动失败降级（submit 均同步写盘）——无后台队列可冲刷
            return True
        if not self._thread.is_alive():
            return True
        with self._submit_lock:
            # 先排空队列：现有条目同步写盘、残留哨兵清除——保证置哨兵前
            # 队列为空（后续 submit 在锁内检查 _sentinel_count 走同步写）。
            drained: list[tuple] = []
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self._sentinel_count -= 1
                else:
                    drained.append(item)
            try:
                self._queue.put(None, block=True, timeout=timeout)
            except queue.Full:
                _logger.warning("历史写盘队列满，无法置退出哨兵（条目可能丢失）")
                return False
            self._sentinel_count += 1
        for history_io, escaped in drained:
            _safe_disk_append(history_io, escaped)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            _logger.warning("历史写盘线程 %s 秒内未退出", timeout)
            return False
        with self._submit_lock:
            # 哨兵已被后台线程消费 → 归零（后续 submit 正常入队）
            self._sentinel_count = 0
        return True


def flush_history_disk(timeout: float = 2.0) -> bool:
    """冲刷共享历史写盘队列（TuiLifecycle.stop 接线，2026-08-06）。

    修复前 ``_HistoryDiskWriter`` 无冲刷接口——注释声称「退出冲刷由 lifecycle
    flush 兜底」但实际无接线，daemon 线程随进程强制终止时队列中最多
    ``_MAX_PENDING`` 条未落盘历史丢失。
    """
    return _HISTORY_DISK_WRITER.flush(timeout=timeout)


#: 模块级共享 writer（daemon 线程，进程退出自动终止）
_HISTORY_DISK_WRITER = _HistoryDiskWriter()


__all__ = [
    "_safe_disk_append",
    "_HistoryDiskWriter",
    "_HISTORY_DISK_WRITER",
    "flush_history_disk",
]
