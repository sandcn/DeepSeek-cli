"""连接清理 — 统一管理 WebSocket 连接的资源释放

将 ws_handler/__init__.py 中的 _cleanup_connection 职责抽离为独立模块，
并提供给 routing.py 中的 ConnectionContext.cleanup() 使用。

包含：
- cleanup_connection — 通用清理函数
- cleanup_pending_selects — 清理残留的 PENDING_SELECTS

★ 页面刷新保护：清理时不会取消正在运行的 LLM 生成任务（current_task），
  而是将其转移到 session._orphaned_task 持久引用，让大模型在后台自然完成。
  新页面加载后，session.messages 中已积累的生成结果会被完整展示。
"""

from __future__ import annotations

import asyncio
import logging
import weakref

from ._pending_selects import pending_selects

_logger = logging.getLogger(__name__)


async def cleanup_connection(
    bridge=None,
    my_select_ids: set[str] | None = None,
    pending_send_tasks: set[asyncio.Task] | None = None,
    process_task: asyncio.Task | None = None,
    proc_state=None,
    session=None,
) -> None:
    """清理 WebSocket 连接的全部资源。

    按依赖顺序逐项清理，确保资源释放路径完整。

    ★ session 参数：当 session 不为 None 时，清理时会保留正在运行的
       LLM 生成任务（current_task），将其保存到 session._orphaned_task，
      使其在后台自然完成，新连接可接管其结果。
    """
    # 1. 取消 EventBus 订阅（先停事件流）
    if bridge is not None:
        try:
            bridge.unsubscribe()
        except Exception:
            _logger.exception("取消订阅异常")

    # ★ cost_update handler 取消订阅已移至 ConnectionContext.cleanup()，
    #   每个连接独立管理自己的 handler 引用，避免共享 session 属性覆盖导致的 handler 泄漏。

    # 2. 清理当前连接残留的 PENDING_SELECTS
    if my_select_ids is not None:
        cleanup_pending_selects(my_select_ids)

    # 3. 等待 pending send tasks 完成（确保消息不丢失）
    if pending_send_tasks:
        try:
            await asyncio.gather(*pending_send_tasks, return_exceptions=True)
        except Exception:
            _logger.exception("排空发送任务异常")

    # 4. 取消后台处理任务（当前无独立队列任务，保留接口兼容）
    if process_task is not None:
        try:
            process_task.cancel()
            await process_task
        except asyncio.CancelledError:
            pass
        except Exception:
            _logger.exception("取消处理任务异常")

    # 5. ★ 页面刷新保护：保留 LLM 生成任务，转移到 session 持久引用
    #    让大模型在后台自然完成，新页面加载后可看到完整结果。
    if proc_state is not None:
        try:
            current = getattr(proc_state, 'current_task', None)
            if current is not None and not current.done():
                _logger.info(
                    "WebSocket 断开但 LLM 仍在生成，转移到后台继续 (task=%s)",
                    hex(id(current)),
                )
                if session is not None:
                    session._orphaned_task = current
                    # 添加完成回调：自动清理引用 + 日志
                    # weakref.ref 避免闭包循环引用（Task→callback→session→Task）
                    _session_ref = weakref.ref(session)
                    def _on_orphan_done(t: asyncio.Task) -> None:
                        s = _session_ref()
                        if s is not None:
                            if not t.cancelled():
                                _logger.info("后台 LLM 生成完成，共 %d 条消息",
                                             len(s.messages))
                            s._orphaned_task = None
                    current.add_done_callback(_on_orphan_done)
                # 不 cancel，让任务自然完成
            else:
                _logger.debug("无运行中的 LLM 任务需保留")
        except Exception:
            _logger.exception("保留 LLM 任务异常")


def cleanup_pending_selects(my_select_ids: set[str]) -> None:
    """清理当前连接残留的 PENDING_SELECTS。

    确保所有尚未完成的 user_select Future 被正确取消，
    避免 Future 泄漏导致内存增长。
    """
    for sid in list(my_select_ids):
        pending_selects.resolve(sid, '{"selected": [], "action": "cancel"}')
    my_select_ids.clear()


__all__ = ["cleanup_connection", "cleanup_pending_selects"]
