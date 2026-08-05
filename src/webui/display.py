"""WebDisplay — Web UI 显示层（BaseDisplay 实现）

WebDisplay: 实现 BaseDisplay 接口，所有方法调用序列化为 JSON 经 WebSocket 发送。
           继承 BaseWebSocketSender 获得背压控制与安全发送能力。

演进说明：
  - BaseDisplay 接口方法（start/stop/tool_*/update_*）使用 types.py 中的消息构建器
  - 扩展方法（tool_batch_start / update_parse_info / add_agent 等）也使用消息构建器
  - PENDING_SELECTS 已迁移至 _pending_selects.py（PendingSelectRegistry）；display.py 重导出 pending_selects
"""

from __future__ import annotations

import logging
from typing import Callable

from ..tui._base_display import BaseDisplay
from ._base_sender import BaseWebSocketSender
from ._pending_selects import pending_selects
from .ws_handler.sandbox import FILE_MODIFY_TOOLS, build_sandbox_updated
from .types import (
    msg_display_started,
    msg_display_stopped,
    msg_tool_parsing,
    msg_tool_started,
    msg_tool_done,
    msg_tool_status,
    msg_model_phase,
    msg_usage_update,
    msg_tool_batch_start,
    msg_agent_added,
    msg_agent_status,
    msg_speed_update,
    msg_live_input,
    msg_live_output,
    msg_parse_info,
)

_logger = logging.getLogger(__name__)

# 向后兼容：pending_selects 从 _pending_selects 导入并在此重导出
# 供 user_select / routing / cleanup 模块共享

# ── 接口方法集 ──────────────────────────────────────────

class WebDisplay(BaseDisplay, BaseWebSocketSender):
    """Web 显示实现 — 将 BaseDisplay 方法调用序列化为 JSON 经 WebSocket 发送。

    通过多重继承同时获得 BaseDisplay（接口契约）和 BaseWebSocketSender（发送能力）。
    WebDisplay 的 is_web 属性返回 True，供 agent 检测运行模式。
    """

    def __init__(self, send_func: Callable[[dict], None]):
        """
        Args:
            send_func: 异步发送函数，接受 dict 参数，
                       典型实现为 functools.partial(ws.send_json, ...)
        """
        BaseDisplay.__init__(self)
        BaseWebSocketSender.__init__(self, send_func)
        self.is_web = True

    # ═══════════════════════════════════════════════════════
    # 生命周期 — BaseDisplay 接口
    # ═══════════════════════════════════════════════════════

    def start(self) -> None:
        self.send_json(msg_display_started())

    def stop(self, final: bool = False) -> None:
        self.send_json(msg_display_stopped(final=final))

    # ═══════════════════════════════════════════════════════
    # 工具调用 — BaseDisplay 接口
    # ═══════════════════════════════════════════════════════

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        self.send_json(msg_tool_parsing(label, tool_name, arguments))

    def tool_start(
        self,
        label: str,
        tool_name: str,
        detail: str = "",
        metadata: dict | None = None,
    ) -> None:
        self.send_json(msg_tool_started(label, tool_name, detail, metadata))

    def tool_done(
        self,
        label: str,
        tool_name: str = "",
        success: bool = True,
        metadata: dict | None = None,
    ) -> None:
        self.send_json(msg_tool_done(label, tool_name, success, metadata))
        # 文件修改工具完成后，推送准确的沙盒计数（前端不再自行 +1 后异步刷新）
        if tool_name in FILE_MODIFY_TOOLS:
            self.send_json(build_sandbox_updated())

    # ═══════════════════════════════════════════════════════
    # 状态与阶段 — BaseDisplay 接口
    # ═══════════════════════════════════════════════════════

    def update_status(self, label: str, status: str) -> None:
        self.send_json(msg_tool_status(label, status))

    def update_model_phase(self, label: str, phase: str, info: str = "") -> None:
        self.send_json(msg_model_phase(label, phase, info))

    def update_usage(self, label: str, usage: dict, replace: bool = False) -> None:
        self.send_json(msg_usage_update(label, usage, replace))

    # ═══════════════════════════════════════════════════════
    # 实时指标（update_speed / update_live_*）
    # ═══════════════════════════════════════════════════════

    def update_speed(self, label: str, speed: float) -> None:
        self.send_json(msg_speed_update(label, speed))

    def update_live_input(self, label: str, tokens: int) -> None:
        self.send_json(msg_live_input(label, tokens))

    def update_live_output(self, label: str, tokens: int) -> None:
        self.send_json(msg_live_output(label, tokens))

    # ═══════════════════════════════════════════════════════
    # 扩展方法 — 非 BaseDisplay 接口，但 UIDisplayAdapter 会调用
    # ═══════════════════════════════════════════════════════

    def tool_batch_start(self, label: str, names: list) -> None:
        self.send_json(msg_tool_batch_start(label, names))

    def update_parse_info(self, label: str, tool_name: str, tokens: int, elapsed: float) -> None:
        self.send_json(msg_parse_info(label, tool_name, tokens, elapsed))

    def parse_info_done(self, label: str) -> None:
        pass

    def update_agent_status(self, label: str, status: str) -> None:
        self.send_json(msg_agent_status(label, status))

    def add_agent(self, label: str, description: str, status: str = "running") -> None:
        self.send_json(msg_agent_added(label, description, status))

    def run_display(self, display_func: Callable[[], str] | None) -> str:
        """Web 模式：直接执行 display_func 并返回结果（不捕获 stdout）。"""
        return display_func() if callable(display_func) else ""

    def capture_and_print(self, display_func: Callable[[], str] | None) -> str:
        """接口兼容方法，委托给 run_display。"""
        return self.run_display(display_func)

__all__ = ["WebDisplay", "pending_selects"]