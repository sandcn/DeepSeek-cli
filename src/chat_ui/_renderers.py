"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）。

上屏历史管理（ScreenHistoryManager）已屏蔽为 No-op，
所有相关调用已移除，减轻每帧方法调用开销。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

from rich.text import Text
from wcwidth import wcswidth

from ._const import (
    _CLEAR_PARSE_LINE,
    _ReasoningState,
    _STYLE_BOLD,
    _STYLE_DIM,
    _STYLE_ERROR,
    _STYLE_FAIL,
    _STYLE_SUCCESS,
    _STYLE_WARN,
    _build_render_dispatch,
    _cmd_name,
)

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter
    from ._render_state import _RenderState


# ── 模块级渲染命令分发表（类定义时即构建，O(1) 查找） ──
# 替换原 ContentRenderer._ensure_dispatch() 惰性初始化模式，
# 消除每帧 render() 调用的 _RENDER_DISPATCH is None 检查。
_RENDER_DISPATCH: dict[int, tuple[str, tuple[int, ...]]] = _build_render_dispatch()


class ContentRenderer:
    """内容渲染器 — 执行 RenderCommand 并输出到终端。

    每个 _do_* 方法对应一种渲染命令，由 _render() 通过模块级 O(1)
    字典分发调用。所有方法在 Reader 线程中串行执行，无需额外同步。

    依赖：
      - _rs (_RenderState)：渲染器生命周期（推理/内容/工具适配器）
      - _bottom_bar：底部栏状态更新（工具计数/模型名）

    ScreenHistoryManager 已屏蔽为 No-op 且不在此模块中创建。
    """

    def __init__(
        self,
        rs: "_RenderState",
        bottom_bar,
        on_display_messages: Callable[..., None] | None = None,
    ):
        self._rs = rs
        self._bb = bottom_bar
        # ── display_messages 回调（由 ChatUIConsumer 注入） ──
        # 保持为实例属性，不受 ScreenHistoryManager 封装
        self._on_display_messages: Callable[..., None] | None = on_display_messages

    @property
    def _tool_adapter(self) -> "OutputAdapter":
        return self._rs.get_tool_adapter()

    # ── 渲染分发 ──────────────────────────────────────

    def render(self, cmd: tuple) -> None:
        """根据命令类型分发到对应渲染方法（模块级 O(1) 字典查找）。"""
        cid = cmd[0]

        entry = _RENDER_DISPATCH.get(cid)
        if entry is None:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return

        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

    # ── 内容渲染 ──────────────────────────────────────

    def _do_reasoning(self, text: str) -> None:
        """渲染推理内容块。"""
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is not None:
            if is_first:
                from ._const import _THINKING_HEADER
                rr.write(_THINKING_HEADER)
            rr.write(text)

    def _do_content(self, text: str) -> None:
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    # ── 工具渲染 ──────────────────────────────────────

    def _do_tool_count_inc(self) -> None:
        self._bb.increment_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（dim 样式 + 缩进）。"""
        ta = self._tool_adapter
        if '\r' in text:
            ta.write_raw(text)
            if text.endswith('\r'):
                self._rs.last_was_carriage = True
            else:
                ta.write_raw('\n')
                self._rs.last_was_carriage = False
        else:
            if self._rs.last_was_carriage:
                ta.write_raw("\n")
                self._rs.last_was_carriage = False
            ta.write(Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """渲染工具执行汇总（着色图标 + 彩色计数）。"""
        ta = self._tool_adapter
        if self._rs.last_was_carriage:
            ta.write_raw("\n")
            self._rs.last_was_carriage = False

        total = len(successful) + len(failed)
        if failed:
            self._render_failure_summary(ta, failed, total)
        elif successful:
            ta.write(Text.assemble(
                ("  · ", _STYLE_SUCCESS),
                (f"{len(successful)}工具完成", _STYLE_SUCCESS),
            ))

    @staticmethod
    def _truncate_by_visual_width(s: str, max_width: int) -> str:
        if not s:
            return s
        w = 0
        cut = len(s)
        for i, ch in enumerate(s):
            cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
            if w + cw > max_width - 3:
                cut = i
                break
            w += cw
        if cut < len(s):
            return s[:cut] + "..."
        return s

    @classmethod
    def _render_failure_summary(cls, ta: "OutputAdapter", failed: tuple, total: int) -> None:
        failed_names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            ta.write(Text.assemble(
                ("  ! ", _STYLE_FAIL),
                (f"全部失败: {failed_names}", _STYLE_FAIL),
            ))
        else:
            ta.write(Text.assemble(
                ("  ! ", _STYLE_WARN),
                (f"{len(failed)}/{total} 失败: {failed_names}", _STYLE_WARN),
            ))

        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    short = cls._truncate_by_visual_width(short, 80)
            ta.write(Text.assemble(
                (f"    {name}", _STYLE_DIM),
                (f"  {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
        if len(failed) > 3:
            ta.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM),
            ))

    def _do_parse_info(self, tool_names: str, tokens: int, elapsed: float) -> None:
        if tokens == _CLEAR_PARSE_LINE:
            self._tool_adapter.write_raw("\n")
            return
        self._tool_adapter.write_raw(
            f"\r\033[K  ~ {tool_names} {tokens}t {elapsed:.2f}s",
        )

    def _do_cmd_output(self, text: str) -> None:
        """渲染 / 命令执行输出，委托 _write_text_or_ansi。"""
        self._write_text_or_ansi(text)

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（> 前缀 + 加粗）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  > ", _STYLE_BOLD),
            (text, _STYLE_BOLD),
        ))

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（· 前缀）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  · ", _STYLE_SUCCESS),
            (text, _STYLE_SUCCESS),
        ))

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ! 样式）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  ! ", _STYLE_ERROR),
            (message, _STYLE_ERROR),
        ))

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行，委托 _write_text_or_ansi。"""
        self._write_text_or_ansi(text)

    def _write_text_or_ansi(self, text: str) -> None:
        if '\033[' in text:
            self._tool_adapter.write(Text.from_ansi(text))
        else:
            self._tool_adapter.write_raw(text + "\n")

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。

        通过 self._on_display_messages 回调调用（由 ChatUIConsumer 注入），
        消除对 tui._message_display 的直接 import 依赖。
        """
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
