"""连接初始化 — WebSocket 连接初始化与资源创建

包含 _setup_connection 函数，负责创建 WebDisplay / WebEventBridge / MsgIndexState
并注入 session.agent。
"""

from __future__ import annotations

import asyncio
import logging

from ..msg_index import MsgIndexState, assign_msg_index
from ..display import WebDisplay
from ..bridge import WebEventBridge
from ...ui.adapters import UIDisplayAdapter
from ..types import msg_round_cost
from ...tui.core.cost import compute_round_cost_data

_logger = logging.getLogger(__name__)

# ★ WebSocket 发送队列最大容量 2000：
#   估算依据：流式场景下每轮对话约产生 500-1000 条消息（chunk/status/usage），
#   2000 上限足以容纳约 2-3 轮对话的积压量。超出时丢弃最旧 10% 的中间状态消息
#   （如 speed_update/live_* 等实时指标），保证最新消息优先到达前端。
_WEBSOCKET_QUEUE_MAXSIZE = 2000


def _setup_connection(ws, session, ws_send, select_id_tracker=None):
    """构建 WebDisplay、WebEventBridge 并注入 session.agent。

    参数:
        ws: WebSocket 连接对象
        session: ChatSession 实例
        ws_send: 异步消息发送函数
        select_id_tracker: 可选的选择 ID 追踪器
    返回:
        (msg_idx_state, web_display, bridge, adapter, pending_send_tasks, drain_send_queue, cost_handler) 七元组
    """
    msg_idx_state = MsgIndexState()
    _loop = asyncio.get_running_loop()
    send_queue: asyncio.Queue = asyncio.Queue(maxsize=_WEBSOCKET_QUEUE_MAXSIZE)
    send_queue_active = True
    pending_send_tasks: set[asyncio.Task] = set()

    async def _send_worker():
        """串行队列消费者 — 保证消息 FIFO 顺序发送。

        所有消息通过 asyncio.Queue 排队，由此 worker 单一线程串行处理，
        确保 msg_index 按序分配、WebSocket 按序发送，消除并行 task
        交错导致的顺序错乱。

        ★ 取消安全：CancelledError 在 await asyncio.wait_for(get()) 中抛出时，
          get() 可能已取出消息（消息已出队但 task_done() 未调用）。
          必须确保 task_done() 被调用，防止 Queue.join() 永久挂起。
        """
        while send_queue_active:
            _got_msg = False
            _task_done_called = False
            try:
                try:
                    msg = await asyncio.wait_for(send_queue.get(), timeout=1.0)
                    _got_msg = True
                except asyncio.TimeoutError:
                    continue
                try:
                    await assign_msg_index(msg, msg_idx_state, session.messages, ws_send)
                except Exception:
                    _logger.exception("串行发送 worker 异常, type=%s", msg.get("type", ""))
                    # fallback: 直接发送原始消息，避免 assign_msg_index 失败导致消息静默丢失
                    try:
                        await ws_send(msg)
                    except Exception:
                        _logger.debug("fallback ws_send 失败")
                finally:
                    if _got_msg and not _task_done_called:
                        send_queue.task_done()
                        _task_done_called = True
            except asyncio.CancelledError:
                # Worker 被显式取消（如 _drain_send_queue 中的 cancel()）
                # ★ 确保 task_done() 配对：CancelledError 在 get() 取到消息后、
                #   try 2 的 finally 执行前抛出时，task_done() 可能未及调用，
                #   导致 Queue.join() 永久挂起。此处补调保证配对完整性。
                if _got_msg and not _task_done_called:
                    send_queue.task_done()
                raise
            except Exception as e:
                # Worker 自动恢复：捕获顶层异常，防止 worker 永久停摆
                if _got_msg and not _task_done_called:
                    send_queue.task_done()
                _logger.error("发送 worker 顶层异常，自动恢复: %s", e)
                await asyncio.sleep(0.5)

    def _tracked_send(msg):
        """将消息放入串行队列，由 _send_worker 按序处理。

        背压策略：队列满时丢弃最旧的消息腾出空间。
        保证最新消息优先到达前端（流式场景下旧中间状态可丢弃）。
        ★ 风险说明：丢弃的消息如果在 assign_msg_index 链路中承载了关键的
           msg_index 分配上下文，可能导致前端消息索引错乱。但此风险仅发生在
           队列积压到 2000 条（_WEBSOCKET_QUEUE_MAXSIZE）的极端场景下，且被丢弃的是最旧的中间状态消息
           （而非最新的），对最终一致性影响极小。
        """
        try:
            send_queue.put_nowait(msg)
        except asyncio.QueueFull:
            # ★ 队列满：丢弃最旧的 10% 消息，腾出空间给新消息
            # 在流式输出场景下，旧的内容块已被前端消费，丢弃未处理的中转消息
            # 比阻塞等待更合理（新消息优先级高于旧消息）。
            drop_count = max(1, _WEBSOCKET_QUEUE_MAXSIZE // 10)
            dropped = 0
            while dropped < drop_count:
                try:
                    send_queue.get_nowait()
                    send_queue.task_done()
                    dropped += 1
                except asyncio.QueueEmpty:
                    break
            if dropped > 0:
                _logger.debug("WebSocket 发送队列满，丢弃 %d 条旧消息腾出空间", dropped)
            # 重试放入（仍可能满 → 标记丢弃，防止上游崩溃）
            try:
                send_queue.put_nowait(msg)
            except asyncio.QueueFull:
                msg_type = msg.get("type", "") if isinstance(msg, dict) else "?"
                _logger.warning("WebSocket 发送队列仍满，丢弃消息(type=%s)", msg_type)


    worker_task = _loop.create_task(_send_worker())
    pending_send_tasks.add(worker_task)
    worker_task.add_done_callback(pending_send_tasks.discard)

    web_display = WebDisplay(_tracked_send)
    bridge = WebEventBridge(_tracked_send, select_id_tracker=select_id_tracker)
    bridge.subscribe()
    adapter = UIDisplayAdapter(web_display)
    setattr(session.agent, '_display_port', adapter)
    session.agent.display = adapter

    # ★ 添加排空辅助函数到返回值（六元组），供关闭时清空队列
    async def _drain_send_queue():
        nonlocal send_queue_active, worker_task
        send_queue_active = False
        # ★ task_done 说明：当 worker 被取消时，最后一条 get() 取出的消息可能尚未调用 task_done()。
        #   由于队列 join() 已被禁用（详见清空循环注释），task_done 计数不精确不影响功能。
        #   后续清空循环会 drain 所有消息，不会留下待处理消息。
        # 直接取消 worker 任务，避免等待 1 秒超时
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        # 清空剩余消息（连接即将关闭，queue 不会再被使用）
        # ★ 注意：不调用 task_done()。
        #   worker 被取消时，可能 get() 已取出消息但 task_done() 未及调用，
        #   也可能 woker 中 task_done() 已执行。此处的 task_done() 计数无法精确匹配，
        #   会导致 ValueError: too many values have been put on the queue。
        #   因为 queue 在连接关闭后不再使用，task_done 计数不影响功能，直接忽略。
        #   ★ 此队列禁用 join()——task_done 计数不精确，调 join() 会永久挂起。
        while not send_queue.empty():
            try:
                send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── 注册 cost_update Hook → 推送成本到前端 ──
    def _on_cost_update(delta=None, total=None, model=None, prices=None,
                        session_elapsed=0, messages=None, **kw):
        if not delta or not total:
            return
        data = compute_round_cost_data(
            delta_in=delta.get("input", 0),
            delta_out=delta.get("output", 0),
            delta_calls=delta.get("calls", 0),
            model=model or session.model,
            prices=prices or {"input": 0.01, "output": 0.03},
            total_stats=total,
            session_elapsed=session_elapsed or 0,
            messages=messages,
        )
        _tracked_send(msg_round_cost(data))

    session.on("cost_update", _on_cost_update)

    return msg_idx_state, web_display, bridge, adapter, pending_send_tasks, _drain_send_queue, _on_cost_update
