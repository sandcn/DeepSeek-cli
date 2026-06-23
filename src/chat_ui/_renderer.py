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
    from ._components import TuiComponent
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

from ._utils import _cmd_name

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
            sys.__stdout__.write("\n")
            sys.__stdout__.flush()
            self._record_lines(1)
            return
        if isinstance(tokens, (int, float)):
            tokens_str = f"{tokens}t" if math.isfinite(tokens) else "?"
        else:
            tokens_str = str(tokens)
        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        sys.__stdout__.write(output)
        sys.__stdout__.flush()

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
        if not frame_lines:
            return
        if len(frame_lines) < 4:
            return
        lines = frame_lines[0]
        scroll_end = frame_lines[1]
        last_lines = frame_lines[2]
        clear_eol = frame_lines[3]
        if not lines or not isinstance(lines, (list, tuple)):
            return
        total = len(lines)
        buf = ""
        if scroll_end > 0 and total > scroll_end:
            lines = lines[total - scroll_end:]
            total = scroll_end
        if scroll_end > 0 and last_lines > 0 and total > last_lines:
            delta = total - last_lines
            buf += f"\033[{scroll_end};1H\033[{delta}S"
        if scroll_end > 0:
            start_row = scroll_end - total + 1
            clear_start = start_row
            if last_lines > 0:
                old_start = scroll_end - last_lines + 1
                if old_start < clear_start:
                    clear_start = old_start
            if clear_start < 1:
                clear_start = 1
            for r in range(clear_start, scroll_end + 1):
                buf += f"\033[{r};1H{clear_eol}"
            buf += f"\033[{start_row};1H"
            for i, line in enumerate(lines):
                buf += line
                if i < total - 1:
                    buf += "\n"
            restore_delta = 0
            if last_lines > 0 and total < last_lines:
                restore_delta = last_lines - total
            if restore_delta > 0:
                buf += f"\033[{scroll_end};1H\033[{restore_delta}T"
                for r in range(1, restore_delta + 1):
                    buf += f"\033[{r};1H{clear_eol}"
            self._adapter.write_raw_buffered(buf)
            return
        try:
            from ..ui._blessed import get_terminal
            term = get_terminal()
            move_up = term.move_up
            sc = term.sc if term.sc else "\033[s"
            rc = term.rc if term.rc else "\033[u"
        except Exception:
            _logger.debug("subagent_frame Blessed 不可用, 使用 ANSI 回退", exc_info=True)
            move_up = lambda n: f"\033[{n}A"
            sc = "\033[s"
            rc = "\033[u"
        buf = ""
        if last_lines > 0:
            buf += rc
            buf += move_up(last_lines)
        for i, line in enumerate(lines):
            buf += "\r" + clear_eol + line
            if i < total - 1:
                buf += "\n"
        extra = last_lines - total
        if extra > 0:
            buf += "\n" + sc
            for _ in range(extra):
                buf += "\n" + clear_eol
        else:
            buf += "\n" + sc
        self._adapter.write_raw_buffered(buf)

    # ── 树形组件渲染 ─────────────────────────────

    def render_tree(self, root: "TuiComponent") -> int:
        """渲染组件树并返回估计行数。

        调用根组件的 render_to_adapter 进行渲染，children 的递归渲染
        由组件自身在 render_to_adapter 中处理。

        Args:
            root: 组件树根节点。

        Returns:
            渲染产生的总估计行数；渲染失败时返回 0。
        """
        try:
            total = self._render_component(root)
            self._record_lines(total)
            return total
        except Exception:
            _logger.exception("组件树渲染失败: %s", type(root).__name__)
            return 0

    def _render_component(self, comp: "TuiComponent") -> int:
        """渲染单个组件节点，返回行数。

        调用组件的 render_to_adapter 进行输出，不自行递归处理 children
        （children 由组件自身在 render_to_adapter 中处理）。

        Args:
            comp: 要渲染的组件。

        Returns:
            组件渲染产生的估计行数；渲染失败时返回 0。
        """
        try:
            return comp.render_to_adapter(self._adapter)
        except Exception:
            _logger.exception("组件渲染失败: %s", type(comp).__name__)
            return 0
