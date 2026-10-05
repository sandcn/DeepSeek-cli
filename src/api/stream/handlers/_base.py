"""StreamChunkHandler — 流式 chunk 处理器基类。

将 ContentHandler 和 ReasoningHandler 共有的缓冲区累积 → 事件发布逻辑
提取为基类，消除重复代码。

子类只需定义：
  - _EVENT_TYPE:  str — 发布的事件类型名（如 "ContentChunkEvent"）
  - _MIN_CHARS:   int — 事件节流阈值（累积 ≥ MIN_CHARS 字符才发布，默认 1）
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

from ...events import publish_event

_logger = logging.getLogger(__name__)


class StreamChunkHandler(ABC):
    """流式 chunk 处理基类 — 自动累积文本并节流发布 EventBus 事件。

    ★ 定时器兜底（问题3）：时间节流命中（距上次 flush < ``_MIN_INTERVAL``）
    时不丢弃缓冲，而是调度一个后台定时器在剩余时间后强制 flush——避免
    「模型输出间隙（思考/工具调用前后）内最后一个 chunk 滞留缓冲区，直到
    下一个 chunk 或流结束才上屏」的显示抖动/延迟。
    """

    # 子类覆盖：定义发布的事件类型名
    _EVENT_TYPE: str = ""
    # 子类覆盖：定义事件节流阈值
    _MIN_CHARS: int = 1
    # 时间节流：100ms 间隔（10Hz），与 _MIN_CHARS 构成 AND 关系
    _MIN_INTERVAL: float = 0.1

    def __init__(self):
        self._chunk_buffer = ""
        self._last_flush_time: float = 0.0
        # 缓冲区 / 定时器锁（事件发布在 API 线程，定时器在后台线程）
        self._buf_lock = threading.Lock()
        self._timer_lock = threading.Lock()
        self._timer: threading.Timer | None = None

    # ── 子类需实现的抽象方法 ─────────────────────────────

    @abstractmethod
    def handle(self, ctx, text: str, token_est: int | None = None) -> None:
        """处理一个文本 chunk。

        Args:
            ctx: StreamContext 实例
            text: 文本增量
            token_est: 可选的 token 估计值
        """
        ...

    # ── 公共缓冲区管理 ───────────────────────────────────

    def buffer(self, text: str, label: str | None) -> None:
        """累积文本到缓冲区，达到阈值时自动发布事件。"""
        if not text:
            return
        with self._buf_lock:
            self._chunk_buffer += text
            ready = len(self._chunk_buffer) >= self._MIN_CHARS
        if ready:
            self._flush(label)

    def flush(self, label: str | None) -> None:
        """刷出剩余的缓冲事件（强制，不受时间节流限制）。"""
        self._flush(label, force=True)

    def _flush(self, label: str | None, force: bool = False) -> None:
        """发布累积的缓冲文本为 EventBus 事件。

        Args:
            force: 若为 True，跳过时间门控检查，强制 flush。
        """
        with self._buf_lock:
            if not self._chunk_buffer:
                return
            if not force:
                now = time.time()
                elapsed = now - self._last_flush_time
                if elapsed < self._MIN_INTERVAL:
                    # ★ 定时器兜底：剩余时间后强制 flush（不丢缓冲）
                    remaining = self._MIN_INTERVAL - elapsed
                    self._schedule_flush_timer(label, remaining)
                    return
                self._last_flush_time = now
            text = self._chunk_buffer
            self._chunk_buffer = ""
        publish_event(self._EVENT_TYPE, text=text, label=label or "")

    def _schedule_flush_timer(self, label: str | None, delay: float) -> None:
        """调度后台定时器在 delay 秒后强制 flush（已有存活定时器则跳过）。"""
        with self._timer_lock:
            if self._timer is not None and self._timer.is_alive():
                return
            timer = threading.Timer(max(0.0, delay), self._on_flush_timer, args=(label,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _on_flush_timer(self, label: str | None) -> None:
        """定时器回调：强制 flush 缓冲（异常不向定时器线程传播）。"""
        with self._timer_lock:
            self._timer = None
        try:
            self._flush(label, force=True)
        except Exception:
            _logger.debug("流式节流定时器 flush 异常", exc_info=True)
