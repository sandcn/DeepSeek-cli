"""HistoryBuffer — 已提交历史的内存窗口（P2-5）。

背景：``AppModel.committed_lines`` 随会话线性增长，长会话内存与每帧 Frame
构造都是 O(全部历史)。终端本身已保存滚动历史，没必要在内存中永久保留全部
已提交行。

本模块提供 **窗口化 list**：

  - 超过 ``max_lines`` 时从头部丢弃最旧行（终端 scrollback 仍保留）；
  - 丢弃时回调 ``on_trim(drop)``，模型据此平移各块的
    ``_first_committed_offset``（已丢弃块标记失效，避免工具标题行错位）；
  - 源文本仍在 ``blocks[*].source_text``，resize 时可按源重排（窗口只影响
    内存中的渲染行，不改变会话内容）。

``max_lines=0`` 表示不限制（默认关闭，由 ``TuiConfig.history_window_lines``
注入）。
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

_logger = logging.getLogger(__name__)


class HistoryBuffer(list):
    """带最大行数窗口的 list（头部丢弃最旧行）。"""

    def __init__(
        self,
        iterable: Iterable = (),
        max_lines: int = 0,
        on_trim: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(iterable)
        self.max_lines = max(0, int(max_lines))
        self.on_trim = on_trim
        self.trimmed_total = 0
        self._trim()

    def _trim(self) -> int:
        if self.max_lines <= 0 or len(self) <= self.max_lines:
            return 0
        drop = len(self) - self.max_lines
        del self[:drop]
        self.trimmed_total += drop
        if self.on_trim is not None:
            try:
                self.on_trim(drop)
            except Exception:
                _logger.debug("HistoryBuffer.on_trim 异常", exc_info=True)
        return drop

    def append(self, item) -> None:  # type: ignore[override]
        super().append(item)
        self._trim()

    def extend(self, iterable) -> None:  # type: ignore[override]
        super().extend(iterable)
        self._trim()

    def insert(self, index, item) -> None:  # type: ignore[override]
        super().insert(index, item)
        self._trim()

    def __iadd__(self, other):  # type: ignore[override]
        result = super().__iadd__(other)
        self._trim()
        return result


__all__ = ["HistoryBuffer"]
