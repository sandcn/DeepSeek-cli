"""内容渲染器模块 — TuiRenderer 聊天域内容渲染。

从 ``_renderer.py`` 提取为独立子模块。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Callable, Union

from src.tui._const import (
    RenderCommand,
    RenderCmd,
    ReasoningCmd, ContentCmd, PhaseDoneCmd,
    ToolOutputCmd, ToolSummaryCmd,
    UserMsgCmd, ParseInfoCmd,
    NotificationCmd, WriteLineCmd, DisplayMsgsCmd,
    ErrorCmd,
    SubagentFrameCmd, MainPhaseCmd,
    CONTENT_COMMANDS,
    _CLEAR_PARSE_LINE,
)

if TYPE_CHECKING:
    from src.tui._bottom_bar import _BottomBar
    from src.tui.state.render_state import ChatRenderState
    from src.tui._cursor_tracker import CursorTracker
    from src.renderer.output import OutputAdapter
    from src.tui._output import RenderOutput

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。"""
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


def _emergency_write(text: str, stream: str = "stdout") -> None:
    """紧急输出 — 绕过 OutputAdapter 直写终端。"""
    import sys
    f = sys.__stdout__ if stream == "stdout" else sys.__stderr__
    f.write(text)
    f.flush()


# ═══════════════════════════════════════════════════════════
# TuiRenderer — 内容渲染器
# ═══════════════════════════════════════════════════════════

class TuiRenderer:
    """聊天域内容渲染器 — 执行 RenderCommand 并直接输出。

    使用 dict 分发命令 ID 到对应的 _do_* 方法。
    仅接受 RenderCmd 数据类输入（tuple 双格式已移除）。
    """

    _CONTENT_COMMANDS = CONTENT_COMMANDS  # 真源在 _const.CONTENT_COMMANDS（步骤 4.1）

    def __init__(
        self,
        rs: "ChatRenderState",
        output_adapter: "OutputAdapter",
        bottom_bar: "_BottomBar",
        on_display_messages: Callable[..., None] | None = None,
        cursor_tracker: "CursorTracker | None" = None,
        render_output: "RenderOutput | None" = None,
    ):
        self._rs = rs
        # 统一输出端口（可选）：提供时作为内容写路径（装饰 OutputAdapter，
        # 叠加行跟踪回调）；未提供时直接使用 output_adapter（兼容旧构造）
        self._adapter = render_output if render_output is not None else output_adapter
        self._bb = bottom_bar
        self._on_display_messages = on_display_messages
        self._tracker = cursor_tracker
        self._in_tool_group = False

        # 命令分发字典 (int → Callable)
        self._handlers: dict[int, Callable] = {
            RenderCommand.NOTIFICATION: self._do_notification,
            RenderCommand.WRITE_LINE: self._do_write_line,
            RenderCommand.ERROR: self._do_error,
            RenderCommand.SPLASH: self._do_splash,
            RenderCommand.SUBAGENT_FRAME: self._do_subagent_frame,
            RenderCommand.REASONING: self._do_reasoning,
            RenderCommand.CONTENT: self._do_content,
            RenderCommand.PHASE_DONE: self._do_phase_done,
            RenderCommand.TOOL_COUNT_INC: self._do_tool_count_inc,
            RenderCommand.TOOL_COUNT_DEC: self._do_tool_count_dec,
            RenderCommand.TOOL_FAIL_INC: self._do_tool_fail_inc,
            RenderCommand.MAIN_PHASE: self._do_main_phase,
            RenderCommand.TOOL_OUTPUT: self._do_tool_output,
            RenderCommand.TOOL_SUMMARY: self._do_tool_summary,
            RenderCommand.PARSE_INFO: self._do_parse_info,
            RenderCommand.USER_MSG: self._do_user_message,
            RenderCommand.DISPLAY_MSGS: self._do_display_messages,
        }

    # ── 批量渲染支持 ──────────────────────────────

    _BATCHABLE_COMMANDS = frozenset({
        RenderCommand.NOTIFICATION,
        RenderCommand.WRITE_LINE,
        RenderCommand.ERROR,
        RenderCommand.TOOL_OUTPUT,
        RenderCommand.TOOL_SUMMARY,
        RenderCommand.USER_MSG,
    })

    def _is_batchable(self, cmd_or_cid: Union[int, RenderCmd]) -> bool:
        if isinstance(cmd_or_cid, int):
            return cmd_or_cid in self._BATCHABLE_COMMANDS
        if isinstance(cmd_or_cid, RenderCmd):
            return cmd_or_cid.cid in self._BATCHABLE_COMMANDS
        return False

    def render_batch(self, commands: list[RenderCmd]) -> None:
        from rich.text import Text
        renderables: list = []
        for cmd in commands:
            if not isinstance(cmd, RenderCmd):
                raise TypeError(f"不支持的命令类型: {type(cmd)}")
            cid = cmd.cid

            if cid == RenderCommand.NOTIFICATION:
                text = cmd.text
                renderables.append(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
                self._record_lines(1)

            elif cid == RenderCommand.WRITE_LINE:
                text = cmd.text
                renderables.append(Text.from_ansi(text))
                self._record_lines(1)

            elif cid == RenderCommand.ERROR:
                message = cmd.message
                renderables.append(Text.from_ansi(
                    f"  \033[1;38;5;196m!\033[0m \033[38;5;196m{message}\033[0m"
                ))
                self._record_lines(1)

            elif cid == RenderCommand.TOOL_OUTPUT:
                text = cmd.text
                if not self._in_tool_group:
                    self._in_tool_group = True
                    renderables.append(Text.from_ansi(
                        f"  \033[38;5;23m\u256d\u2500\u2500 工具调用 \u2500\u2500\u256e\033[0m"
                    ))
                renderables.append(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
                self._record_lines(1)

            elif cid == RenderCommand.TOOL_SUMMARY:
                if self._in_tool_group:
                    self._in_tool_group = False
                    renderables.append(Text.from_ansi(
                        f"  \033[38;5;23m\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f\033[0m"
                    ))
                self._record_lines(1)

            elif cid == RenderCommand.USER_MSG:
                text = cmd.text
                renderables.append(Text.from_ansi(
                    f"\n  \033[1;38;5;81m>\033[0m \033[38;5;252m{text}\033[0m\n"
                ))
                self._record_lines(2)

        if renderables:
            self._adapter.batch_write(renderables)

    @property
    def output_adapter(self) -> "OutputAdapter":
        return self._adapter

    def _record_lines(self, n: int) -> None:
        if self._tracker is not None:
            self._tracker.record_newlines(n)

    def render(self, cmd: RenderCmd) -> None:
        if not isinstance(cmd, RenderCmd):
            raise TypeError(f"不支持的命令类型: {type(cmd)}")
        cid = cmd.cid
        handler = self._handlers.get(cid)
        if handler is None:
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return
        try:
            handler(*self._cmd_args(cmd))
        except TypeError:
            _logger.error(
                "渲染命令 %s 参数错误: 期望签名与传入参数不匹配",
                _cmd_name(cid),
                exc_info=True,
            )

    def _cmd_args(self, cmd: RenderCmd) -> tuple:
        """提取 handler 位置参数（RenderCmd 属性 → 位置参数）。

        显式 isinstance 链（与旧 _cmd_to_tuple 的 per-type 映射一致），
        消除 hasattr 属性探测在 RenderCmd 扩展时误捕的风险。
        """
        if isinstance(cmd, ToolSummaryCmd):
            return (cmd.successful, cmd.failed)
        if isinstance(cmd, DisplayMsgsCmd):
            return (cmd.messages, cmd.speed)
        if isinstance(cmd, ParseInfoCmd):
            return (cmd.tool_names, cmd.tokens, cmd.elapsed)
        if isinstance(cmd, SubagentFrameCmd):
            return (cmd.frame_lines,)
        if isinstance(cmd, (WriteLineCmd, NotificationCmd, ToolOutputCmd,
                            UserMsgCmd, ReasoningCmd, ContentCmd)):
            return (cmd.text,)
        if isinstance(cmd, ErrorCmd):
            return (cmd.message,)
        if isinstance(cmd, (MainPhaseCmd, PhaseDoneCmd)):
            return (cmd.phase,)
        # 其余命令无参（ToolCountIncCmd/ToolCountDecCmd/ToolFailIncCmd/SplashCmd）
        return ()

    # ═══════════════════════════════════════════════════════
    # 框架级命令
    # ═══════════════════════════════════════════════════════

    def _do_notification(self, text: str) -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
        self._record_lines(1)

    def _do_write_line(self, text: str) -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(text))
        self._record_lines(1)

    def _do_error(self, message: str) -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(
            f"  \033[1;38;5;196m!\033[0m \033[38;5;196m{message}\033[0m"
        ))
        self._record_lines(1)

    def _do_splash(self) -> None:
        from rich.text import Text
        model_name = getattr(self._bb, '_model_name', '')
        splash_text = f"\n  \033[1;38;5;45mDeepSeek CLI\033[0m"
        if model_name:
            splash_text += f" \033[38;5;242m· {model_name}\033[0m"
        splash_text += "\n"
        self._adapter.write(Text.from_ansi(splash_text))
        self._record_lines(2)

    def _do_subagent_frame(self, frame_lines: tuple | list) -> None:
        if isinstance(frame_lines, list) and not frame_lines:
            if hasattr(self._bb, 'set_subagent_frame'):
                self._bb.set_subagent_frame([])
            return
        if not frame_lines:
            return
        if isinstance(frame_lines, (list, tuple)) and frame_lines and isinstance(frame_lines[0], str):
            lines = frame_lines
        elif len(frame_lines) >= 4 and isinstance(frame_lines[0], (list, tuple)):
            lines = frame_lines[0]
        else:
            return
        if hasattr(self._bb, 'set_subagent_frame'):
            self._bb.set_subagent_frame(list(lines))

    # ═══════════════════════════════════════════════════════
    # 聊天域命令
    # ═══════════════════════════════════════════════════════

    def _do_reasoning(self, text: str) -> None:
        rr = self._rs.get_reasoning()
        if rr is not None:
            rr.write(text)
            self._record_lines(0)
        elif text:
            # 推理渲染器已关闭：显式丢弃（不静默、不重建不错位），
            # 与 _do_content 的关闭后丢弃语义一致（渲染状态层兜底）。
            _logger.debug("推理渲染器已关闭，丢弃推理文本（%d 字符）", len(text))

    def _do_content(self, text: str) -> None:
        cr = self._rs.get_content()
        if cr is not None:
            cr.write(text)
            self._record_lines(0)
        elif text:
            # 内容渲染器已关闭：显式丢弃（不静默、不重建不错位）。
            # 多轮会话由 _do_main_phase 触发 reopen_content 后重建。
            _logger.debug("内容渲染器已关闭，丢弃内容文本（%d 字符）", len(text))

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    def _do_tool_count_inc(self) -> None:
        self._bb.increment_tool()

    def _do_tool_count_dec(self) -> None:
        self._bb.decrement_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    def _do_main_phase(self, phase: str) -> None:
        if phase == "thinking":
            self._rs.reopen_reasoning()
        if phase in ("thinking", "answering"):
            # 新一轮内容开始前重开 content 通道（多轮会话：工具调用关闭
            # content 后，下一轮首个 content 前由 ContentHandler 发布
            # ModelPhaseEvent("answering") → MainPhaseCmd 先于首个 ContentCmd
            # 出队，seq 保序保证 reopen 先于新内容渲染；"thinking" 为推理模型
            # 新一轮信号（兜底）。
            self._rs.reopen_content()
        self._bb.set_main_phase(phase)

    def _do_tool_output(self, text: str) -> None:
        from rich.text import Text
        if not self._in_tool_group:
            self._in_tool_group = True
            self._adapter.write(Text.from_ansi(
                f"  \033[38;5;23m\u256d\u2500\u2500 工具调用 \u2500\u2500\u256e\033[0m"
            ))
            self._record_lines(1)
        self._adapter.write(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
        self._record_lines(1)

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        from rich.text import Text
        if self._in_tool_group:
            self._in_tool_group = False
            self._adapter.write(Text.from_ansi(
                f"  \033[38;5;23m\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f\033[0m"
            ))
            self._record_lines(1)

    def _do_parse_info(self, tool_names: str, tokens, elapsed: float) -> None:
        if tokens == _CLEAR_PARSE_LINE:
            # 换行结束进度行（write_raw 保持行内覆盖语义，不经 Rich 渲染）
            self._adapter.write_raw("\n")
            self._record_lines(1)
            return
        if isinstance(tokens, (int, float)):
            tokens_str = f"{tokens}t" if math.isfinite(tokens) else "?"
        else:
            tokens_str = str(tokens)
        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        # 统一输出管线：进度行走 OutputAdapter.write_raw（\r 覆盖语义，
        # 不使用 _emergency_write 绕过）
        self._adapter.write_raw(output)

    def _do_user_message(self, text: str) -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(
            f"\n  \033[1;38;5;81m>\033[0m \033[38;5;252m{text}\033[0m\n"
        ))
        self._record_lines(2)

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
        self._record_lines(1)


__all__ = ["TuiRenderer", "_cmd_name", "_emergency_write"]
