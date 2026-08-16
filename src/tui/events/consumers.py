"""事件消费者 — 消费 EventBus 事件的实用组件

提供预构建的消费者组件，订阅 DisplayEventBus 并处理事件。

组件列表：
- OutputConsumer: 消费 OutputEvent，按 level 映射到终端颜色并输出

ToolSummaryEvent 已移至 ChatUIConsumer（chat_ui.py）处理。
"""

from __future__ import annotations

import logging
import sys
from typing import List, Optional

from .event_bus import DisplayEventBus
from .publish import emit
from src.renderer._locks import _try_acquire_output_lock
from src.tui.ink.output import Line
from .event_types import (
    DisplayEvent,
    OutputEvent,
    ToolSummaryEvent,
)

_logger = logging.getLogger(__name__)

# -- 级别 -> Style 映射（统一 ink 输出模型） -----------------------------
# ★ 标准 React Ink 输出模型（2026-08-16 深化控件化，用户需求「所有 TUI 都
#   要用 React Ink 控件跟布局实现所有」）：OutputConsumer 为「无 ChatUI 上下
#   文的回退直写输出路径」——渲染行统一经 ``core.style.Style`` + ``ink Line``
#   输出模型构建（``Line.render()``），不再手工拼接 ANSI 色串（与
#   message_display 兜底 / _diff_renderer 已迁移语义一致：回退路径与界面渲染
#   共用同一输出模型/样式体系）。色号取与旧 16 色视觉等价的 256 色语义色
#   （error 亮红 196 / warning 黄 220 / success 绿 41 / info 中灰 244）。
#   旧 ``_LEVEL_COLORS``/``_RESET`` 保留为 deprecated 兼容 re-export（外部
#   既有引用/测试兼容；生产路径不再消费）。

from src.tui.core.style import Style

_LEVEL_STYLES: dict[str, Style] = {
    "error":   Style(fg=196),   # 亮红（旧 \033[31m RED）
    "warning": Style(fg=220),   # 黄（旧 \033[33m YELLOW）
    "success": Style(fg=41),    # 绿（旧 \033[32m GREEN）
    "info":    Style(fg=244),   # 中灰（旧 \033[90m DARK_GRAY）
    "raw":     Style(),         # 原样输出（无样式）
}
#: 旧 ANSI 色串映射（deprecated 兼容 re-export——生产路径经 ``_LEVEL_STYLES``；
#: 保留供外部既有引用/测试，勿在生产代码新增引用）
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

    单一消费路径策略（方向D 步骤7）：
      ChatUI 活跃时，OutputEvent 由 EventDispatcher（管线路径）消费
      （_on_output → WriteLineCmd），OutputConsumer 仅处理非 ChatUI 上下文输出；
      由 ``_should_skip`` 显式策略方法判定（source="cmd" 或 ChatUI 活跃均跳过直写）。
      ``chat_ui_managed`` 参数使该策略可配置（测试可传 False 关闭 ChatUI 检测）。

    ToolSummaryEvent 已移至 ChatUIConsumer 处理，此处不再订阅。
    """

    def __init__(
        self,
        event_bus: Optional[DisplayEventBus] = None,
        stream=None,
        *,
        chat_ui_managed: bool = True,
    ):
        self._bus = event_bus or DisplayEventBus.get_default()
        self._stream = stream or sys.stdout
        self._started: bool = False
        self._chat_ui_managed = chat_ui_managed

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
        if self._should_skip(event):
            return
        self._write(event.text, event.level)

    def _should_skip(self, event: DisplayEvent) -> bool:
        """单一消费路径策略：返回 True 表示跳过直写。

        策略：
          - source="cmd" 的事件已由 ChatUIConsumer 接管渲染，跳过避免重复；
          - ChatUI 活跃时（chat_ui_managed 且存在活跃 ChatUIConsumer），
            所有 OutputEvent 由 ChatUIConsumer 渲染管线处理，
            OutputConsumer 跳过直写避免重复和绕过 ChatUI。
        """
        if event.source == "cmd":
            # ★ P3-15：source="cmd" 无条件跳过直写（不检查 ChatUI 活跃）——
            # 契约注明：所有 source="cmd" 的 OutputEvent 发布方均在 ChatUI
            # 命令执行路径（CommandUiAdapter 等），必然存在活跃 ChatUIConsumer
            # 接管渲染；即使 chat_ui_managed=False（测试场景）也跳过，
            # 避免命令输出重复直写。若未来出现非 ChatUI 路径发布
            # source="cmd"，需改为 ``and self._chat_ui_managed
            # and self._active_chat_ui_present()``。
            return True
        if self._chat_ui_managed and self._active_chat_ui_present():
            return True
        return False

    def _active_chat_ui_present(self) -> bool:
        try:
            from ..consumer import get_active_chat_ui
            return get_active_chat_ui() is not None
        except ImportError:
            return False

    def _write(self, text: str, level: str = "info") -> None:
        """输出带颜色/级别的文本到终端（由 output_lock 保护）。

        ★ 标准 React Ink 输出模型（2026-08-16 深化控件化）：渲染行统一经
        ``ink Line`` 构建（``Line.of(text, style)``，样式为 ``core.style.Style``）
        后 ``render()`` 输出——回退直写路径与界面渲染共用同一输出模型/样式
        体系（不再手工拼接 ANSI 色串，与 message_display/_diff_renderer 已
        迁移语义一致）。非 raw 级别经 ``_LEVEL_STYLES`` 查表（缺省回退空
        Style 原样输出）；raw 级原样输出（不附加任何样式）。

        ★ P3-14：忽略 ``_try_acquire_output_lock`` 的 yield bool 值——
        锁超时（render_lock 被渲染管线占用 >1s）时仍**降级直写**终端。
        这是有意的降级策略：OutputConsumer 为非 ChatUI 上下文的兜底输出
        路径（低频、非关键渲染），宁可容忍与渲染管线并发写终端的轻微竞态，
        也不阻塞输出（阻塞会卡住日志/回显）。如需严格互斥可检查 bool
        并跳过直写，但会引入输出丢失风险。
        """
        with _try_acquire_output_lock(name="output_consumer._write", timeout=1.0):
            try:
                if self._stream.closed:
                    return
                if level == "raw":
                    line = text
                else:
                    style = _LEVEL_STYLES.get(level, Style())
                    line = Line.of(text, style).render()
                self._stream.write(line + "\n")
                self._stream.flush()
            except (ValueError, OSError):
                # 输出写失败属非关键降级（终端关闭/坏管道等），记录警告不抛
                _logger.warning("输出写失败", exc_info=True)


# -- 便捷函数 -----------------------------------------------------------


def publish_output(text: str, level: str = "info", source: str = "") -> None:
    """便捷函数：发布输出事件到默认 EventBus。

    这是替代 print() 的标准方式，任何模块都可直接调用。

    Args:
        text: 输出文本（不带 ANSI 颜色码，由消费者添加）
        level: 输出级别: "info", "success", "warning", "error", "raw"
        source: 事件来源标识
    """
    emit(OutputEvent(text=text, level=level, source=source))

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
    emit(
        ToolSummaryEvent(
            successful_tools=tuple(successful_tools),
            failed_tools=tuple(failed_tools),
            source=source,
        )
    )
