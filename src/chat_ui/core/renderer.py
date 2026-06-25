from __future__ import annotations

"""
渲染器 — _RenderState + TuiRenderer + _RENDER_DISPATCH。

从 _tui.py 拆分，管理推理/内容 IncrementalRenderer 生命周期和渲染命令分发。
"""

import logging
import math
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ...api.renderer import IncrementalRenderer
    from ...api.renderer.output import OutputAdapter
    from ..components.base import TuiComponent
    from ..infrastructure.protocol import BottomBarProtocol
    from ..infrastructure.terminal import TerminalIO

from ..commands.const import (
    RenderCommand,
    _CLEAR_PARSE_LINE,
)

from ..commands.types import (
    CmdReasoning,
    CmdContent,
    CmdPhaseDone,
    CmdToolOutput,
    CmdToolSummary,
    CmdUserMsg,
    CmdParseInfo,
    CmdNotification,
    CmdWriteLine,
    CmdDisplayMsgs,
    CmdToolCountInc,
    CmdToolFailInc,
    CmdToolCountDec,
    CmdError,
    CmdSubagentFrame,
    CmdAnimationTick,
    CmdToolCallUpdate,
)
from ..state.render_state import _RenderState

from ..components.base import (
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

from ..infrastructure.utils import _cmd_name
from ..components.subagent_frame import SubagentFrameRenderer

_logger = logging.getLogger(__name__)


# @deprecated: 使用 TuiRenderer.render() 中的 match-case 分发替代。
# 保留仅为 test_chat_ui_handler.py:269 的向后兼容引用。
# 计划 2 个版本后移除。
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
        terminal_io: "TerminalIO | None" = None,
    ):
        self._rs = rs
        self._bb = bottom_bar
        self._on_display_messages = on_display_messages
        self._adapter = output_adapter
        self._tracker = cursor_tracker
        self._tio = terminal_io

    @property
    def output_adapter(self):
        """OutputAdapter 实例 — 公开只读属性。

        供外部消费者（如 InkRenderer 初始化）获取适配器引用，
        避免直接访问私有属性 _adapter。
        """
        return self._adapter

    @property
    def render_state(self):
        """RenderState 实例 — 公开只读属性。

        供 DirectRenderStrategy / RichLiveContentRenderer 等
        外部模块获取渲染状态，避免通过 getattr 访问私有 _rs。
        """
        return self._rs

    def render(self, cmd) -> None:
        """分发渲染命令到对应的 _do_* 方法（isinstance 多态分发）。

        通过 match-case 替代 _RENDER_DISPATCH 字典查表 + getattr 反射，
        提供类型安全和 IDE 自动补全。

        Args:
            cmd: 渲染命令 dataclass 实例（CmdReasoning / CmdContent / ...）
        """
        match cmd:
            case CmdReasoning(text=text):
                self._do_reasoning(text)
            case CmdContent(text=text):
                self._do_content(text)
            case CmdPhaseDone(phase=phase):
                self._do_phase_done(phase)
            case CmdToolOutput(text=text):
                self._do_tool_output(text)
            case CmdToolSummary(successful=s, failed=f):
                self._do_tool_summary(s, f)
            case CmdUserMsg(text=text):
                self._do_user_message(text)
            case CmdParseInfo(tool_names=tn, tokens=tok, elapsed=el):
                self._do_parse_info(tn, tok, el)
            case CmdNotification(text=text):
                self._do_notification(text)
            case CmdWriteLine(text=text):
                self._do_write_line(text)
            case CmdDisplayMsgs(messages=msgs, speed=spd):
                self._do_display_messages(msgs, spd)
            case CmdToolCountInc():
                self._do_tool_count_inc()
            case CmdToolFailInc():
                self._do_tool_fail_inc()
            case CmdToolCountDec():
                self._do_tool_count_dec()
            case CmdError(message=msg):
                self._do_error(msg)
            case CmdSubagentFrame(frame_lines=fl):
                self._do_subagent_frame(fl)
            case CmdAnimationTick():
                # 动画滴答由 TuiEngine._drain_queue() 统一处理，
                # 此处为防御性空操作，防止未过滤的 CmdAnimationTick 落入 _ 分支。
                pass
            case CmdToolCallUpdate(tool_id=tid, name=name, status=status, text=text,
                                   params_summary=ps, elapsed_ms=ems):
                self._do_tool_call_update(tid, name, status, text, ps, ems)
            case _:
                _logger.error("未知渲染命令类型: %s", type(cmd).__name__)

    def _record_lines(self, n: int) -> None:
        if self._tracker is not None:
            self._tracker.record_newlines(n)

    # ── 内容渲染 ──────────────────────────────────

    def _do_reasoning(self, text: str) -> None:
        block = self._rs.get_thinking_block()
        self._record_lines(block.write(text))

    def _do_content(self, text: str) -> None:
        block = self._rs.get_answer_block()
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

    def _do_tool_call_update(self, tool_id: str, name: str, status: str, text: str,
                             params_summary: str = "", elapsed_ms: float = 0.0) -> None:
        """渲染工具调用状态更新。

        running: 渲染带 spinner 图标的 ⚙ {name} 行（dim 样式）
        completed: 渲染 ✓ {name} 完成标记（green 样式）
        failed: 渲染 ✗ {name} 失败标记（red 样式）
        """
        try:
            from ..infrastructure.styled import StyledText

            text = text.strip()

            if status == "running":
                line = StyledText.assemble(
                    ("  ⚙ ", "bright_black"),
                    (name, "bright_black"),
                )
            elif status == "completed":
                line = StyledText.assemble(
                    ("  ✓ ", "green"),
                    (name, "green"),
                )
            elif status == "failed":
                line = StyledText.assemble(
                    ("  ✗ ", "red"),
                    (name, "red"),
                )
            else:
                line = StyledText.assemble(
                    ("  · ", ""),
                    (name, ""),
                )

            self._adapter.write(line)
            self._record_lines(1)

            # 如果有附加文本（工具输出摘要），也渲染出来
            if text:
                summary = StyledText.assemble(
                    ("     ", "bright_black"),
                    (text, "bright_black"),
                )
                self._adapter.write(summary)
                self._record_lines(1)
        except Exception:
            _logger.warning("_do_tool_call_update 渲染异常", exc_info=True)

    # ── 解析进度 ──────────────────────────────────

    def _do_parse_info(self, tool_names: str, tokens, elapsed: float) -> None:
        if tokens == _CLEAR_PARSE_LINE:
            self._adapter.write_raw("\n")
            self._record_lines(1)
            return
        if isinstance(tokens, (int, float)):
            tokens_str = f"{tokens}t" if math.isfinite(tokens) else "?"
        else:
            tokens_str = str(tokens)
        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        self._adapter.write_raw(output)

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
        SubagentFrameRenderer().render(frame_lines, self._adapter)

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


# ═══════════════════════════════════════════════════════════
# RichLiveContentRenderer — Rich Live 内容区渲染器
# ═══════════════════════════════════════════════════════════

class RichLiveContentRenderer:
    """Rich Live 内容区渲染器 — 差分渲染，替代手动 ANSI 刷新。

    通过配置开关 chat_ui.render.use_rich_live 控制启用。
    仅管理滚动内容区（DECSTBM 上方），底部栏保留手动 DECSTBM 管理。
    """

    def __init__(self, render_state: _RenderState, output_adapter):
        self._rs = render_state
        self._adapter = output_adapter
        self._live = None
        self._content_buffer: list[str] = []
        self._available = False
        try:
            from rich.live import Live
            from rich.panel import Panel
            self._Live = Live
            self._Panel = Panel
            self._available = True
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> None:
        """启动 Rich Live 上下文。"""
        if not self._available:
            return
        self._live = self._Live(
            self._Panel(" ", title="Chat"),
            refresh_per_second=10,
            auto_refresh=False,
            console=self._adapter._console if hasattr(self._adapter, '_console') else None,
        )
        self._live.start()

    def update_content(self, text: str) -> None:
        """追加内容到缓冲区"""
        self._content_buffer.append(text)

    def refresh(self) -> None:
        """触发布局刷新"""
        if self._live and self._available:
            try:
                content = "\n".join(self._content_buffer[-100:])  # 最近100行
                self._live.update(
                    self._Panel(content or " ", title="Chat")
                )
            except Exception:
                pass
            self._content_buffer = self._content_buffer[-200:]

    def stop(self) -> None:
        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None


__all__ = ["TuiRenderer", "_RenderState", "RichLiveContentRenderer"]
