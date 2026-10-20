"""InputPump — stdin 读取线程（P1-1 架构修复）。

背景：修复前 stdin 读取由渲染线程在每帧 ``_phase_process_input`` 中通过
``select(timeout=0)`` 轮询——渲染帧一旦变慢（大 markdown 重排 / resize /
全量重写），输入读取与处理都被串行推迟；输入延迟没有上界。

本模块把**原始字节读取**从渲染线程剥离：独立 daemon 线程持续 select/read，
把字节投递到 ``InputIO.pending`` 并唤醒渲染循环；渲染线程只负责解析与分发
（保持 hooks/router/模型单写者语义）。这样：

  - 渲染慢帧不再阻塞 OS 输入缓冲的排空；
  - 空闲时渲染循环阻塞在调度器 Event 上，输入到达即唤醒（不轮询）；
  - 输入解析仍在渲染线程，``use_input`` handler / 模型写入的线程归属不变。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

_logger = logging.getLogger(__name__)


class InputPump:
    """stdin 原始读取线程（幂等 start/stop）。"""

    def __init__(
        self,
        input_instance: Any,
        on_data: Callable[[], None],
        poll_interval: float = 0.05,
    ) -> None:
        self._input = input_instance
        self._on_data = on_data
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ink-input-pump", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        self._thread = None

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._input.poll_raw(self._poll_interval)
            except Exception:
                _logger.debug("InputPump 读取异常", exc_info=True)
                raw = b""
            if raw:
                try:
                    self._input.feed_raw(raw)
                except Exception:
                    _logger.debug("InputPump 投递异常", exc_info=True)
                    continue
                try:
                    self._on_data()
                except Exception:
                    _logger.debug("InputPump 唤醒回调异常", exc_info=True)


__all__ = ["InputPump"]
