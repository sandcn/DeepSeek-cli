"""流式输出生命周期回调 — 兼容 re-export 层。

实现已下沉核心层 ``core.stats._stream_lifecycle``（消除核心层对 api 的
反向依赖）；本模块保留旧路径 ``src.api._stream_lifecycle`` 兼容既有调用方/
测试，新代码请使用 ``src.core.stats``。

真源唯一（禁止双份实现漂移）：本模块不再自带副本，全部符号转发核心层实现。
"""

from __future__ import annotations

from ..core.stats._stream_lifecycle import (  # noqa: F401
    _notify_stream_ended,
    _notify_stream_progress,
    _notify_stream_started,
    set_stream_lifecycle_callbacks,
)

__all__ = [
    "set_stream_lifecycle_callbacks",
    "_notify_stream_started",
    "_notify_stream_ended",
    "_notify_stream_progress",
]
