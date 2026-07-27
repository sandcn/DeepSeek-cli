"""渲染器 — TuiRenderer + 聊天域渲染命令。

从 _tui.py 拆分，管理推理/内容 IncrementalRenderer 生命周期和渲染命令分发。
继承 FrameworkRenderer（renderer_base.py）获得框架级命令处理能力。
由 ComponentRegistry 管理命令-组件映射关系。
"""

from __future__ import annotations

import logging
import math
import sys
from typing import TYPE_CHECKING, Callable

from rich.text import Text

if TYPE_CHECKING:
    from ...renderer import IncrementalRenderer
    from ...renderer.output import OutputAdapter
    from ..widgets.cursor_tracker import CursorTracker
    from .protocols import BottomBarProtocol

from .const import (
    RenderCommand,
    _CLEAR_PARSE_LINE,
)
from ..state.render_state import ChatRenderState

from ..components import (
    ThinkingBlock,
    AnswerBlock,
    UserMsgBlock,
    ToolOutputBlock,
    ToolSummaryBlock,
)

from ..animation.animator import AnimatorContext
from ..core.effects import build_glow_ansi
from ..core.component_registry import ComponentRegistry
from .renderer_base import FrameworkRenderer, register_render_command
from .utils import _cmd_name, _emergency_write

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# TuiRenderer — 聊天域内容渲染器（继承 FrameworkRenderer）
# ═══════════════════════════════════════════════════════════

class TuiRenderer(FrameworkRenderer):
    """聊天域内容渲染器 — 执行 RenderCommand 并直接输出。

    继承 FrameworkRenderer 获得框架级命令处理能力（NOTIFICATION/WRITE_LINE/
    ERROR/SPLASH/SUBAGENT_FRAME），在此添加聊天域特有的渲染命令处理。

    将每个渲染命令映射到对应的组件，通过 OutputAdapter 输出。
    """

    def __init__(
        self,
        rs: ChatRenderState,
        output_adapter: "OutputAdapter",
        bottom_bar: "BottomBarProtocol",
        on_display_messages: Callable[..., None] | None = None,
        cursor_tracker: "CursorTracker | None" = None,
    ):
        super().__init__(
            output_adapter=output_adapter,
            cursor_tracker=cursor_tracker,
            bottom_bar=bottom_bar,
        )
        self._rs = rs
        self._on_display_messages = on_display_messages
        self._in_tool_group = False
        # AnswerBlock 实例缓存：同一轮回答的所有 CONTENT 命令复用同一实例，
        # 确保 _first_write 仅首次为 True（FadeIn 不重复），
        # _cumulative_content 持续累积（render() 可返回完整内容）。
        self._content_block: AnswerBlock | None = None
        # ThinkingBlock 实例缓存：同一次推理的所有 REASONING 命令复用同一实例，
        # 确保 _cumulative_content 持续累积（render() 可返回完整内容），
        # 与 AnswerBlock 的复用模式保持一致。
        self._reasoning_block: ThinkingBlock | None = None

    # ── 内容渲染 ──────────────────────────────────

    @register_render_command(RenderCommand.REASONING, (1,))
    def _do_reasoning(self, text: str) -> None:
        # ★ 复用 ThinkingBlock 实例（而非每次创建新实例），确保：
        #   - _is_first_write() 基于 reasoning_state 判断（Always correct）
        #   - _cumulative_content 持续累积（render() fallback 路径可用）
        if self._reasoning_block is None:
            self._reasoning_block = ThinkingBlock(self._rs)
        self._record_lines(self._reasoning_block.write(text))

    @register_render_command(RenderCommand.CONTENT, (1,))
    def _do_content(self, text: str) -> None:
        # 从 AnimatorContext 获取当前帧号，使后续组件能够使用呼吸效果
        _frame = AnimatorContext.get_default().frame
        # ★ 复用 AnswerBlock 实例（而非每次创建新实例），确保：
        #   - _first_write 仅首次为 True（FadeIn 正确作用于首个 chunk）
        #   - _cumulative_content 持续累积（render() 可返回完整内容）
        if self._content_block is None:
            self._content_block = AnswerBlock(self._rs)
        self._record_lines(self._content_block.write(text))

    @register_render_command(RenderCommand.PHASE_DONE, (1,))
    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
            # ★ 重置 ThinkingBlock 缓存，为下一轮推理的首个 REASONING 创建新实例
            self._reasoning_block = None
        elif phase == "content":
            self._rs.close_content()
            # ★ 重置 AnswerBlock 缓存，为下一轮回答的首个 CONTENT 创建新实例
            self._content_block = None

    # ── 工具渲染 ──────────────────────────────────

    @register_render_command(RenderCommand.TOOL_COUNT_INC, ())
    def _do_tool_count_inc(self) -> None:
        """工具计数+1：当 ToolStartedEvent 被处理时，通知底部栏增加工具计数。

        注册为 RenderCommand.TOOL_COUNT_INC 的命令处理器。
        触发时机：每次工具开始执行（ToolStartedEvent 分发后）。
        """
        self._bb.increment_tool()

    @register_render_command(RenderCommand.TOOL_COUNT_DEC, ())
    def _do_tool_count_dec(self) -> None:
        """工具计数-1：当 ToolDoneEvent 被处理时，通知底部栏减少工具计数。

        注册为 RenderCommand.TOOL_COUNT_DEC 的命令处理器。
        触发时机：每次工具执行完成（ToolDoneEvent 分发后）。
        """
        self._bb.decrement_tool()

    @register_render_command(RenderCommand.TOOL_FAIL_INC, ())
    def _do_tool_fail_inc(self) -> None:
        """工具失败计数+1：当工具执行失败时，通知底部栏增加失败计数。

        注册为 RenderCommand.TOOL_FAIL_INC 的命令处理器。
        触发时机：工具执行失败（失败的 ToolDoneEvent 分发后）。
        """
        self._bb.increment_tool_fail()

    @register_render_command(RenderCommand.MAIN_PHASE, (1,))
    def _do_main_phase(self, phase: str) -> None:
        self._bb.set_main_phase(phase)

    @register_render_command(RenderCommand.TOOL_OUTPUT, (1,))
    def _do_tool_output(self, text: str) -> None:
        if not self._in_tool_group:
            self._in_tool_group = True
            self._render_tool_panel("╭", "─", "工具调用")
        block = ToolOutputBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    # ── 工具 Panel 边框辅助方法（消除顶部/底部边框渲染重复） ─────────

    def _render_tool_panel(self, corner: str, char: str, label: str = "") -> None:
        """渲染工具 Panel 边框（带呼吸辉光的圆角框，统一顶部/底部）。

        Args:
            corner: 角字符，顶部传 ``╭``，底部传 ``╰``
            char: 边框填充字符，通常为 ``─``
            label: 标签文本，顶部传 ``工具调用``，底部不传（空字符串）
        """
        _frame = AnimatorContext.get_default().frame
        glow = build_glow_ansi(_frame, 23, 24)
        reverse_corner = corner.translate(str.maketrans({'╭': '╮', '╰': '╯'}))
        if label:
            line = f"  {glow}{corner}{char * 2} {label} {char * 2}{reverse_corner}\033[0m"
        else:
            line = f"  {glow}{corner}{char * 10}{reverse_corner}\033[0m"
        self._adapter.write(Text.from_ansi(line))
        self._record_lines(1)

    @register_render_command(RenderCommand.TOOL_SUMMARY, (1, 2))
    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        block = ToolSummaryBlock(successful, failed)
        self._record_lines(block.render_to_adapter(self._adapter))
        if self._in_tool_group:
            self._in_tool_group = False
            self._render_tool_panel("╰", "─")

    # ── 解析进度 ──────────────────────────────────

    @register_render_command(RenderCommand.PARSE_INFO, (1, 2, 3))
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

    @register_render_command(RenderCommand.USER_MSG, (1,))
    def _do_user_message(self, text: str) -> None:
        block = UserMsgBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    @register_render_command(RenderCommand.DISPLAY_MSGS, (1, 2))
    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
        self._record_lines(1)
