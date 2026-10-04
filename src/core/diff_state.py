"""Diff 渲染状态原语 — 跨层共享的 diff 互斥标记（零依赖）。

``diff_active`` 标记 diff 渲染进行中，供刷新循环与 diff 渲染互斥。
定义于核心层，表现层 ``renderer.locks`` 与基础设施层 ``tools.file_base``
均从此导入，消除基础设施层对表现层锁模块的反向依赖。
"""

from __future__ import annotations

import threading

diff_active = threading.Event()

__all__ = ["diff_active"]
