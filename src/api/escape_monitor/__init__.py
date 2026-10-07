"""Esc 键中断监听（包，原 escape_monitor.py 拆分）。

子模块：
- _history.py       —— 历史文件读写 + 模块级常量
- _monitor.py       —— EscapeMonitor 类 + 模块级导出函数

★ 启动性能（懒导出）：``EscapeMonitor`` 经 PEP 562 模块级 ``__getattr__``
惰性导入。``src.tui._input`` 只需要本包的 ``history`` 子模块（历史文件读写），
但 Python 导入 ``src.api.escape_monitor.history`` 会先执行本 ``__init__`` ——
若在导入期 eager 导入 ``_monitor`` 子模块，会连锁加载 ``interrupt_async`` /
``_compat_termios`` 等 TUI 输入路径并不需要的模块。改为惰性后，只有真正
构造/使用 ``EscapeMonitor`` 时才加载。
"""

from __future__ import annotations

from importlib import import_module

from ._registry import (
    get_active_monitor,
    stop_active_monitor,
    _active_monitor,
    _active_monitor_lock,
)
from .history import (
    _append_to_history_file,
    _compact_history_file,
    _read_history_file,
    _EOF_THRESHOLD,
    _SELECT_ERROR_THRESHOLD,
    _HISTORY_COMPACT_RATIO,
    _HISTORY_MAX_ENTRIES,
    INPUT_HISTORY_FILE,
)

#: 惰性导出表：公开名 → (子模块相对名, 子模块内属性名)
_LAZY_EXPORTS = {
    "EscapeMonitor": ("._monitor", "EscapeMonitor"),
}

__all__ = [
    "EscapeMonitor",
    "get_active_monitor",
    "stop_active_monitor",
    "_append_to_history_file",
    "_compact_history_file",
    "_read_history_file",
    "_EOF_THRESHOLD",
    "_SELECT_ERROR_THRESHOLD",
    "_HISTORY_COMPACT_RATIO",
    "_HISTORY_MAX_ENTRIES",
    "INPUT_HISTORY_FILE",
    "_active_monitor",
    "_active_monitor_lock",
]


def __getattr__(name: str):
    """PEP 562 模块级惰性属性解析（首次访问后缓存到模块命名空间）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
