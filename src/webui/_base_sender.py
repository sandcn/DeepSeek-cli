"""WebSocket 消息发送基类 — 安全发送 + FIFO 串行队列

提供 WebDisplay 和 WebEventBridge 共享的消息发送基础设施。
消除两者之间的重复代码（send_json / async_drain）。

★ 关键设计：所有消息通过 connection.py 中的串行 asyncio.Queue 排队，
   由单一 worker task 串行处理并发送到 WebSocket，保证 FIFO 顺序。
   BaseWebSocketSender.send_json() 只负责将消息放入队列（同步 put_nowait），
   不创建任何 fire-and-forget task，彻底消除并行 task 交错导致的顺序错乱。
"""

from __future__ import annotations

import logging
from typing import Callable

_logger = logging.getLogger(__name__)


class BaseWebSocketSender:
    """WebSocket 消息发送基类。

    send_json 使用底层 _send 函数直接发送（同步 put_nowait 到串行队列），
    由 connection.py 中的 _send_worker 单一 task 串行处理后发送到 WebSocket，
    保证消息严格按 FIFO 顺序到达前端。

    属性:
        _send: 底层发送函数（connection.py 的 _tracked_send → queue.put_nowait）
    """

    def __init__(self, send_func: Callable[[dict], None]):
        """
        Args:
            send_func: 底层发送函数（同步，接受 dict 参数）
        """
        self._send = send_func

    # ── 消息发送 ──────────────────────────────────────────

    def send_json(self, msg: dict) -> None:
        """同步发送 JSON 消息 — 放入串行队列，由单一 worker 按序处理。

        不创建任何异步 task，所有消息排队等待串行处理，保证 FIFO 顺序。
        消除原 fire-and-forget task 模式中并行 task 交错导致的顺序错乱。
        """
        try:
            self._send(msg)
        except (TypeError, ValueError) as e:
            # 格式问题（如 msg 不是 dict）：记日志后向上传播让调用方感知
            # ★ 防御：msg 可能不是 dict（导致 TypeError 的元凶），
            #   msg.get() 会再次抛出 AttributeError 遮蔽原始异常。
            #   用 isinstance 安全获取 type 信息。
            msg_type = msg.get("type", "?") if isinstance(msg, dict) else type(msg).__name__
            _logger.error("%s 发送消息格式异常 (msg type=%s): %s",
                          type(self).__name__, msg_type, e)
            raise
        except (ConnectionResetError, ConnectionAbortedError, RuntimeError) as e:
            # 连接断开/运行时异常：记日志但不传播（连接层会自行恢复/清理）
            _logger.warning("%s 发送消息连接异常: %s", type(self).__name__, e)
        except Exception:
            _logger.exception("%s 发送消息异常", type(self).__name__)


__all__ = ["BaseWebSocketSender"]
