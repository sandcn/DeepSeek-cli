"""React Ink-like TUI renderer — 非全屏组件化终端聊天渲染。

所有渲染逻辑集中在此单模块中，组件化设计，统一布局（非分屏）。

组件层次：
  App (根)
  ├── MessageStream (滚动消息区)
  │   ├── UserMsgBlock      — 用户消息（> 前缀）
  │   ├── ThinkingBlock     — 思考/推理内容块
  │   ├── AnswerBlock       — 助手回答（流式 Markdown）
  │   ├── ToolOutputBlock   — 工具执行输出
  │   ├── ToolSummaryBlock  — 工具完成汇总
  │   ├── ErrorBlock        — 错误提示
  │   └── NotificationBlock — 系统通知
  ├── StatusLine            — 状态栏（模型/tokens/速率/工具计数）
  ├── InputLine             — 输入行
  └── Overlay (条件渲染)
      ├── CompletionPopup   — Tab 补全弹窗
      └── SelectionMenu     — 底部选择菜单

布局（统一，非分屏）：
  ┌──────────────────────────────────────┐
  │  [滚动消息区 — 自然终端滚动]          │
  │  ... 消息内容 ...                     │
  ├──────────────────────────────────────┤
  │  model · tokens · time · ⚙N          │  ← StatusLine
  ├──────────────────────────────────────┤
  │  > 用户输入█                          │  ← InputLine
  └──────────────────────────────────────┘

架构：事件驱动 + 命令队列 + render 线程（保持与旧版相同的并发模型）
"""

from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from rich.style import Style
from rich.text import Text

from ._const import (
    _MAIN_LABEL, _MAIN_SOURCE,
    _STYLE_DIM, _STYLE_FAIL, _STYLE_WARN, _STYLE_SUCCESS, _STYLE_ERROR, _STYLE_BOLD,
    _THINKING_HEADER, _THINKING_SEPARATOR, _CLEAR_PARSE_LINE,
    _MAX_ERROR_LENGTH, _RENDER_INTERVAL,
    _ANSI_RED, _ANSI_YELLOW, _ANSI_RESET, _ANSI_CURSOR_BOTTOM,
    RenderCommand, _ReasoningState,
)

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 工具函数（从 _utils 导入）
# ═══════════════════════════════════════════════════════════

from ._utils import _truncate_msg, _cmd_name


# ═══════════════════════════════════════════════════════════
# 状态管理（从 _state 导入）
# ═══════════════════════════════════════════════════════════

from ._state import _active_consumer, get_active_chat_ui, _register_consumer, _unregister_consumer


# ═══════════════════════════════════════════════════════════
# 常量（从 _const 导入，此处不再重复定义）
# ═══════════════════════════════════════════════════════════

_ACTIVE_RENDER_INTERVAL = 0.005
_IDLE_DRAIN_THRESHOLD = 5
_CONSECUTIVE_FULL_THRESHOLD = 10
_MAX_OUTPUT_LEN = 10000


# ═══════════════════════════════════════════════════════════
# 协议
# ═══════════════════════════════════════════════════════════

@runtime_checkable
class BottomBarProtocol(Protocol):
    def increment_tool(self) -> None: ...
    def decrement_tool(self) -> None: ...
    def increment_tool_fail(self) -> None: ...
    def force_redraw(self) -> None: ...
    def sync_bottom_lines(self) -> None: ...
    @property
    def is_status_active(self) -> bool: ...
    def ensure_cursor_in_upper(self) -> None: ...
    def get_scroll_end(self) -> int: ...
    def get_cursor_info(self) -> tuple[str, int, int, int]: ...
    def compute_cursor_position(self, text: str, cursor_pos: int, h: int, w: int) -> tuple[int, int]: ...
    @property
    def is_completion_visible(self) -> bool: ...
    def hide_completions(self) -> None: ...
    def cycle_completion(self, delta: int) -> None: ...
    def show_completions(self, items: list[str], selected: int = 0, texts: list[str] | None = None, start_pos: int = 0, orig_prefix: str = "") -> None: ...
    def get_selected_completion(self) -> tuple[str, int, str]: ...


# ═══════════════════════════════════════════════════════════
# 组件基类
# ═══════════════════════════════════════════════════════════

class TuiComponent:
    """React Ink-like 渲染组件基类。

    每个组件通过 render() 返回渲染输出。
    子类需实现 render() 方法。
    """
    def render(self) -> str:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════
# 消息流组件
# ═══════════════════════════════════════════════════════════

class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        return Text.assemble(("\n  > ", _STYLE_BOLD), (self.text, _STYLE_BOLD))


class ThinkingBlock(TuiComponent):
    """思考/推理内容块 — 流式追加写入 IncrementalRenderer。"""
    def __init__(self, rs: "_RenderState"):
        self._rs = rs

    def write(self, text: str) -> int:
        """写入推理内容，返回估计行数。"""
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is None:
            return 0
        lines = 0
        if is_first:
            rr.write(_THINKING_HEADER)
            lines += _estimate_content_lines(_THINKING_HEADER)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    def render(self) -> str:
        return ""


class AnswerBlock(TuiComponent):
    """助手回答块 — 流式 Markdown 渲染。"""
    def __init__(self, rs: "_RenderState"):
        self._rs = rs

    def write(self, text: str) -> int:
        """写入内容，返回估计行数。"""
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)
        return _estimate_content_lines(text)

    def close(self) -> None:
        self._rs.close_content()

    def render(self) -> str:
        return ""


class ToolOutputBlock(TuiComponent):
    """工具执行输出块。"""
    def __init__(self, text: str):
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        text = self.text
        if len(text) > _MAX_OUTPUT_LEN:
            text = text[:_MAX_OUTPUT_LEN] + "...(truncated)"
        has_carriage = '\r' in text
        if has_carriage:
            if '\033[' in text:
                clean = text.replace('\r', '')
                try:
                    adapter.write(Text.from_ansi(clean))
                except Exception:
                    adapter.write_raw(clean)
            else:
                adapter.write_raw(text.split('\r')[-1])
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                clean = text.replace('\r', '') if '\033[' in text else text.split('\r')[-1]
                return _estimate_content_lines(clean)
            return 0
        else:
            adapter.write(Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text


class ToolSummaryBlock(TuiComponent):
    """工具完成汇总块。"""
    def __init__(self, successful: tuple, failed: tuple):
        self.successful = successful or ()
        self.failed = failed or ()

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        failed = self._normalize_failed()
        total = len(self.successful) + len(failed)
        if failed:
            return self._render_failure(failed, total, adapter)
        elif self.successful:
            adapter.write(Text.assemble(
                ("  · ", _STYLE_SUCCESS),
                (f"{len(self.successful)}工具完成", _STYLE_SUCCESS),
            ))
            return 1
        return 0

    def _normalize_failed(self) -> tuple:
        safe = []
        for item in self.failed:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                error = str(item[1]) if item[1] is not None else ""
                if len(item) > 2:
                    extras = ", ".join(str(x) for x in item[2:])
                    error = f"{error} [{extras}]" if error else f"[{extras}]"
                safe.append((str(item[0]), error))
            else:
                safe.append((str(item), ""))
        return tuple(safe)

    def _render_failure(self, failed: tuple, total: int, adapter: "OutputAdapter") -> int:
        names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            adapter.write(Text.assemble(
                ("  ! ", _STYLE_FAIL),
                (f"全部失败: {names}", _STYLE_FAIL),
            ))
        else:
            adapter.write(Text.assemble(
                ("  ! ", _STYLE_WARN),
                (f"{len(failed)}/{total} 失败: {names}", _STYLE_WARN),
            ))
        lines = 1
        detail = 0
        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    max_w = 80
                    s = short
                    w = 0
                    cut = len(s)
                    for i, ch in enumerate(s):
                        cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
                        if w + cw > max_w - 3:
                            cut = i
                            break
                        w += cw
                    if cut < len(s):
                        short = s[:cut] + "..."
            adapter.write(Text.assemble(
                (f"    {name}", _STYLE_DIM),
                (f"  {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
            detail += 1
        if len(failed) > 3:
            adapter.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM),
            ))
            detail += 1
        return lines + detail

    def render(self) -> str:
        return f"ToolSummary(success={len(self.successful)}, fail={len(self.failed)})"


class ErrorBlock(TuiComponent):
    """错误提示块 — 红色 ! 前缀。"""
    def __init__(self, message: str):
        self.message = _truncate_msg(message, _MAX_ERROR_LENGTH)

    def render(self) -> Text:
        return Text.assemble(("\n  ! ", _STYLE_ERROR), (self.message, _STYLE_ERROR))


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        return Text.assemble(("\n  · ", _STYLE_SUCCESS), (self.text, _STYLE_SUCCESS))


# ═══════════════════════════════════════════════════════════
# 底部栏组件
# ═══════════════════════════════════════════════════════════

class StatusLine(TuiComponent):
    """状态行 — 模型名 · tokens · 时间 · 工具计数。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.model: str = ""
        self.tokens: int = 0
        self.elapsed: float = 0.0
        self.tool_count: int = 0
        self.tool_fail: int = 0
        self.streaming: bool = False

    def render(self) -> str:
        """渲染为单行状态文本。"""
        parts = []
        if self.model:
            parts.append(self.model)
        if self.tokens:
            parts.append(f"{self.tokens}t")
        if self.elapsed:
            parts.append(f"{self.elapsed:.1f}s")
        if self.tool_count:
            s = f"⚙{self.tool_count}"
            if self.tool_fail:
                s += f"!{self.tool_fail}"
            parts.append(s)
        return " · ".join(parts) if parts else ""


class InputLine(TuiComponent):
    """输入行 — > 提示符 + 用户输入文本 + 光标。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.text: str = ""
        self.cursor_pos: int = 0

    def render(self) -> str:
        return f"> {self.text}"


class CompletionPopup(TuiComponent):
    """补全弹窗 — 浮动在输入行上方的候选项列表。

    由底部栏 _CompletionPopup 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.items: list[str] = []
        self.selected: int = 0
        self.visible: bool = False

    def show(self, items: list[str], selected: int = 0) -> None:
        self.items = items
        self.selected = selected
        self.visible = True

    def hide(self) -> None:
        self.visible = False
        self.items.clear()

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = []
        for i, item in enumerate(self.items):
            prefix = "→ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)


class SelectionMenu(TuiComponent):
    """底部选择菜单 — 供 user_select / 消息编辑 / 命令面板等使用。

    由底部栏 _BottomBar.run_bottom_bar_selection() 实际渲染。
    """
    def __init__(self):
        self.items: list[str] = []
        self.selected: int = 0
        self.visible: bool = False
        self.title: str = ""

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = [f"  {self.title}"] if self.title else []
        for i, item in enumerate(self.items):
            prefix = "▶ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 渲染状态管理
# ═══════════════════════════════════════════════════════════

@dataclass
class _RenderState:
    """推理/内容 IncrementalRenderer 生命周期管理。"""
    reasoning: "IncrementalRenderer | None" = None
    content: "IncrementalRenderer | None" = None
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE
    _shared_adapter: "OutputAdapter | None" = None

    def set_output_adapter(self, adapter: "OutputAdapter") -> None:
        self._shared_adapter = adapter

    def get_reasoning(self) -> "IncrementalRenderer | None":
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning is None:
            from ..api.renderer import IncrementalRenderer
            self.reasoning = IncrementalRenderer(
                style="dim", _file=sys.__stdout__,
                typing_speed=1000, show_indicator=False,
            )
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        if self.content is None:
            if self._shared_adapter is None:
                _logger.warning("get_content: _shared_adapter 未设置")
            from ..api.renderer import IncrementalRenderer
            self.content = IncrementalRenderer(
                style="", _file=sys.__stdout__,
                typing_speed=1000, show_indicator=False,
                output_adapter=self._shared_adapter,
            )
        return self.content

    def close_reasoning(self) -> None:
        if self.reasoning_state == _ReasoningState.CLOSED:
            return
        rr = self.reasoning
        if rr is not None:
            rr.write(_THINKING_SEPARATOR)
            rr.close()
            self.reasoning = None
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        if self.reasoning_state != _ReasoningState.CLOSED:
            return
        self.reasoning = None
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        cr = self.content
        if cr is not None:
            cr.close()
            self.content = None

    def close_all(self) -> None:
        try:
            self.close_reasoning()
        except Exception:
            pass
        try:
            self.close_content()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# 行数估算辅助
# ═══════════════════════════════════════════════════════════

def _estimate_content_lines(text: str) -> int:
    if not text:
        return 1
    return text.count('\n') + 1


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
        self._adapter.write(block.render())
        self._record_lines(_estimate_content_lines(f"\n{text}"))

    def _do_notification(self, text: str) -> None:
        block = NotificationBlock(text)
        self._adapter.write(block.render())
        self._record_lines(_estimate_content_lines(f"\n{text}"))

    def _do_error(self, message: str) -> None:
        block = ErrorBlock(message)
        self._adapter.write(block.render())
        self._record_lines(_estimate_content_lines(f"\n{message}"))

    def _do_write_line(self, text: str) -> None:
        if '\033[' in text:
            try:
                self._adapter.write(Text.from_ansi(text))
            except Exception:
                self._adapter.write_raw(text + "\n")
        else:
            self._adapter.write_raw(text + "\n")
        self._record_lines(_estimate_content_lines(text))

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
        self._record_lines(1)

    # ── SubAgent 面板 ─────────────────────────────

    def _do_subagent_frame(self, frame_lines: tuple) -> None:
        if not frame_lines:
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


# ═══════════════════════════════════════════════════════════
# 事件分发器
# ═══════════════════════════════════════════════════════════

_HANDLER_MAP: dict[str, tuple[type, str]] = {
    "ReasoningChunkEvent":  ("ReasoningChunkEvent",  "_on_reasoning_chunk"),
    "ContentChunkEvent":    ("ContentChunkEvent",    "_on_content_chunk"),
    "PhaseDoneEvent":       ("PhaseDoneEvent",       "_on_phase_done"),
    "ToolStartedEvent":     ("ToolStartedEvent",     "_on_tool_started"),
    "ToolDoneEvent":        ("ToolDoneEvent",        "_on_tool_done"),
    "ToolOutputChunkEvent": ("ToolOutputChunkEvent", "_on_tool_output"),
    "ParseInfoEvent":       ("ParseInfoEvent",       "_on_parse_info"),
    "ParseInfoDoneEvent":   ("ParseInfoDoneEvent",   "_on_parse_info_done"),
    "OutputEvent":          ("OutputEvent",          "_on_output"),
    "ModelPhaseEvent":      ("ModelPhaseEvent",      "_on_model_phase"),
    "ToolSummaryEvent":     ("ToolSummaryEvent",     "_on_tool_summary"),
}


class EventDispatcher:
    """DisplayEvent → RenderCommand 过滤+入队。"""

    def __init__(self, push_cmd: Callable[[tuple], None]):
        self._push_cmd = push_cmd

    @staticmethod
    def _is_agent_source(source: str | None) -> bool:
        if source is None:
            return False
        return source == _MAIN_SOURCE or source.startswith("agent-")

    def _on_reasoning_chunk(self, event) -> None:
        from ..ui.events.event_types import ReasoningChunkEvent
        if not isinstance(event, ReasoningChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event) -> None:
        from ..ui.events.event_types import ContentChunkEvent
        if not isinstance(event, ContentChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_phase_done(self, event) -> None:
        from ..ui.events.event_types import PhaseDoneEvent
        if not isinstance(event, PhaseDoneEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event) -> None:
        from ..ui.events.event_types import ToolStartedEvent
        if not isinstance(event, ToolStartedEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event) -> None:
        from ..ui.events.event_types import ToolDoneEvent
        if not isinstance(event, ToolDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))

    def _on_tool_output(self, event) -> None:
        from ..ui.events.event_types import ToolOutputChunkEvent
        if not isinstance(event, ToolOutputChunkEvent):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event) -> None:
        from ..ui.events.event_types import ParseInfoEvent
        if not isinstance(event, ParseInfoEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event) -> None:
        from ..ui.events.event_types import ParseInfoDoneEvent
        if not isinstance(event, ParseInfoDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event) -> None:
        from ..ui.events.event_types import OutputEvent
        if not isinstance(event, OutputEvent):
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event) -> None:
        from ..ui.events.event_types import ModelPhaseEvent
        if not isinstance(event, ModelPhaseEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        if event.phase != "error":
            return
        if not event.info:
            return
        info = _truncate_msg(event.info, _MAX_ERROR_LENGTH)
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event) -> None:
        from ..ui.events.event_types import ToolSummaryEvent
        if not isinstance(event, ToolSummaryEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))


# ═══════════════════════════════════════════════════════════
# TuiEngine — 渲染引擎
# ═══════════════════════════════════════════════════════════

class TuiEngine:
    """渲染引擎 — render 线程 + Queue 命令队列 + 三阶段渲染循环。

    组件化架构：所有内容通过 TuiRenderer 渲染，底部栏由 BottomBarProtocol 管理。
    """

    def __init__(
        self,
        renderer: TuiRenderer,
        bottom_bar: "BottomBarProtocol",
        cursor_tracker: Any = None,
    ):
        self._renderer = renderer
        self._bb = bottom_bar
        self._cursor_tracker = cursor_tracker
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._cmd_event = threading.Event()
        self._render_thread: threading.Thread | None = None
        self._render_running = False
        self._consecutive_full = 0
        self._bottom_redraw_requested = threading.Event()
        self._panel_refresh_cb: Callable[[], None] | None = None

    def push_cmd(self, cmd: tuple) -> None:
        try:
            self._cmd_queue.put(cmd, block=False)
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            self._consecutive_full += 1
            _logger.warning("渲染命令队列已满（%s 条），丢弃命令: %s", self._cmd_queue.qsize(), _cmd_name(cmd[0]))
            if self._consecutive_full >= _CONSECUTIVE_FULL_THRESHOLD:
                _logger.error("渲染输出管线持续拥堵（%d 次连续满队列）", self._consecutive_full)

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._panel_refresh_cb = callback

    def request_bottom_redraw(self) -> None:
        self._bottom_redraw_requested.set()

    def start(self) -> None:
        if self._render_thread is not None:
            if self._render_thread.is_alive():
                _logger.warning("start() 被重复调用，render 线程仍在运行，跳过")
                return
            self._render_thread.join()
        self._render_running = True
        self._render_thread = threading.Thread(target=self._render, daemon=True)
        self._render_thread.start()

    def stop(self) -> None:
        self._render_running = False
        if self._render_thread is not None:
            self._render_thread.join(timeout=2.0)
            if self._render_thread.is_alive():
                for _ in range(3):
                    self._render_thread.join(timeout=0.5)
                    if not self._render_thread.is_alive():
                        break
        self._drain_queue_safe()

    def flush(self, timeout: float | None = 5.0) -> None:
        if self._render_thread is None or not self._render_thread.is_alive():
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        task_done = threading.Thread(target=self._cmd_queue.join, daemon=True)
        task_done.start()
        task_done.join(timeout=timeout)

    def ensure_cursor_upper(self) -> None:
        self._bb.ensure_cursor_in_upper()

    # ── 三阶段流水线 ──────────────────────────────

    def _phase_pre_update_panels(self) -> None:
        if self._panel_refresh_cb is not None:
            try:
                self._panel_refresh_cb()
            except Exception:
                _logger.warning("panel_refresh_cb 异常", exc_info=True)

    def _phase_render(self, commands: list[tuple]) -> None:
        try:
            self._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        self.ensure_cursor_upper()
        for cmd in commands:
            try:
                self._renderer.render(cmd)
            except Exception:
                _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                self.push_cmd((RenderCommand.ERROR, f"渲染命令 {_cmd_name(cmd[0])} 失败"))

    def _phase_redraw_bottom(self, has_commands: bool) -> None:
        redraw = has_commands or self._bottom_redraw_requested.is_set() or self._bb.is_status_active
        self._bottom_redraw_requested.clear()
        if redraw:
            try:
                self._bb.force_redraw()
            except Exception:
                _logger.debug("force_redraw 异常", exc_info=True)
            try:
                self._position_cursor()
            except Exception:
                _logger.debug("position_cursor 异常", exc_info=True)

    # ── render 线程 ────────────────────────────────

    def _render(self) -> None:
        idle_count = 0
        try:
            while self._render_running:
                try:
                    has_content = self._drain_queue()
                    if has_content:
                        idle_count = 0
                        wait_timeout = _ACTIVE_RENDER_INTERVAL
                    else:
                        idle_count += 1
                        wait_timeout = (
                            _RENDER_INTERVAL
                            if idle_count >= _IDLE_DRAIN_THRESHOLD
                            else _ACTIVE_RENDER_INTERVAL
                        )
                    self._cmd_event.wait(timeout=wait_timeout)
                    self._cmd_event.clear()
                except Exception:
                    _logger.critical("render 线程异常崩溃", exc_info=True)
                    sys.__stderr__.write(
                        f"{_ANSI_RED}[ChatUI] render 线程异常终止，"
                        f"请联系开发人员查看日志{_ANSI_RESET}\n"
                    )
                    sys.__stderr__.flush()
                    self._render_running = False
                    break
        finally:
            self._drain_queue_safe()

    def _drain_queue(self) -> bool:
        commands: list[tuple] = []
        self._phase_pre_update_panels()
        from ..ui._lock import _try_acquire_output_lock
        with _try_acquire_output_lock(name="drain_queue", timeout=1.0) as locked:
            if not locked:
                return False
            while True:
                try:
                    commands.append(self._cmd_queue.get_nowait())
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            has_content = bool(commands)
            if commands:
                self._phase_render(commands)
            self._phase_redraw_bottom(has_content)
            return has_content

    def _drain_queue_safe(self) -> None:
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
            except queue.Empty:
                break

    def _position_cursor(self) -> None:
        if not getattr(self._bb, '_active', False):
            return
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        r_cursor, cursor_col = self._bb.compute_cursor_position(text, cursor_pos, h, w)
        try:
            from ..ui._blessed import get_terminal
            term = get_terminal()
            sys.__stdout__.write(term.move_xy(cursor_col - 1, r_cursor - 1))
        except Exception:
            sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()
        if self._cursor_tracker is not None:
            self._cursor_tracker.set(r_cursor, cursor_col)


# ═══════════════════════════════════════════════════════════
# 补全处理器（从 _completion 导入）
# ═══════════════════════════════════════════════════════════

from ._completion import _CmplHandler, _apply_completion


# ═══════════════════════════════════════════════════════════
# ChatUIConsumer — 对外公开 API（组件化架构）
# ═══════════════════════════════════════════════════════════

class ChatUIConsumer:
    """终端聊天消费者 — 组件化 TUI 架构。

    React Ink-like 组件层次：
      MessageStream ─── 滚动消息区（AnswerBlock / ThinkingBlock / ...）
      StatusLine    ─── 状态栏
      InputLine     ─── 输入行
      Overlay       ─── 补全弹窗 / 选择菜单（条件渲染）

    内部子系统：
      _rs       (_RenderState)    — 渲染器生命周期
      _engine   (TuiEngine)       — render 线程 + 命令队列
      _disp     (EventDispatcher) — 事件过滤+入队
      _renderer (TuiRenderer)     — 组件化渲染分发
      _cmpl     (_CmplHandler)    — Tab 补全交互
    """

    def __init__(self, event_bus=None):
        if event_bus is None:
            from ..ui.events.event_bus import DisplayEventBus
            event_bus = DisplayEventBus.get_default()
        self._bus = event_bus

        from ..ui._cursor_tracker import CursorTracker
        from ..ui._bottom_bar import _BottomBar
        from ..ui._completion import CompletionEngine
        from rich.console import Console
        from ..api.renderer.output import OutputAdapter
        from ..terminal import get_safe_console_config

        self._rs = _RenderState()
        self._cursor_tracker = CursorTracker()
        self._bottom_bar = _BottomBar(cursor_tracker=self._cursor_tracker)

        console = Console(**get_safe_console_config(), file=sys.__stdout__)
        output_adapter = OutputAdapter(console)

        from ..ui.tui._message_display import _display_messages

        self._tui_renderer = TuiRenderer(
            self._rs, output_adapter, self._bottom_bar,
            on_display_messages=_display_messages,
            cursor_tracker=self._cursor_tracker,
        )
        self._engine = TuiEngine(
            self._tui_renderer, self._bottom_bar,
            cursor_tracker=self._cursor_tracker,
        )
        self._disp = EventDispatcher(push_cmd=self._engine.push_cmd)
        self._rs.set_output_adapter(output_adapter)
        self._cmpl = _CmplHandler(
            self._bottom_bar, CompletionEngine(),
            request_redraw=self._engine.request_bottom_redraw,
        )
        self._bound_handlers: dict[type, Any] | None = None
        self._started = False

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        if self._bound_handlers is None:
            self._bound_handlers = {}
            from ..ui.events.event_types import (
                ReasoningChunkEvent, ContentChunkEvent, PhaseDoneEvent,
                ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent,
                ToolSummaryEvent, ParseInfoEvent, ParseInfoDoneEvent,
                OutputEvent, ModelPhaseEvent,
            )
            _event_type_map = {
                "ReasoningChunkEvent": ReasoningChunkEvent,
                "ContentChunkEvent": ContentChunkEvent,
                "PhaseDoneEvent": PhaseDoneEvent,
                "ToolStartedEvent": ToolStartedEvent,
                "ToolDoneEvent": ToolDoneEvent,
                "ToolOutputChunkEvent": ToolOutputChunkEvent,
                "ParseInfoEvent": ParseInfoEvent,
                "ParseInfoDoneEvent": ParseInfoDoneEvent,
                "OutputEvent": OutputEvent,
                "ModelPhaseEvent": ModelPhaseEvent,
                "ToolSummaryEvent": ToolSummaryEvent,
            }
            for key, (_, handler_name) in _HANDLER_MAP.items():
                event_type = _event_type_map[key]
                handler = getattr(self._disp, handler_name)
                self._bound_handlers[event_type] = handler
        for event_type in self._bound_handlers:
            try:
                self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)
            except Exception:
                pass
        for event_type in self._bound_handlers:
            self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)
        _register_consumer(self)
        self._engine.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        if self._bound_handlers is not None:
            for event_type in self._bound_handlers:
                try:
                    self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)
                except Exception:
                    _logger.debug("stop: unsubscribe %s 失败", event_type.__name__, exc_info=True)
        self._engine.flush()
        self._engine.stop()
        _unregister_consumer()
        from ..ui._lock import output_lock
        with output_lock:
            self._rs.close_all()
            self._bottom_bar.teardown()
        self._started = False

    def suspend(self) -> None:
        if not self._started:
            return
        self._engine.stop()
        self._engine.flush()
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.teardown()

    def resume(self) -> None:
        if not self._started:
            return
        if self._engine._render_running:
            return
        from ..ui._blessed import get_terminal
        from ..ui._lock import output_lock
        with output_lock:
            try:
                term = get_terminal()
                sys.__stdout__.write(term.move_xy(0, term.height - 1))
            except Exception:
                sys.__stdout__.write(_ANSI_CURSOR_BOTTOM)
            sys.__stdout__.flush()
            self._bottom_bar.setup()
            self._engine.start()

    # ── 公开方法 ──────────────────────────────────

    def on_user_message(self, text: str) -> None:
        self._engine.push_cmd((RenderCommand.USER_MSG, text))

    def on_notification(self, text: str) -> None:
        self._engine.push_cmd((RenderCommand.NOTIFICATION, text))

    def on_error(self, message: str) -> None:
        if not message:
            return
        self._engine.push_cmd((RenderCommand.ERROR, message))

    def refresh(self) -> None:
        pass

    def request_bottom_redraw(self) -> None:
        self._engine.request_bottom_redraw()

    def write_line(self, text: str) -> None:
        self._engine.push_cmd((RenderCommand.WRITE_LINE, text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        self._engine.push_cmd((RenderCommand.DISPLAY_MSGS, messages, speed))

    def wait_for_user_input(self, monitor, prefill: str = "", timeout: float | None = None) -> str:
        if prefill:
            monitor.set_prefill(prefill)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            text = monitor.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    def setup_completion(self, monitor) -> None:
        monitor.set_completion_callback(self._cmpl.on_tab)
        monitor.set_dismiss_completion_callback(self._cmpl.on_dismiss)
        monitor.set_completion_navigate_callback(self._cmpl.on_navigate)
        monitor.set_auto_completion_callback(self._cmpl.on_auto)

    @property
    def bottom_bar(self):
        return self._bottom_bar

    @property
    def output_adapter(self):
        return self._tui_renderer._adapter

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._engine.set_panel_refresh_callback(callback)

    def setup_bottom_bar(self) -> None:
        from ..ui._lock import output_lock
        with output_lock:
            self._bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        self._bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        self._engine.ensure_cursor_upper()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        effective_pos = len(text) if cursor_pos < 0 else cursor_pos
        self._bottom_bar.set_input_state(text, effective_pos)
        self._engine.request_bottom_redraw()

    def flush(self, timeout: float | None = 5.0) -> None:
        self._engine.flush(timeout=timeout)

    def push_cmd(self, cmd: tuple) -> None:
        self._engine.push_cmd(cmd)
