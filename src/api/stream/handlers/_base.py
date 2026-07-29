"""StreamChunkHandler — 流式 chunk 处理器基类。

将 ContentHandler 和 ReasoningHandler 共有的缓冲区累积 → 事件发布逻辑
提取为基类，消除重复代码。

子类只需定义：
  - _EVENT_TYPE:  str — 发布的事件类型名（如 "ContentChunkEvent"）
  - _MIN_CHARS:   int — 事件节流阈值（累积 ≥ MIN_CHARS 字符才发布，默认 1）
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ...events import publish_event


class StreamChunkHandler(ABC):
    """流式 chunk 处理基类 — 自动累积文本并节流发布 EventBus 事件。"""

    # 子类覆盖：定义发布的事件类型名
    _EVENT_TYPE: str = ""
    # 子类覆盖：定义事件节流阈值
    _MIN_CHARS: int = 1
    # 时间节流：100ms 间隔（10Hz），与 _MIN_CHARS 构成 AND 关系
    _MIN_INTERVAL: float = 0.1

    def __init__(self):
        self._chunk_buffer = ""
        self._last_flush_time: float = 0.0

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
        self._chunk_buffer += text
        if len(self._chunk_buffer) >= self._MIN_CHARS:
            self._flush(label)

    def flush(self, label: str | None) -> None:
        """刷出剩余的缓冲事件（强制，不受时间节流限制）。"""
        self._flush(label, force=True)

    def _flush(self, label: str | None, force: bool = False) -> None:
        """发布累积的缓冲文本为 EventBus 事件。

        Args:
            force: 若为 True，跳过时间门控检查，强制 flush。
        """
        if not self._chunk_buffer:
            return
        if not force:
            now = time.time()
            if now - self._last_flush_time < self._MIN_INTERVAL:
                return  # 时间间隔不足 100ms，不 flush，buffer 继续累积
            self._last_flush_time = now
        text = self._chunk_buffer
        self._chunk_buffer = ""
        publish_event(self._EVENT_TYPE, text=text, label=label or "")
