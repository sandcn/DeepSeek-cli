"""CacheRegistry — 统一缓存失效入口（P2-3）。

背景：修复前渲染链路的缓存分散在多层，各自用不同键失效（props 引用、
Line 对象身份、stable_prefix、renderer prev、committed 前缀、measure
cache），没有统一入口；任何一处原地修改都可能静默留下陈旧缓存。

本模块提供**命名失效器注册表**：

  - ``register(name, invalidate)``：缓存拥有者登记失效函数；
  - ``invalidate(reason)``：按原因触发全部（或指定命名空间）失效，并递增
    ``epoch``（供 Frame/渲染器判断是否需要全量重校验）；
  - ``epoch``：全局缓存代际（resize / 清屏 / 主题切换等结构性变更递增）。

现有缓存仍按各自最优键命中；本注册表只保证「结构性变更时不存在漏失效」，
并把失效动作集中到一处可观测、可测试。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

_logger = logging.getLogger(__name__)


class CacheRegistry:
    """命名缓存失效器注册表（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, Callable[[], None]] = {}
        self._epoch = 0

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def register(self, name: str, invalidate: Callable[[], None]) -> None:
        with self._lock:
            self._entries[name] = invalidate

    def unregister(self, name: str) -> None:
        with self._lock:
            self._entries.pop(name, None)

    def invalidate(self, reason: str = "", names: list[str] | None = None) -> int:
        """触发失效；返回实际执行的失效器数量。

        Args:
            reason: 失效原因（resize / clear / theme / model_reset）。
            names: 仅失效指定名称；None 表示全部。
        """
        with self._lock:
            self._epoch += 1
            targets = (
                [(n, fn) for n, fn in self._entries.items() if n in names]
                if names is not None
                else list(self._entries.items())
            )
        count = 0
        for name, fn in targets:
            try:
                fn()
                count += 1
            except Exception:
                _logger.warning("缓存失效器异常: %s（reason=%s）", name, reason, exc_info=True)
        return count


__all__ = ["CacheRegistry"]
