"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）+ _screen_history（ScreenHistoryManager）。

上屏历史管理已提取到 _screen_history.py 的 ScreenHistoryManager 类。
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
    _cmd_name,
)
from ._screen_history import ScreenHistoryManager

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter
    from ._render_state import _RenderState


class ContentRenderer:
    """内容渲染器 — 执行 RenderCommand 并输出到终端。

    每个 _do_* 方法对应一种渲染命令，由 _render() 通过 O(1) 字典分发调用。
    所有方法在 Reader 线程中串行执行，无需额外同步。

    依赖：
      - _rs (_RenderState)：渲染器生命周期（推理/内容/工具适配器）
      - _bottom_bar：底部栏状态更新（工具计数/模型名）
      - _shm (ScreenHistoryManager)：上屏历史记录与重放

    上屏历史重放（Screen History Replay）：
      ScreenHistoryManager 管理 _screen_history 和累积缓冲区，
      replay() 在终端 resize 后重新绘制上屏内容。
    """

    def __init__(
        self,
        rs: "_RenderState",
        bottom_bar,
        on_display_messages: Callable[..., None] | None = None,
    ):
        self._rs = rs
        self._bb = bottom_bar

        # ── 上屏历史管理器（终端 resize 后重放用） ──
        self._shm = ScreenHistoryManager(
            on_display_messages=on_display_messages,
        )

    @property
    def _tool_adapter(self) -> "OutputAdapter":
        return self._rs.get_tool_adapter()

    @property
    def _screen_history(self) -> list[tuple]:
        """向后兼容：供测试/调试直接访问历史记录。"""
        return self._shm.screen_history

    # ── 渲染分发 ──────────────────────────────────────

    _RENDER_DISPATCH = None  # 由 _const._build_render_dispatch() 填充

    @classmethod
    def _ensure_dispatch(cls) -> dict[int, tuple[str, tuple[int, ...]]]:
        """惰性初始化渲染命令分发表。"""
        if cls._RENDER_DISPATCH is None:
            from ._const import _build_render_dispatch
            cls._RENDER_DISPATCH = _build_render_dispatch()
        return cls._RENDER_DISPATCH

    def render(self, cmd: tuple) -> None:
        """根据命令类型分发到对应渲染方法（O(1) 字典查找）。"""
        cid = cmd[0]

        entry = self._ensure_dispatch().get(cid)
        if entry is None:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return

        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

    # ── 上屏历史管理（委托 ScreenHistoryManager） ────

    def clear_screen_history(self) -> None:
        """清空上屏历史记录（新会话开始前调用）。"""
        self._shm.clear()

    def replay_upper_screen(self) -> None:
        """终端尺寸变化后重放上屏历史内容（委托 ScreenHistoryManager）。"""
        self._shm.replay(self._tool_adapter, self._bb)

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
        # ── 上屏历史：累积推理文本 ──
        self._shm.append_reasoning(text)

    def _do_content(self, text: str) -> None:
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
            # ── 上屏历史：关闭推理时刷新累积缓冲区 ──
            self._shm.flush_reasoning()
        self._rs.get_content().write(text)
        # ── 上屏历史：累积内容文本 ──
        self._shm.append_content(text)

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
            self._shm.flush_reasoning()
        elif phase == "content":
            self._rs.close_content()
            self._shm.flush_content()

    # ── 工具渲染 ──────────────────────────────────────

    def _do_tool_count_inc(self) -> None:
        self._bb.increment_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（dim 样式 + 缩进）。"""
        self._shm.flush_all()
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
            # ── 上屏历史：保存工具输出（不含 \r 行内覆盖类） ──
            self._shm.record('tool_output', text)

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """渲染工具执行汇总（着色图标 + 彩色计数）。"""
        self._shm.flush_all()
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
        self._shm.record('tool_summary', successful, failed)

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
        self._shm.flush_all()
        self._write_text_or_ansi(text)
        self._shm.record('cmd_output', text)

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（> 前缀 + 加粗）。"""
        self._shm.flush_all()
        self._tool_adapter.write(Text.assemble(
            ("\n  > ", _STYLE_BOLD),
            (text, _STYLE_BOLD),
        ))
        self._shm.record('user_msg', text)

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（· 前缀）。"""
        self._shm.flush_all()
        self._tool_adapter.write(Text.assemble(
            ("\n  · ", _STYLE_SUCCESS),
            (text, _STYLE_SUCCESS),
        ))
        self._shm.record('notification', text)

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ! 样式）。"""
        self._shm.flush_all()
        self._tool_adapter.write(Text.assemble(
            ("\n  ! ", _STYLE_ERROR),
            (message, _STYLE_ERROR),
        ))
        self._shm.record('error', message)

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行，委托 _write_text_or_ansi。"""
        self._shm.flush_all()
        self._write_text_or_ansi(text)
        self._shm.record('write_line', text)

    def _write_text_or_ansi(self, text: str) -> None:
        if '\033[' in text:
            self._tool_adapter.write(Text.from_ansi(text))
        else:
            self._tool_adapter.write_raw(text + "\n")

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。

        通过 self._shm 的 on_display_messages 回调调用（由 ChatUIConsumer 注入），
        消除对 tui._message_display 的直接 import 依赖。
        """
        cb = self._shm.on_display_messages
        if cb is not None:
            cb(messages, speed=speed)
        self._shm.record('display_msgs', list(messages), speed)
