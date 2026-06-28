"""渲染器 — _RenderState + TuiRenderer + _RENDER_DISPATCH。

从 _tui.py 拆分，管理推理/内容 IncrementalRenderer 生命周期和渲染命令分发。
"""

from __future__ import annotations

import logging
import math
import sys
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..api.renderer import IncrementalRenderer
    from ..api.renderer.output import OutputAdapter
    from ._protocols import BottomBarProtocol

from ._const import (
    RenderCommand,
    _CLEAR_PARSE_LINE,
)
from ._render_state import _RenderState

from ._components import (
    ThinkingBlock,
    AnswerBlock,
    UserMsgBlock,
    ToolOutputBlock,
    ToolSummaryBlock,
    ErrorBlock,
    NotificationBlock,
    WriteLineBlock,
    _estimate_content_lines,
)

from ._utils import _cmd_name, _emergency_write

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 渲染分发表
# ═══════════════════════════════════════════════════════════

_RENDER_DISPATCH: dict[int, tuple[str, tuple[int, ...]]] = {
    RenderCommand.REASONING:       ("_do_reasoning",       (1,)),
    RenderCommand.CONTENT:         ("_do_content",         (1,)),
    RenderCommand.PHASE_DONE:      ("_do_phase_done",      (1,)),
    RenderCommand.TOOL_OUTPUT:     ("_do_tool_output",     (1,)),
    RenderCommand.TOOL_SUMMARY:    ("_do_tool_summary",    (1, 2)),
    RenderCommand.USER_MSG:        ("_do_user_message",    (1,)),
    RenderCommand.PARSE_INFO:      ("_do_parse_info",      (1, 2, 3)),
    RenderCommand.NOTIFICATION:    ("_do_notification",    (1,)),
    RenderCommand.WRITE_LINE:      ("_do_write_line",      (1,)),
    RenderCommand.DISPLAY_MSGS:    ("_do_display_messages", (1, 2)),
    RenderCommand.TOOL_COUNT_INC:  ("_do_tool_count_inc",  ()),
    RenderCommand.TOOL_COUNT_DEC:  ("_do_tool_count_dec",  ()),
    RenderCommand.TOOL_FAIL_INC:   ("_do_tool_fail_inc",   ()),
    RenderCommand.ERROR:           ("_do_error",           (1,)),
    RenderCommand.SUBAGENT_FRAME:  ("_do_subagent_frame",  (1,)),
}


# ═══════════════════════════════════════════════════════════
# TuiRenderer — 组件化内容渲染器
# ═══════════════════════════════════════════════════════════

class TuiRenderer:
    """组件化内容渲染器 — 执行 RenderCommand 并直接输出。

    将每个渲染命令映射到对应的组件，通过 OutputAdapter 输出。
    """

    def __init__(
        self,
        rs: _RenderState,
        output_adapter: "OutputAdapter",
        bottom_bar: "BottomBarProtocol",
        on_display_messages: Callable[..., None] | None = None,
        cursor_tracker: Any = None,
    ):
        self._rs = rs
        self._bb = bottom_bar
        self._on_display_messages = on_display_messages
        self._adapter = output_adapter
        self._tracker = cursor_tracker

    @property
    def output_adapter(self) -> "OutputAdapter":
        """获取当前 OutputAdapter 实例。"""
        return self._adapter

    def render(self, cmd: tuple) -> None:
        """分发渲染命令到对应的 _do_* 方法。

        通过 _RENDER_DISPATCH 表将命令 ID 映射到方法名和参数索引，
        提取参数后调用对应处理方法。

        Args:
            cmd: 渲染命令元组，格式为 (command_id, *args)
        """
        if not cmd:
            return
        cid = cmd[0]
        entry = _RENDER_DISPATCH.get(cid)
        if entry is None:
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return
        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

    def _record_lines(self, n: int) -> None:
        if self._tracker is not None:
            self._tracker.record_newlines(n)

    # ── 内容渲染 ──────────────────────────────────

    def _do_reasoning(self, text: str) -> None:
        block = ThinkingBlock(self._rs)
        self._record_lines(block.write(text))

    def _do_content(self, text: str) -> None:
        block = AnswerBlock(self._rs)
        self._record_lines(block.write(text))

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    # ── 工具渲染 ──────────────────────────────────

    def _do_tool_count_inc(self) -> None:
        self._bb.increment_tool()

    def _do_tool_count_dec(self) -> None:
        self._bb.decrement_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    def _do_tool_output(self, text: str) -> None:
        block = ToolOutputBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        block = ToolSummaryBlock(successful, failed)
        self._record_lines(block.render_to_adapter(self._adapter))

    # ── 解析进度 ──────────────────────────────────

    def _do_parse_info(self, tool_names: str, tokens, elapsed: float) -> None:
        if tokens == _CLEAR_PARSE_LINE:
            _emergency_write("\n")
            self._record_lines(1)
            return
        if isinstance(tokens, (int, float)):
            tokens_str = f"{tokens}t" if math.isfinite(tokens) else "?"
        else:
            tokens_str = str(tokens)
        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        _emergency_write(output)

    # ── 样式化行渲染 ──────────────────────────────

    def _do_user_message(self, text: str) -> None:
        block = UserMsgBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    def _do_notification(self, text: str) -> None:
        block = NotificationBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    def _do_error(self, message: str) -> None:
        block = ErrorBlock(message)
        self._record_lines(block.render_to_adapter(self._adapter))

    def _do_write_line(self, text: str) -> None:
        block = WriteLineBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
        self._record_lines(1)

    # ── SubAgent 面板 ─────────────────────────────

    def _do_subagent_frame(self, frame_lines: tuple) -> None:
        """将 subagent 面板行数据传递给 BottomBar 渲染。

        不再直接写 ANSI 到上屏，改为委托 BottomBar.force_redraw()
        在固定下屏区域渲染。
        """
        if not frame_lines:
            return
        if len(frame_lines) < 4:
            return
        lines = frame_lines[0]
        if not lines or not isinstance(lines, (list, tuple)):
            return
        if hasattr(self._bb, 'set_subagent_frame'):
            self._bb.set_subagent_frame(list(lines))
