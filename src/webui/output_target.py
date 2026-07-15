"""WebSocket 输出目标 — 实现 IOutputTarget 接口

WebSocketTarget 将 IOutputTarget 适配到 WebSocket 发送通道，
使 CLI 层的输出目标（如 ParallelDisplay 的 output_target）可在 WebUI 模式下
将帧渲染/文本输出推送到前端。

设计意图：
  - 实现 IOutputTarget Protocol，与 TerminalTarget / BufferTarget 等互换
  - write()/write_line() 复用已有的 command_output 消息类型
  - render_frame() 使用 output_frame 消息类型发送行列表
  - 与 BaseWebSocketSender 使用相同的 send_func 模式

用法：
    from src.webui.output_target import WebSocketTarget

    def send(msg: dict) -> None:
        queue.put_nowait(msg)

    target = WebSocketTarget(send)
    target.write_line("Hello from WebUI")
    target.render_frame(["line1", "line2"], last_lines=0)
"""

from __future__ import annotations

import logging
from typing import Callable, List

from ..tui_framework.terminal.output_target import IOutputTarget
from .types import msg_command_output, msg_output_frame

_logger = logging.getLogger(__name__)

# 输出目标宽度限制常量
_TARGET_WIDTH_MIN = 40
_TARGET_WIDTH_MAX = 200


class WebSocketTarget(IOutputTarget):
    """WebSocket 输出目标 — 实现 IOutputTarget 接口

    将 IOutputTarget 适配到 WebSocket 发送通道。
    - write() / write_line()：发送 command_output 消息（前端已有 handler）
    - render_frame()：发送 output_frame 消息（含完整行列表，供前端帧替换）
    - terminal_width：前端可报告窗口宽度（默认 120）

    线程安全：send_func 应使用 asyncio.Queue.put_nowait 等线程安全方式发送，
    不在此类中加锁（由 send_func 实现方自行保证）。
    """

    def __init__(
        self,
        send_func: Callable[[dict], None],
        terminal_width: int = 120,
    ):
        """
        Args:
            send_func: 发送函数，接受 dict 参数（与 BaseWebSocketSender 相同模式）
            terminal_width: 输出宽度（列数），前端可调用 set_width() 更新

        Raises:
            TypeError: send_func 不是可调用对象时抛出
        """
        if not callable(send_func):
            raise TypeError(
                f"send_func 必须是可调用对象，收到: {type(send_func).__name__}"
            )
        self._send = send_func
        self._width = max(_TARGET_WIDTH_MIN, min(terminal_width, _TARGET_WIDTH_MAX))

    # ═══════════════════════════════════════════════════════
    # IOutputTarget 接口实现
    # ═══════════════════════════════════════════════════════

    def _safe_send(self, msg: dict) -> None:
        """安全发送消息，捕获发送异常不传播。"""
        try:
            self._send(msg)
        except Exception:
            _logger.exception("WebSocketTarget 发送消息异常: type=%s", msg.get("type", "?"))

    def write(self, text: str) -> None:
        """写入文本（发送为 command_output 消息）。"""
        if text:
            self._safe_send(msg_command_output(text, level="info"))

    def write_line(self, text: str = "") -> None:
        """写入一行文本（发送为 command_output 消息）。"""
        self._safe_send(msg_command_output(text, level="info"))

    def render_frame(self, lines: List[str], last_lines: int) -> int:
        """增量渲染帧 — 发送行列表到前端供帧替换。

        Web 场景下不做 ANSI 清行码操作，而是将完整帧数据推送给前端，
        由前端自行管理 DOM 更新。

        Args:
            lines: 当前帧的行列表
            last_lines: 上一帧的行数（前端可据此判断是否需要清除旧帧）

        Returns:
            len(lines) — 本次渲染的行数，供下一帧 last_lines 使用
        """
        self._safe_send(msg_output_frame(lines=list(lines), last_lines=last_lines))
        return len(lines)

    @property
    def terminal_width(self) -> int:
        """输出目标的宽度（列数）。"""
        return self._width

    # ═══════════════════════════════════════════════════════
    # 扩展方法
    # ═══════════════════════════════════════════════════════

    def set_width(self, width: int) -> None:
        """设置输出宽度（前端可在窗口 resize 时调用更新）。

        Args:
            width: 目标宽度（列数），最小 40，最大 200

        Raises:
            TypeError: width 不是 int 类型时抛出
        """
        if not isinstance(width, int):
            raise TypeError(f"width 必须是 int，收到: {type(width).__name__}")
        self._width = max(_TARGET_WIDTH_MIN, min(width, _TARGET_WIDTH_MAX))

    def send_raw(self, msg: dict) -> None:
        """发送原始消息（底层透传）。

        当需要发送非标准消息类型时直接调用底层 send_func。
        """
        self._safe_send(msg)


__all__ = ["WebSocketTarget"]
