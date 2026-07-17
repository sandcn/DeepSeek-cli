"""
rich.Console 惰性初始化 — 导入本模块不会立即创建 Console 实例。
"""
from __future__ import annotations

import threading
from typing import Any, Dict

_console = None
_console_lock = threading.Lock()


def get_console():
    global _console
    if _console is None:
        with _console_lock:
            if _console is None:  # 双重检查
                from rich.console import Console
                from ...terminal import get_safe_console_config
                config: Dict[str, Any] = get_safe_console_config()
                _console = Console(**config)
    return _console
