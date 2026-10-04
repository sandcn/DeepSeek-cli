"""显示事件发布门面 — 兼容 re-export 层。

发布实现与 DisplayEventBus 已下沉至核心层（``core.events.publish`` /
``core.events.display_bus``），解除 api/tools/core → tui 的反向依赖。
本模块保持旧导入路径 ``src.tui.events.publish`` 兼容（表现层与测试引用），
全部符号从核心层 re-export。新增发布代码请直接使用 ``src.core.events.publish``。
"""

from __future__ import annotations

from ...core.events.publish import (  # noqa: F401
    default_bus,
    emit,
    publish_output,
    publish_tool_summary,
)

__all__ = ["emit", "default_bus", "publish_output", "publish_tool_summary"]
