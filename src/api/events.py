"""事件发布工具函数 — 统一管理 EventBus 事件发布。

集中所有事件发布逻辑，消除各模块中重复的 try-except 导入代码。
所有流式处理器统一通过此模块发布事件，降低新增事件类型的维护成本。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def publish_event(event_type: str, **kwargs) -> bool:
    """安全发布事件到默认 DisplayEventBus。

    当 ui.events 模块不可用时静默跳过，不产生副作用。

    Args:
        event_type: 事件类名（如 "PhaseDoneEvent", "ContentChunkEvent"）
        **kwargs: 事件构造函数参数

    Returns:
        True 发布成功，False 跳过（模块不可用或事件类型不存在）
    """
    try:
        from src.tui.events.event_bus import DisplayEventBus
        from src.tui.events import event_types as evt
        event_cls = getattr(evt, event_type, None)
        if event_cls is not None:
            DisplayEventBus.get_default().publish(event_cls(**kwargs))
            return True
        _logger.warning("事件类型 %s 在 event_types 中不存在", event_type)
    except ImportError:
        _logger.debug("UI events 模块不可用，事件发布跳过（非 Web/TUI 模式）")
    except Exception:
        _logger.exception("发布事件 %s 异常", event_type)
    return False
