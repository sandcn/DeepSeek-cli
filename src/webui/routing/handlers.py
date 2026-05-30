"""消息处理函数 + 路由表

将 routing.py 中各消息类型的 handler 函数和路由表抽离为独立模块，
保持 routing/ 子包中各模块的单一职责。

路由表 WS_MESSAGE_HANDLERS 集中注册了所有消息类型 → 处理函数的映射。

用户消息处理直接创建后台 task 执行 run_round，复用 CLI 的消息处理模式
（无独立消息队列，消息处理流程与 app_loop._handle_round 一致）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from src._compat import dataclass
from typing import TYPE_CHECKING, Callable

from ...api.interrupt_async import request_interrupt_async, reset_interrupt_async
from ...api.stats import get_token_stats, get_total_tokens, get_short_window_speed
from ...chat_msgs import delete_session as _delete_session
from ...chat_msgs import rename_session as _rename_session
from ...chat_msgs import load_session
from ...config import MODELS
from ...core.constants import filter_non_system
from ...core.sandbox_manager import get_sandbox_manager
from ...notifications import async_notify_chat_completed
from .._pending_selects import pending_selects
from ..msg_index import assign_msg_index
from .._termux import close_browsers
from ..ws_handler.commands import _run_web_command
from ..ws_handler.sandbox import _handle_sandbox_message
from ..ws_handler.sandbox import build_sandbox_updated
from ..ws_handler.edit import _handle_get_messages, _handle_edit_messages_action
from ..ws_handler.utils import _rebuild_message_indices
from ..types import msg_sessions_list, msg_session_deleted, msg_session_loaded
from ...ui._lock import locked_print

if TYPE_CHECKING:
    from .context import ConnectionContext

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Token 统计轮询（run_round 执行期间向前端推送用量）
# ═══════════════════════════════════════════════════════════════

# 超时/熔断常量
_STOP_TIMEOUT = 2.0          # stop_generating 等待 task_ready 超时（秒）
_PENDING_LOOP_MAX_ITER = 10  # 排队消息处理熔断阈值
_PROCESS_TASK_TIMEOUT = 5.0  # process_task 等待取消超时（秒）

# 从 ws_handler 包导入共享常量
from ..ws_handler import _MESSAGE_PREVIEW_LENGTH
# 从 types 导入消息长度上限常量（原 _MAX_MSG_LENGTH 已迁移到 types.py）
from ..types import _MAX_MSG_LENGTH


@dataclass(slots=True)
class _TokenStatsState:
    """追踪 token 统计状态，有变化时才向前端推送。"""
    last_input: int = -1
    last_output: int = -1
    last_speed: float = -1.0


""" ★ 2026-05-18 共享 token 统计轮询 (v2)
    所有 WebSocket 连接共享一个轮询任务，避免 N 个连接 N 倍轮询开销。

    设计要点：
    - _poll_ws_sends: set — 注册的 ws_send 回调集合
    - _poll_shared_task: asyncio.Task | None — 共享轮询任务
    - _poll_stop_event: asyncio.Event | None — 停止信号，由最后一个退出的
      连接设置，轮询循环自然检查此信号后退出，消除取消任务的竞态条件。
      新连接在停止信号设置后加入时，循环会检测到并清除信号继续运行。
"""
_poll_ws_sends: set = set()
_poll_shared_task: asyncio.Task | None = None
_poll_stop_event: asyncio.Event | None = None
# ★ 保护共享轮询状态的锁，防止多个连接同时注册/注销时的 TOCTOU 竞态
_poll_lock: asyncio.Lock = asyncio.Lock()


async def _poll_token_stats(ws_send, interval: float = 0.5) -> None:
    """注册 ws_send 到共享轮询器。

    第一个注册者启动共享轮询任务，后续注册者复用已有任务。
    所有 ws_send 注销后轮询任务自动停止。

    安全退出路径：
    1. _web_consumer finally 块取消此协程
    2. 取消后从 _poll_ws_sends 注销自身
    3. 若成为最后一个活跃连接，设置 _poll_stop_event
    4. _shared_poll_loop 自然检测到事件后退出（无需暴力 cancel）
    """
    global _poll_shared_task, _poll_stop_event
    async with _poll_lock:
        _poll_ws_sends.add(ws_send)

        if _poll_shared_task is None:
            _poll_stop_event = asyncio.Event()
            _poll_shared_task = asyncio.create_task(_shared_poll_loop(interval))

    try:
        # 保持此协程存活直到被取消
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        async with _poll_lock:
            _poll_ws_sends.discard(ws_send)
            # 最后一个连接退出时通知共享循环
            if not _poll_ws_sends and _poll_stop_event is not None:
                _poll_stop_event.set()


async def _shared_poll_loop(interval: float = 0.5) -> None:
    """共享轮询工作器：读取全局 token 统计，广播到所有注册的 ws_send。

    通过 _poll_stop_event 实现安全退出：
    - 每次迭代前检查事件是否已设置
    - 事件设置且无活跃连接时退出
    - 事件设置但又有新连接加入时，清除事件继续运行
    """
    global _poll_shared_task, _poll_stop_event
    state = _TokenStatsState()
    stop_event = _poll_stop_event

    try:
        while True:
            # ── 检查停止信号 ──────────────────────────
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass  # 超时是正常的轮询间隔，无需处理
            else:
                # 事件已设置：检查是否真的没有活跃连接
                if not _poll_ws_sends:
                    break  # 所有连接已注销，安全退出
                # 新连接在事件设置后加入，清除事件继续运行
                stop_event.clear()
                continue

            # ── 执行轮询 ──────────────────────────────
            stats = get_token_stats()
            total_output = get_total_tokens()
            speed = round(get_short_window_speed(), 1)
            inp = stats.get("input", 0)
            if (inp != state.last_input or total_output != state.last_output
                    or speed != state.last_speed):
                state.last_input, state.last_output, state.last_speed = (
                    inp, total_output, speed
                )
                msg = {
                    "type": "usage_update",
                    "label": "main",
                    "usage": {
                        "input": inp,
                        "output": total_output,
                        "total": total_output,
                        "speed": speed,
                    },
                    "replace": True,
                }
                # 广播到所有活跃 ws_send，移除已断开的连接
                dead = set()
                for cb in list(_poll_ws_sends):
                    try:
                        await cb(msg)
                    except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
                        dead.add(cb)
                    except Exception:
                        _logger.exception("广播 usage_update 异常")
                if dead:
                    async with _poll_lock:
                        for cb in dead:
                            _poll_ws_sends.discard(cb)
    except asyncio.CancelledError:
        pass
    finally:
        async with _poll_lock:
            _poll_shared_task = None
            _poll_stop_event = None


# ═══════════════════════════════════════════════════════════════
# Handler 函数
# ═══════════════════════════════════════════════════════════════

async def _handle_exit_command(ctx: ConnectionContext) -> None:
    """处理 exit 命令：保存会话、关闭浏览器、关闭 WebSocket 连接。"""
    session = ctx.session
    ws = ctx.ws
    sid = session.save()
    load_cmd = f"/load {sid}" if sid else "/load <会话ID>"
    locked_print(f"\n  \033[32m会话已保存 (ID: {sid})\033[0m")
    locked_print(f"  \033[32m恢复此对话请运行命令: {load_cmd}\033[0m\n")

    # ── Termux：关闭浏览器（委托给 _termux 模块） ──
    await close_browsers()

    # 停止 MessageQueue 消费者（exit 命令后不再处理消息）
    if ctx.process_task is not None and not ctx.process_task.done():
        ctx.process_task.cancel()
        try:
            await asyncio.wait_for(ctx.process_task, timeout=_PROCESS_TASK_TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        ctx.process_task = None

    await ctx.ws_send({
        "type": "session_saved",
        "session_id": sid or "",
        "message": "会话已保存，连接即将关闭",
    })
    if ctx.drain_send_queue is not None:
        await ctx.drain_send_queue()
    await ws.close()
    ctx._exit_flag = True


async def _handle_user_message(data: dict, ctx: ConnectionContext) -> None:
    """用户消息：放入 core.message_queue.MessageQueue，由后台消费者串行处理。

    与 CLI app_loop 共用 core.message_queue.MessageQueue 机制，
    不再使用独立的 asyncio.Queue 或 create_task。
    """
    content = data.get("content", "").strip()
    if not content:
        return
    if content.lower() == "exit":
        await _handle_exit_command(ctx)
        return

    await ctx.message_queue.put(content)


def _make_web_consumer(ctx: ConnectionContext):
    """创建 WebUI 的 MessageQueue 消费者回调（与 _handle_user_content 逻辑对等）。

    MessageQueue.async_consume 会串行调用此回调处理每条用户消息。
    返回 async callable(msg) -> None。
    """
    session = ctx.session
    ws_send = ctx.ws_send
    # 在闭包之前提取必要子对象为本地变量，避免 WebSocket 断开后 ctx 被部分清理时访问无效状态
    msg_idx_state = ctx.msg_idx_state
    proc_state = ctx.proc_state

    async def _web_consumer(msg):
        """消费者回调：处理一条用户消息。"""
        content = msg.content

        # ── 消息大小限制 ──
        if len(content) > _MAX_MSG_LENGTH:
            await ws_send({
                "type": "command_output",
                "text": f"消息过长（{len(content)}字符），最大允许 {_MAX_MSG_LENGTH} 字符",
                "level": "error",
            })
            return

        # ── 处理 / 开头的命令 ──
        if content.startswith("/"):
            cmd_name = content.split()[0].lower()
            handled = await _run_web_command(
                content, cmd_name, session, ws_send,
                msg_idx_state, proc_state,
            )
            if handled:
                return
            await ws_send({
                "type": "command_output",
                "text": f"未知命令: {content}，输入 /help 查看可用命令",
                "level": "warning",
            })
            return

        # ── 普通用户消息 ──
        await _run_web_chat(content, ctx, session, ws_send)

    return _web_consumer


async def _run_web_chat(content: str, ctx: ConnectionContext, session, ws_send) -> None:
    """执行一轮普通用户消息的对话处理。

    包含消息索引分配、中断重置、状态弹窗、Token 轮询、
    run_round + run_pending_loop + 完成通知的完整流程。
    """
    await assign_msg_index({
        "type": "user_message",
        "content": content,
    }, ctx.msg_idx_state, session.messages, ws_send)

    reset_interrupt_async()

    await ws_send({
        "type": "status_popup",
        "action": "show",
        "text": "🤖 生成中...",
    })

    tok_poll_task = asyncio.create_task(_poll_token_stats(ws_send))

    try:
        # 支持排队消息自动处理 + 熔断保护（与 CLI 共用 session.run_pending_loop）
        ctx.proc_state.task_ready.clear()
        ctx.proc_state.current_task = asyncio.create_task(
            session.run_round(content),
        )
        ctx.proc_state.task_ready.set()
        result = await ctx.proc_state.current_task
        _logger.debug("对话轮次完成: interrupted=%s",
                      result.get("interrupted", False) if isinstance(result, dict) else False)

        # 自动处理排队消息（复用公共方法）
        breached, _ = await session.run_pending_loop(max_iter=_PENDING_LOOP_MAX_ITER)
        if breached:
            _logger.error("排队消息处理超过熔断阈值，终止循环")
            await ws_send({
                "type": "command_output",
                "text": "系统繁忙，部分消息未能处理，请刷新页面重试",
                "level": "error",
            })

        await async_notify_chat_completed(session.messages,
                                           elapsed=result.get("elapsed") if isinstance(result, dict) else None)

    except asyncio.CancelledError:
        _logger.info("对话轮次被取消")
        if ctx.proc_state.current_task is not None and not ctx.proc_state.current_task.done():
            session._orphaned_task = ctx.proc_state.current_task
            _logger.info("LLM 生成任务已转移到后台继续执行 (task=%s)",
                         hex(id(ctx.proc_state.current_task)))
        return

    except Exception:
        _logger.exception("对话轮次异常")
        # run_pending_loop 内部异常时已自动将剩余消息重新入队到 session._pending_messages

    finally:
        tok_poll_task.cancel()
        try:
            await tok_poll_task
        except asyncio.CancelledError:
            pass
        await ws_send({
            "type": "status_popup",
            "action": "hide",
        })
        _orphaned = session._orphaned_task
        if (_orphaned is not None
                and not _orphaned.done()
                and _orphaned is ctx.proc_state.current_task):
            pass
        else:
            ctx.proc_state.current_task = None


async def _handle_user_select(data: dict, ctx: ConnectionContext) -> None:
    """用户选择：完成 Future。"""
    select_id = data.get("select_id", "")
    action = data.get("action", "confirmed")
    selected = data.get("selected", [])
    ctx.my_select_ids.discard(select_id)
    if select_id in pending_selects:
        future = pending_selects._pending.pop(select_id)
        if not future.done():
            future.set_result(json.dumps({
                "selected": selected,
                "action": action,
            }, ensure_ascii=False))


async def _handle_stop_generating(data: dict, ctx: ConnectionContext) -> None:
    """停止生成：中断当前对话轮次。"""
    request_interrupt_async()
    task = ctx.proc_state.current_task
    if task is None:
        try:
            await asyncio.wait_for(ctx.proc_state.task_ready.wait(), timeout=_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        task = ctx.proc_state.current_task
        # 二次检查：task 可能刚完成被清零
        if task is None:
            _logger.debug("stop_generating: 生成任务已自然完成")
            return
    if not task.done():
        task.cancel()


async def _handle_sandbox_get_files(data: dict, ctx: ConnectionContext) -> None:
    await _handle_sandbox_message(data, ctx.ws_send, ctx.session)


async def _handle_sandbox_file_diff(data: dict, ctx: ConnectionContext) -> None:
    await _handle_sandbox_message(data, ctx.ws_send, ctx.session)


async def _handle_get_messages_req(data: dict, ctx: ConnectionContext) -> None:
    await _handle_get_messages(ctx.ws_send, ctx.session)


async def _handle_edit_messages(data: dict, ctx: ConnectionContext) -> None:
    await _handle_edit_messages_action(
        data, ctx.ws_send, ctx.session,
        ctx.msg_idx_state, ctx.ws,
    )


async def _handle_get_full_state(data: dict, ctx: ConnectionContext) -> None:
    """返回完整会话状态（供重连后客户端同步使用）。"""
    session = ctx.session
    rebuilt = _rebuild_message_indices(session.messages)
    # 尝试从已保存的会话文件中读取标题
    session_title = ""
    if session.session_id:
        try:
            saved_data = await asyncio.to_thread(load_session, session.session_id)
            if saved_data:
                session_title = saved_data.get("title", "") or ""
        except Exception:
            _logger.debug("读取会话标题失败（重连时非关键）")
    await ctx.ws_send({
        "type": "full_state",
        "messages": rebuilt,
        "model": session.model,
        "title": session_title,
    })
    _logger.info("已同步全量状态 (%d 条消息) 到重连客户端", len(rebuilt))


async def _handle_get_models(data: dict, ctx: ConnectionContext) -> None:
    await ctx.ws_send({
        "type": "models_list",
        "models": MODELS,
        "current": ctx.session.model,
    })


async def _handle_set_model(data: dict, ctx: ConnectionContext) -> None:
    new_model = data.get("model", "")
    if new_model and new_model in MODELS:
        ctx.session.model = new_model
        # ★ P0 修复: 同步文件 I/O 移至线程池，避免阻塞事件循环
        await asyncio.to_thread(ctx.session.save)
        await ctx.ws_send({
            "type": "model_changed",
            "model": new_model,
        })
        _logger.info("WebUI 切换模型: %s", new_model)
    elif new_model:
        await ctx.ws_send({
            "type": "command_output",
            "text": f"无效的模型名称: {new_model}，可用模型: {', '.join(MODELS)}",
            "level": "warning",
        })


async def _handle_get_sessions(data: dict, ctx: ConnectionContext) -> None:
    """获取所有已保存的会话列表。"""
    sessions = ctx.session.list_sessions()
    current_id = ctx.session.session_id or ""
    await ctx.ws_send(msg_sessions_list(sessions, current_id))


async def _handle_delete_session(data: dict, ctx: ConnectionContext) -> None:
    """删除指定会话。"""
    session_id = data.get("session_id", "")
    if not session_id:
        return
    success = _delete_session(session_id)
    if success:
        await ctx.ws_send(msg_session_deleted(session_id))
    else:
        await ctx.ws_send({
            "type": "command_output",
            "text": f"删除会话 {session_id} 失败，会话文件不存在或无法删除",
            "level": "error",
        })


async def _handle_load_session(data: dict, ctx: ConnectionContext) -> None:
    """加载历史会话，替换当前对话内容。"""
    session_id = data.get("session_id", "")
    if not session_id:
        return
    if len(filter_non_system(ctx.session.messages)) > 0:
        if ctx.session.session_id:
            saved_sid = ctx.session.session_id
            ctx.session.session_id = None
            try:
                # ★ P0 修复: 同步文件 I/O 移至线程池，避免阻塞事件循环
                await asyncio.to_thread(ctx.session.save)
            finally:
                ctx.session.session_id = saved_sid
        else:
            ctx.session.save()
    result = ctx.session.load(session_id)
    if result is None:
        await ctx.ws_send({
            "type": "command_output",
            "text": f"会话 {session_id} 不存在",
            "level": "error",
        })
        return
    ctx.msg_idx_state.reset()
    rebuilt = _rebuild_message_indices(ctx.session.messages)
    await ctx.ws_send(msg_session_loaded(
        session_id=session_id,
        model=ctx.session.model,
        messages=rebuilt,
    ))
    await ctx.ws_send({
        "type": "model_changed",
        "model": ctx.session.model,
    })
    # 清空沙盒并推送计数（加载历史会话后沙盒记录已失效）
    sm = get_sandbox_manager()
    if sm:
        sm.clear()
    await ctx.ws_send(build_sandbox_updated())


async def _handle_rename_session(data: dict, ctx: ConnectionContext) -> None:
    """重命名当前会话标题。"""
    new_title = data.get("title", "").strip()
    if not new_title:
        return
    sid = ctx.session.session_id
    if not sid:
        await ctx.ws_send({
            "type": "command_output",
            "text": "当前会话尚未保存，无法重命名",
            "level": "warning",
        })
        return
    success = _rename_session(sid, new_title)
    if success:
        await ctx.ws_send({
            "type": "session_renamed",
            "session_id": sid,
            "title": new_title,
        })
        _logger.info("会话重命名: %s → %s", sid, new_title)
    else:
        await ctx.ws_send({
            "type": "command_output",
            "text": f"重命名会话 {sid} 失败",
            "level": "error",
        })


async def _handle_ping(data: dict, ctx: ConnectionContext) -> None:
    """处理客户端 keepalive ping，无操作。"""
    pass


# ═══════════════════════════════════════════════════════════════
# 路由表
# ═══════════════════════════════════════════════════════════════

WS_MESSAGE_HANDLERS: dict[str, Callable] = {
    "user_message": _handle_user_message,
    "user_select": _handle_user_select,
    "stop_generating": _handle_stop_generating,
    "get_sandbox_files": _handle_sandbox_get_files,
    "get_sandbox_file_diff": _handle_sandbox_file_diff,
    "get_messages": _handle_get_messages_req,
    "edit_messages_action": _handle_edit_messages,
    "get_full_state": _handle_get_full_state,
    "get_models": _handle_get_models,
    "set_model": _handle_set_model,
    "get_sessions": _handle_get_sessions,
    "delete_session": _handle_delete_session,
    "load_session": _handle_load_session,
    "rename_session": _handle_rename_session,
    "ping": _handle_ping,
}


__all__ = [
    "WS_MESSAGE_HANDLERS",
    "_handle_exit_command", "_handle_user_message",
    "_handle_user_select", "_handle_stop_generating",
    "_handle_ping",
    "_handle_rename_session",
    "_make_web_consumer",
    "_run_web_chat",
]
