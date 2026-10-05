"""历史文件读写 — 兼容 re-export 层。

实现已迁移至公共模块 ``escape_monitor.history``（消除跨包引用私有模块的
命名不一致）。本模块保留旧路径 ``src.api.escape_monitor._history`` 兼容
既有调用方（表现层 tui 等）；新代码请使用 ``escape_monitor.history``。
"""

from __future__ import annotations

from .history import *  # noqa: F401,F403

from .history import (  # noqa: F401  （显式重导出，含下划线私有名）
    MONITOR_JOIN_TIMEOUT,
    MONITOR_START_JOIN_TIMEOUT,
    UNIX_SELECT_TIMEOUT,
    WINDOWS_POLL_INTERVAL,
    _EOF_THRESHOLD,
    _HISTORY_COMPACT_RATIO,
    _HISTORY_MAX_ENTRIES,
    _POLL_INTERVAL,
    _SELECT_ERROR_THRESHOLD,
    _append_to_history_file,
    _compact_history_file,
    _lock_history_file,
    _read_history_file,
    _unlock_history_file,
)
from ._registry import _active_monitor, _active_monitor_lock  # noqa: F401  （活跃实例注册表）
