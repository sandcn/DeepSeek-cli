"""ConnectionContext — WebSocket 连接上下文管理

统一管理连接生命周期内的全部资源，包括：
- aiohttp WebSocket 响应对象
- ChatSession / WebDisplay / WebEventBridge 等核心对象
- 后台任务（MessageQueue 消费、发送任务）
- 关闭信号与资源清理

用户消息通过 core.message_queue.MessageQueue 统一投递和消费，
与 CLI app_loop 共用同一消息队列机制。
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from ...core.session import ChatSession
from ...core.message_queue import MessageQueue
from ...chat_msgs import load_session
from ..ws_handler.sandbox import build_sandbox_updated
from ..ws_handler.utils import _rebuild_message_indices
from ..ws_handler.connection import _setup_connection
from .handlers import _make_web_consumer
from ..cleanup import cleanup_connection

_logger = logging.getLogger(__name__)


class ConnectionContext:
    """WebSocket 连接上下文 — 统一管理连接生命周期内的全部资源。

    取代 ws_handler/__init__.py 中的 ctx 裸字典，提供：
    - 类型化的属性访问
    - 统一的初始化接口（from_ws）
    - 统一的清理接口（cleanup）
    """

    def __init__(self):
        # ── 核心对象 ──
        self.session: ChatSession | None = None
        self.ws: web.WebSocketResponse | None = None
        self.ws_send = None  # Callable[[dict], Awaitable[None]]

        # ── 显示/事件层 ──
        self.msg_idx_state = None
        self.web_display = None
        self.bridge = None
        self.adapter = None

        # ── 任务管理 ──
        self.pending_send_tasks: set[asyncio.Task] | None = None
        self.process_task: asyncio.Task | None = None
        self.proc_state = None
        self.message_queue: MessageQueue | None = None

        # ── 用户交互 ──
        self.my_select_ids: set[str] | None = None

        # ── cost_update handler 引用（每个连接独立，避免共享 session 属性覆盖） ──
        self._cost_handler = None

        # ── 关闭信号 ──
        self.shutdown_event: asyncio.Event | None = None
        self._exit_flag: bool = False
        self.drain_send_queue = None  # Callable | None

    @classmethod
    async def from_ws(cls, request: web.Request) -> tuple["ConnectionContext", web.WebSocketResponse]:
        """从 aiohttp Request 构建完整的连接上下文。

        返回 (ctx, ws) 元组。
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        master_session: ChatSession = request.app["master_session"]

        # ★ P1-4 修复：每个 WebSocket 连接创建独立的 ChatSession
        #   避免多标签页共享同一会话导致消息交叉污染
        session = ChatSession(model=master_session.model)
        session.initialize()

        # ── 异步发送函数 ──
        async def ws_send(msg: dict) -> None:
            if not ws.closed:
                try:
                    await ws.send_json(msg)
                except (ConnectionResetError, ConnectionAbortedError):
                    _logger.debug("WebSocket 连接已断开")
                except Exception:
                    _logger.exception("WebSocket send 异常")

        # ── 连接初始化 ──
        msg_idx_state, web_display, bridge, adapter, pending_send_tasks, _drain_send_queue, cost_handler = _setup_connection(
            ws, session, ws_send,
        )

        # ── 孤儿任务注册表（页面刷新时跨会话传递后台 LLM 任务） ──
        orphaned_task_registry = request.app.get("orphaned_task_registry")

        # ── 发送初始状态 + 处理后台孤儿任务 ──
        await cls._send_initial_state(ws_send, session)
        await cls._handle_orphaned_task(ws_send, session, orphaned_task_registry)

        # ── 构建上下文 ──
        ctx = cls()
        ctx.session = session
        ctx.ws = ws
        ctx.ws_send = ws_send
        ctx.msg_idx_state = msg_idx_state
        ctx.web_display = web_display
        ctx.bridge = bridge
        ctx.adapter = adapter
        ctx.pending_send_tasks = pending_send_tasks
        ctx.my_select_ids = set()
        bridge.select_id_tracker = ctx.my_select_ids
        ctx._cost_handler = cost_handler
        ctx.shutdown_event = request.app.get("shutdown_event")
        ctx.orphaned_task_registry = orphaned_task_registry

        class _ProcState:
            def __init__(self):
                self.current_task: asyncio.Task | None = None
                self.task_ready: asyncio.Event = asyncio.Event()

        # ★ ★ P0 修复：proc_state 必须在 _web_consumer 创建前初始化，
        #    防止 async_consume 启动后 _web_consumer 访问 ctx.proc_state 为 None。
        ctx.proc_state = _ProcState()

        # ★ 创建 core.message_queue.MessageQueue（与 CLI 共用同一队列机制）。
        #   _handle_user_message 将用户消息放入队列，
        #   由后台 async_consume 任务串行消费。
        msg_queue = MessageQueue()
        ctx.message_queue = msg_queue
        ctx.process_task = asyncio.create_task(
            msg_queue.async_consume(_make_web_consumer(ctx)),
        )

        ctx.drain_send_queue = _drain_send_queue

        return ctx, ws

    @staticmethod
    async def _send_initial_state(ws_send, session: ChatSession) -> None:
        """发送初始状态（消息列表 + 沙盒计数），前端据此更新角标。"""
        rebuilt = _rebuild_message_indices(session.messages)
        # 尝试从已保存的会话文件中读取标题
        session_title = ""
        if session.session_id:
            try:
                saved_data = await asyncio.to_thread(load_session, session.session_id)
                if saved_data:
                    session_title = saved_data.get("title", "") or ""
            except Exception:
                _logger.debug("读取会话标题失败（初始化时非关键）")
        await ws_send({
            "type": "session_initialized",
            "messages": rebuilt,
            "model": session.model,
            "title": session_title,
        })
        # 推送初始沙盒计数（前端不再主动请求 get_sandbox_files 来更新角标）
        await ws_send(build_sandbox_updated())

    @staticmethod
    async def _handle_orphaned_task(ws_send, session: ChatSession,
                                    orphaned_task_registry: dict | None = None) -> None:
        """检查后台孤儿任务是否已结束，若已完成则推送最终消息。

        优先使用 orphaned_task_registry（页面刷新时跨会话传递），
        降级至 session._state.orphaned_task（向后兼容）。
        """
        # 检查注册表（优先级高）：页面刷新时旧连接的任务存在于此
        task = None
        messages = session.messages
        model = session.model
        if orphaned_task_registry is not None:
            task = orphaned_task_registry.get("task")
            if task is not None:
                registry_msgs = orphaned_task_registry.get("messages")
                registry_model = orphaned_task_registry.get("model")
                if registry_msgs is not None:
                    messages = registry_msgs
                if registry_model is not None:
                    model = registry_model
        # 降级：检查会话自身的孤儿任务（向后兼容）
        if task is None:
            task = session._state.orphaned_task
        if task is not None and task.done():
            if not task.cancelled():
                _logger.info("后台 LLM 生成任务已完成，推送最终消息")
                rebuilt = _rebuild_message_indices(messages)
                await ws_send({
                    "type": "messages_updated",
                    "messages": rebuilt,
                    "model": model,
                })
                await ws_send(build_sandbox_updated())

    async def cleanup(self) -> None:
        """清理连接全部资源。

        ★ 页面刷新保护：传入 session + orphaned_task_registry 使 cleanup_connection 保留
           正在运行的 LLM 生成任务到全局注册表，新连接可接管其结果。
        """
        # 先移除本连接的 cost_update handler（每个连接独立注册，必须各自移除）
        if self.session is not None and self._cost_handler is not None:
            try:
                self.session.off("cost_update", self._cost_handler)
            except Exception:
                _logger.exception("取消 cost_update 订阅异常")
            self._cost_handler = None

        # 先排空发送队列中的残留消息
        if self.drain_send_queue is not None:
            try:
                await self.drain_send_queue()
            except Exception:
                _logger.exception("排空发送队列异常")
        await cleanup_connection(
            bridge=self.bridge,
            my_select_ids=self.my_select_ids,
            pending_send_tasks=self.pending_send_tasks,
            process_task=self.process_task,
            proc_state=self.proc_state,
            session=self.session,
            orphaned_task_registry=getattr(self, 'orphaned_task_registry', None),
        )


__all__ = ["ConnectionContext"]
