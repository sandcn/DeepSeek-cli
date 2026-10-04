"""会话日志插件 — 提供 ``ctx.session_log``（会话的事实源）。

对应 DeepSeek Harness 的 ``core/session``：仅追加的 ``SessionEvent`` 日志与
内存存储。模型可见上下文由日志派生（``derive_messages``），fork / 恢复 /
回放 / 遥测都从同一份持久事实出发。

本服务提供日志的创建、恢复、快照、消息视图与一致性校验；运行时不变量插件
据此断言「模型可见即已记录」。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..core.session_log import LoggedMessageList, SessionLog, derive_messages
from ..kernel import Service, plugin


class SessionLogService(Service):
    """会话日志服务 — 占据 ``ctx.session_log``。"""

    provide = "session_log"
    name = "session_log"

    def create(self, session_id: Optional[str] = None) -> SessionLog:
        """创建新的空会话日志。"""
        return SessionLog(session_id=session_id)

    def restore(self, raw_events: Iterable[Dict[str, Any]], *, session_id: Optional[str] = None) -> SessionLog:
        """从序列化事件重建会话日志（恢复 / 回放）。"""
        return SessionLog.restore(raw_events, session_id=session_id)

    def fork(self, log: SessionLog, at: Optional[int] = None, *, session_id: Optional[str] = None) -> SessionLog:
        """在指定位置派生日志（会话 fork）。"""
        return log.fork(at, session_id=session_id)

    def view(self, log: Optional[SessionLog] = None, *, initial=None) -> LoggedMessageList:
        """创建由日志驱动的消息视图。"""
        return LoggedMessageList(log if log is not None else SessionLog(), initial=initial)

    def snapshot(self, log: SessionLog) -> List[Dict[str, Any]]:
        """序列化日志事件（持久化用）。"""
        return log.snapshot()

    def messages(self, log: SessionLog, *, projections=None) -> List[Dict[str, Any]]:
        """从日志投影模型历史。"""
        return log.derive_messages(projections)

    def verify(self, messages: Iterable[Dict[str, Any]], log: SessionLog, *, projections=None) -> bool:
        """校验消息列表与日志投影一致（「模型可见即已记录」）。"""
        return log.verify(messages, projections)

    def append(self, log: SessionLog, event_type: str, **data: Any):
        """追加一条会话事件。"""
        return log.append(event_type, **data)


@plugin("session_log", provide=["session_log"])
def apply(ctx):
    return SessionLogService(ctx)
