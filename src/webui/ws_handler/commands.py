"""Web 命令处理 — WebSocket 环境下的命令执行

包含 /clear /retry 等命令在 Web UI 环境下的处理逻辑。
"""

from __future__ import annotations

import asyncio
import logging

from ...core.commands import handle_command as _handle_cmd
from ...api.interrupt_async import reset_interrupt_async
from ...notifications import async_notify_chat_completed
from .utils import _WebCmdCtx

_logger = logging.getLogger(__name__)


async def _run_web_command(content: str, cmd_name: str, session, ws_send, msg_idx_state, proc_state) -> bool:
    """在 Web UI 环境中执行命令。

    返回:
        True — 命令已处理
        False — 命令未知，由调用方处理
    """
    ctx = _WebCmdCtx(session.messages, session)
    parts = content.split(maxsplit=1)
    ctx.arg = parts[1].strip() if len(parts) > 1 else ""

    try:
        # ★ 直接同步调用，不在线程池中执行。
        #   _handle_cmd 会直接修改 session.messages（通过 ctx.messages），
        #   线程池中执行会导致竞态条件。
        #   命令处理逻辑轻量（仅 list/dict 操作），不阻塞事件循环。
        cmd_handled = _handle_cmd(
            content, ctx.messages, ctx.state,
            ctx.build_system_prompt, ctx.get_user_input, ctx.context_manager,
            session,
        )
    except Exception as exc:
        _logger.exception("命令执行异常: %s", content)
        await ws_send({
            "type": "command_error",
            "command": content,
            "error": str(exc),
        })
        return True

    if not cmd_handled:
        return False

    # 同步 model
    new_model = ctx.state.get("model")
    if new_model and new_model != session.model:
        session.model = new_model

    if cmd_name == "/clear":
        msg_idx_state.reset()
        await ws_send({
            "type": "clear_messages",
        })

    # retry 信号
    if ctx.state.get("retry"):
        ctx.state["retry"] = False
        reset_interrupt_async()
        try:
            retry_result: dict | None = None
            proc_state.current_task = asyncio.create_task(session.retry())
            retry_result = await proc_state.current_task
        except asyncio.CancelledError:
            _logger.info("对话轮次被取消")
            # ★ 页面刷新保护：将运行中的 LLM 任务转移到 session 持久引用
            if proc_state.current_task is not None and not proc_state.current_task.done():
                session._orphaned_task = proc_state.current_task
                _logger.info("LLM 生成任务已转移到后台继续执行 (task=%s)",
                             hex(id(proc_state.current_task)))
            # ★ 直接 return，不通过 finally 中的 DesktopNotifier 路径
            #   取消时生成不完整，推送通知是错误的
            return
        except Exception:
            _logger.exception("对话轮次异常")
        finally:
            # ★ 仅在没有后台孤儿任务时才清空引用
            # 先将 orphaned_task 取到本地，减少跨协程属性访问
            orphaned = session._orphaned_task
            current = proc_state.current_task
            if not (orphaned is not None and current is not None
                    and orphaned is current and not current.done()):
                proc_state.current_task = None
        # ★ 仅在非取消路径上发送完成通知
        #   CancelledError 分支已 return，不会到达此处
        try:
            await async_notify_chat_completed(session.messages,
                                               elapsed=retry_result.get("elapsed") if isinstance(retry_result, dict) else None)
        except Exception:
            _logger.warning("发送对话完成通知失败", exc_info=True)

    return True


__all__ = ["_run_web_command"]
