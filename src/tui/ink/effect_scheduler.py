"""EffectScheduler — passive effects 帧尾调度（P1-4）。

背景：修复前 ``useEffect`` 与 ``useLayoutEffect`` 都在
``Reconciler.render`` 的提交期**同步执行**——effect 内的 IO/重计算会直接
阻塞渲染帧与输入处理，且与「绘制后执行」的 React 语义不一致。

本模块把 passive effects 收敛到帧尾队列：

  - ``useLayoutEffect`` 仍在布局后同步执行（React 语义：绘制前、可测量）；
  - ``useEffect`` 按 fiber 入队，由宿主在帧输出完成后调用 ``flush``（可设
    时间预算，超预算留到下一帧）；
  - effect 内 setState 通过既有 ``_schedule`` 请求下一帧，不会递归重入
    当前渲染。

无宿主（``renderToString`` / 单元测试）时 ``Reconciler`` 默认同步执行，
保持既有语义；``InkSession`` 构造的 reconciler 使用延迟模式。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

_logger = logging.getLogger(__name__)


class EffectScheduler:
    """passive effect 队列（帧尾执行，带单帧时间预算）。"""

    def __init__(self) -> None:
        self._queue: list[Any] = []

    def enqueue(self, fiber: Any) -> None:
        if any(f is fiber for f in self._queue):
            return
        self._queue.append(fiber)

    def __len__(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        self._queue.clear()

    def flush(
        self,
        runner: Callable[[Any], None],
        budget_ms: float = 8.0,
    ) -> int:
        """执行队列中的 passive effects；超预算的留到下一帧。

        Args:
            runner: ``(fiber) -> None`` 执行器（Reconciler._commit_live layout=False）。
            budget_ms: 单帧时间预算（毫秒）；0 表示不限制。

        Returns:
            本次执行的 fiber 数。
        """
        if not self._queue:
            return 0
        deadline = time.monotonic() + max(0.0, budget_ms) / 1000.0 if budget_ms else None
        count = 0
        pending: list[Any] = []
        for fiber in self._queue:
            if deadline is not None and time.monotonic() >= deadline:
                pending.append(fiber)
                continue
            try:
                runner(fiber)
                count += 1
            except Exception:
                _logger.warning(
                    "passive effect 执行异常 fiber=%s",
                    getattr(fiber, "type", None),
                    exc_info=True,
                )
        self._queue = pending
        return count


__all__ = ["EffectScheduler"]
