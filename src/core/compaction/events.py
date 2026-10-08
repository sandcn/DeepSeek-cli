"""压缩显示事件发布 — TUI 显示压缩过程（开始 / 完成 / 失败）。

事件经 ``EventPort.publish_event`` 发到显示总线；无事件端口（单元测试、
无 UI 场景）时静默跳过，不影响压缩主流程。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def publish_compaction_event(
    event_port,
    label: str,
    phase: str,
    *,
    count: int = 0,
    tokens: int = 0,
    saved_tokens: int = 0,
    detail: str = "",
    source: str = "context",
) -> None:
    """发布一条压缩状态显示事件（异常安全）。

    Args:
        event_port: EventPort（None 时跳过）。
        label: Agent 标识（主 Agent 为 "main"，SubAgent 为自身 label）。
        phase: ``"started"`` / ``"finished"`` / ``"failed"``。
        count: 本次压缩遮蔽的历史条数（finished 时有意义）。
        tokens: 释放的估算 token（finished）。
        saved_tokens: 摘要替代后净释放 token（finished）。
        detail: 失败原因或补充说明。
        source: 事件来源标识。
    """
    if event_port is None:
        return
    try:
        from ..events.display_types import CompactionChangedEvent

        event_port.publish_event(CompactionChangedEvent(
            label=label or "main",
            phase=phase,
            count=max(0, int(count or 0)),
            tokens=max(0, int(tokens or 0)),
            saved_tokens=max(0, int(saved_tokens or 0)),
            detail=str(detail or ""),
            source=source,
        ))
    except Exception:
        _logger.debug("发布压缩显示事件失败", exc_info=True)


__all__ = ["publish_compaction_event"]
