"""DisplayEventBus — 兼容 re-export 层。

事件总线实现已下沉至核心层 ``core.events.display_bus``（解除 api/tools/core
→ tui 的反向依赖）。本模块保持旧导入路径 ``src.tui.events.event_bus`` 兼容
（表现层与测试引用），全部符号从核心层 re-export。
"""

from __future__ import annotations

from ...core.events.display_bus import (  # noqa: F401
    DisplayEventBus,
    EventHandler,
    _EXC_LOG_WINDOW,
    _last_exc_log,
)

__all__ = ["DisplayEventBus", "EventHandler"]
