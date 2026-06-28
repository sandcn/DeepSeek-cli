"""事件消费者 — 消费 EventBus 事件的实用组件

提供预构建的消费者组件，订阅 DisplayEventBus 并处理事件。

组件列表：
- OutputConsumer: 消费 OutputEvent，按 level 映射到终端颜色并输出

ToolSummaryEvent 已移至 ChatUIConsumer（chat_ui.py）处理。
"""

from __future__ import annotations

import sys
from typing import List, Optional

from .event_bus import DisplayEventBus
from .._lock import _try_acquire_output_lock
from .event_types import (
    DisplayEvent,
    OutputEvent,
    ToolSummaryEvent,
)

# -- 级别 -> ANSI 颜色映射 ----------------------------------------------

_LEVEL_COLORS: dict[str, str] = {
    "error": "\033[31m",      # RED
    "warning": "\033[33m",    # YELLOW
    "success": "\033[32m",    # GREEN
    "info": "\033[90m",       # DARK_GRAY
    "raw": "",                # 原样输出
}
_RESET = "\033[0m"


# ═══════════════════════════════════════════════════════════
# OutputConsumer -- 将 OutputEvent 渲染到终端
# ═══════════════════════════════════════════════════════════

class OutputConsumer:
    """消费 OutputEvent，按 level 映射颜色后输出到终端。

    替代直接 print() 调用，统一输出路由。
    非 TTY 环境自动剥离颜色码。

    ToolSummaryEvent 已移至 ChatUIConsumer 处理，此处不再订阅。
    """

    def __init__(self, event_bus: Optional[DisplayEventBus] = None, stream=None):
        self._bus = event_bus or DisplayEventBus.get_default()
        self._stream = stream or sys.stdout
        self._started: bool = False

    def start(self) -> None:
        """订阅 OutputEvent。"""
        if self._started:
            return
        self._bus.subscribe(self._on_output, event_type=OutputEvent)
        self._started = True

    def stop(self) -> None:
        """取消订阅。"""
        if not self._started:
            return
        self._bus.unsubscribe(self._on_output, event_type=OutputEvent)
        self._started = False

    def _on_output(self, event: DisplayEvent) -> None:
        if not isinstance(event, OutputEvent):
            return
        # source="cmd" 的事件已由 ChatUIConsumer 接管渲染，此处跳过避免重复
        if event.source == "cmd":
            return
        # ChatUI 活跃时，所有 OutputEvent 由 ChatUIConsumer 渲染管线处理，
        # OutputConsumer 跳过直写避免重复和绕过 ChatUI
        try:
            from ...chat_ui import get_active_chat_ui
            if get_active_chat_ui() is not None:
                return
        except ImportError:
            pass
        self._write(event.text, event.level)

    def _write(self, text: str, level: str = "info") -> None:
        """输出带颜色/级别的文本到终端（由 output_lock 保护）。"""
        with _try_acquire_output_lock(name="output_consumer._write", timeout=1.0):
            try:
                if self._stream.closed:
                    return
                if level == "raw":
                    line = text
                else:
                    color = _LEVEL_COLORS.get(level, "")
                    line = f"{color}{text}{_RESET}"
                self._stream.write(line + "\n")
                self._stream.flush()
            except (ValueError, OSError):
                pass


# -- 便捷函数 -----------------------------------------------------------


def publish_output(text: str, level: str = "info", source: str = "") -> None:
    """便捷函数：发布输出事件到默认 EventBus。

    这是替代 print() 的标准方式，任何模块都可直接调用。

    Args:
        text: 输出文本（不带 ANSI 颜色码，由消费者添加）
        level: 输出级别: "info", "success", "warning", "error", "raw"
        source: 事件来源标识
    """
    DisplayEventBus.get_default().publish(
        OutputEvent(text=text, level=level, source=source)
    )


def publish_tool_summary(
    successful_tools: List[str],
    failed_tools: List[tuple[str, str]],
    source: str = "",
) -> None:
    """便捷函数：发布工具执行汇总事件到默认 EventBus。

    替代 agent.py 中 _show_tool_execution_summary 的 print 调用。

    Args:
        successful_tools: 成功执行的工具名称列表
        failed_tools: 失败的工具列表 [(name, error), ...]
        source: 事件来源标识
    """
    DisplayEventBus.get_default().publish(
        ToolSummaryEvent(
            successful_tools=tuple(successful_tools),
            failed_tools=tuple(failed_tools),
            source=source,
        )
    )
