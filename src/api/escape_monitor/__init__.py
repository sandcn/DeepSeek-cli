"""Esc 键中断监听（包，原 escape_monitor.py 拆分）。

子模块：
- _history.py       —— 历史文件读写 + 模块级常量
- _input_handler.py —— StreamInputHandler 类
- _monitor.py       —— EscapeMonitor 类 + 模块级导出函数
"""

from __future__ import annotations

from ._monitor import (
    EscapeMonitor,
    get_active_monitor,
    stop_active_monitor,
    _active_monitor,
    _active_monitor_lock,
)
from ._input_handler import StreamInputHandler
from ._history import (
    _append_to_history_file,
    _compact_history_file,
    _read_history_file,
    _HISTORY_COMPACT_RATIO,
    _HISTORY_MAX_ENTRIES,
    INPUT_HISTORY_FILE,
)

__all__ = [
    "EscapeMonitor",
    "StreamInputHandler",
    "get_active_monitor",
    "stop_active_monitor",
    "_append_to_history_file",
    "_compact_history_file",
    "_read_history_file",
    "_HISTORY_COMPACT_RATIO",
    "_HISTORY_MAX_ENTRIES",
    "INPUT_HISTORY_FILE",
    "_active_monitor",
    "_active_monitor_lock",
]
