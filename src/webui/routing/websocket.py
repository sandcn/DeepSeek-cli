"""WebSocket 主入口 — handle_websocket + 心跳检测

消息循环 + 心跳 ping 检测死连接。
消息路由通过 WS_MESSAGE_HANDLERS 表分发。
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web
from aiohttp import WSMsgType

from .context import ConnectionContext
from .handlers import WS_MESSAGE_HANDLERS

_logger = logging.getLogger(__name__)


async def async_timeout_iter(ws: web.WebSocketResponse, timeout: float = 30.0):
    """异步迭代 WebSocket 消息，每次迭代有超时保护。

    使用 asyncio.ensure_future + asyncio.wait_for 模式：
    将 __anext__() 包装为可取消的 Task，超时时主动 cancel 并 await，
    确保底层协程被正确取消，防止协程泄漏。
    """
    aiter_ = ws.__aiter__()
    while True:
        next_task = asyncio.ensure_future(aiter_.__anext__())
        try:
            yield await asyncio.wait_for(next_task, timeout=timeout)
        except asyncio.TimeoutError:
            if not next_task.done():
                next_task.cancel()
                try:
                    await next_task
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            continue
        except StopAsyncIteration:
            break


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """处理单个 WebSocket 连接。

    每个连接独立拥有 WebDisplay + WebEventBridge + MsgIndexState。
    用户消息放入 asyncio.Queue，后台任务依次消费并执行 run_round。
    消息路由通过 WS_MESSAGE_HANDLERS 表分发。
    """
    ctx, ws = await ConnectionContext.from_ws(request)

    # ── 死连接检测事件（ping worker 设置，消息循环检查） ──
    _dead_event = asyncio.Event()

    # ── 心跳 ping（每 30s 检测死连接） ─────────────────
    async def _ping_worker():
        _consecutive_ping_failures = 0
        _max_ping_failures = 3
        try:
            while not ws.closed:
                await asyncio.sleep(30)
                try:
                    await ws.ping()
                    _consecutive_ping_failures = 0  # 成功后重置
                except Exception:
                    _consecutive_ping_failures += 1
                    _logger.warning(
                        "WebSocket ping 失败 (%d/%d)",
                        _consecutive_ping_failures,
                        _max_ping_failures,
                    )
                    if _consecutive_ping_failures >= _max_ping_failures:
                        _logger.error(
                            "WebSocket ping 连续 %d 次失败，关闭连接",
                            _max_ping_failures,
                        )
                        _dead_event.set()  # ★ 通知消息循环退出
                        break
        except asyncio.CancelledError:
            pass

    ping_task = asyncio.create_task(_ping_worker())

    try:
        async for msg in async_timeout_iter(ws, timeout=30.0):
            if _dead_event.is_set():  # ★ 死连接检测
                break

            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data) if isinstance(msg.data, str) else msg.data
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")
                handler = WS_MESSAGE_HANDLERS.get(msg_type)
                if handler:
                    try:
                        await handler(data, ctx)
                    except Exception:
                        _logger.exception("消息处理异常, type=%s", msg_type)
                        await ctx.ws_send({
                            "type": "command_output",
                            "text": f"处理消息时发生错误: {msg_type}",
                            "level": "error",
                        })
                    if ctx._exit_flag:
                        return ws
            elif msg.type == WSMsgType.CLOSE:
                _logger.debug("WebSocket 客户端主动关闭连接")
                break
            elif msg.type == WSMsgType.BINARY:
                _logger.debug("收到二进制消息，忽略")
                continue
            elif msg.type == WSMsgType.ERROR:
                _logger.error("WebSocket 连接异常: %s", ws.exception())
                break
    finally:
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass
        await ctx.cleanup()
        # ★ Bug6 修复：exit 命令只关闭当前 WebSocket 连接，
        # 不触发服务器关闭。关闭服务器应由用户按 Ctrl+C 完成。
        # 移除 ctx.shutdown_event.set() 调用。

    return ws


__all__ = ["handle_websocket", "async_timeout_iter"]
