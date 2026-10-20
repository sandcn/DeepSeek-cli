"""兼容转发层 — 实现已下沉到 :mod:`src._win_console`（L0 基础设施层）。

为什么下沉：``src/tools/user_select.py`` 在原生 Windows 上需要探测
``ReadConsoleInputW`` 输入能力（``supported()``），但基础设施层（tools）
不得直接依赖表现层（tui）。实现放到 L0 的 ``src/_win_console.py`` 后，
tui 与 tools 都从那里导入；本模块保留原有导入路径
（``src.tui._win_console``），既有调用点（``ink/terminal``、
``_input_dispatcher``）无需改动。
"""

from __future__ import annotations

from .._win_console import (  # noqa: F401
    disable_raw,
    enable_raw,
    read_bytes,
    supported,
    translate_key_event,
)

__all__ = [
    "supported",
    "enable_raw",
    "disable_raw",
    "read_bytes",
    "translate_key_event",
]
