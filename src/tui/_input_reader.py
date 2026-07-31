"""InputReader — 独立 daemon 线程读取 stdin 原始字节。

将原始字节读取从 render 线程中分离到独立线程，
避免渲染卡顿时输入延迟。

架构：
  - InputReader 在独立 daemon 线程中持续读取 stdin 原始字节
  - 读取的字节放入线程安全 queue.Queue
  - Input.process_events() 从队列消费字节，不再直接读取 stdin
  - 无 InputReader 时降级为原有直接读取模式

设计原则：
  - 单一职责：仅负责原始字节读取，不参与 ANSI 解析
  - 零阻塞：主线程/渲染线程从不会在读取 stdin 上阻塞
  - 清理安全：daemon 线程自动随主线程退出
"""

from __future__ import annotations

import logging
import os
import queue
import select
import threading
from typing import Optional

_logger = logging.getLogger(__name__)


class InputReader:
    """独立 stdin 读取器 — daemon 线程读取原始字节。

    Attributes:
        fd: 要读取的文件描述符（通常为 sys.stdin.fileno()）。
        poll_interval: select 轮询间隔（秒），默认 0.01。
    """

    def __init__(
        self,
        fd: int,
        poll_interval: float = 0.01,
        max_read_size: int = 65536,
    ):
        """初始化 InputReader。

        Args:
            fd: 要读取的文件描述符。
            poll_interval: select 轮询间隔（秒）。
            max_read_size: 单次 os.read 最大字节数。
        """
        self._fd = fd
        self._poll_interval = poll_interval
        self._max_read_size = max_read_size
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> None:
        """启动 daemon 读取线程。幂等操作。"""
        with self._lock:
            if self._running:
                return
            if self._thread is not None and self._thread.is_alive():
                self._running = True
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            _logger.debug("InputReader 线程已启动 (fd=%d)", self._fd)

    def stop(self) -> None:
        """停止读取线程。幂等操作。"""
        with self._lock:
            self._running = False
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._running = False
            _logger.debug("InputReader 线程已停止")

    @property
    def is_alive(self) -> bool:
        """返回读取线程是否存活。"""
        return self._thread is not None and self._thread.is_alive()

    # ── 数据获取 ──────────────────────────────────────

    def get_raw(self) -> Optional[bytes]:
        """非阻塞获取原始字节数据。

        Returns:
            原始字节数据，无数据时返回 None。
        """
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def drain(self) -> list[bytes]:
        """排空所有待处理数据。

        Returns:
            所有待处理原始字节数据列表。
        """
        chunks: list[bytes] = []
        while True:
            chunk = self.get_raw()
            if chunk is None:
                break
            chunks.append(chunk)
        return chunks

    # ── 内部方法 ──────────────────────────────────────

    def _run(self) -> None:
        """daemon 线程主循环 — select 轮询 + os.read。"""
        while self._running:
            try:
                rlist, _, _ = select.select([self._fd], [], [], self._poll_interval)
                if rlist:
                    raw = os.read(self._fd, self._max_read_size)
                    if raw:
                        self._queue.put(raw)
            except (OSError, ValueError) as exc:
                # fd 可能已关闭或无效
                if self._running:
                    _logger.debug(
                        "InputReader 读取异常 (fd=%d): %s", self._fd, exc,
                    )
                    # 短暂休眠避免忙轮询
                    threading.Event().wait(0.1)
            except Exception:
                if self._running:
                    _logger.exception("InputReader 线程异常")


__all__ = ["InputReader"]
