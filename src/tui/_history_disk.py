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

    线程安全：``queue.Queue`` 内部锁保护；``submit`` 非阻塞（队列满时丢弃并
    记 debug——不阻塞输入路径，与「写盘失败仅记日志」一致）。
    """

    #: 待写队列上限（条）——超出丢弃（历史写盘慢时防内存无限）
    _MAX_PENDING = 256

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=self._MAX_PENDING)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tui-history-disk-writer",
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # 哨兵退出（当前无生产调用方；保留供生命周期/测试）
                break
            history_io, escaped = item
            _safe_disk_append(history_io, escaped)

    def submit(self, history_io, escaped: str) -> None:
        try:
            self._queue.put((history_io, escaped), block=False)
        except queue.Full:
            _logger.debug("历史写盘队列已满，丢弃", exc_info=True)


#: 模块级共享 writer（daemon 线程，进程退出自动终止）
_HISTORY_DISK_WRITER = _HistoryDiskWriter()


__all__ = ["_safe_disk_append", "_HistoryDiskWriter", "_HISTORY_DISK_WRITER"]
