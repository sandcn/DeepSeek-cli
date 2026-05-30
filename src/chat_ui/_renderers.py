"""ContentRenderer — 14 种渲染命令的纯渲染方法。

仅在 Reader 线程中调用，通过持有 _RenderState / _BottomBar 等
依赖完成终端输出。所有方法同步阻塞 I/O（由 output_lock 串行化）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from wcwidth import wcswidth

from ..api.renderer.output import OutputAdapter
from ._const import (
    _CLEAR_PARSE_LINE,
    _MAIN_LABEL,
    _ReasoningState,
    _STYLE_BOLD,
    _STYLE_DIM,
    _STYLE_DIM_GREY,
    _STYLE_ERROR,
    _STYLE_FAIL,
    _STYLE_SUCCESS,
    _STYLE_USER,
    _STYLE_WARN,
    _THINKING_HEADER,
    RenderCommand,
    _cmd_name,
)
from ._render_state import _RenderState

if TYPE_CHECKING:
    from ..ui._bottom_bar import _BottomBar


def _build_render_dispatch() -> dict[int, tuple[str, tuple[int, ...]]]:
    """构建渲染命令分发表（模块级函数）。"""
    R = RenderCommand
    return {
        R.REASONING:      ("_do_reasoning",       (1,)),
        R.CONTENT:        ("_do_content",         (1,)),
        R.PHASE_DONE:     ("_do_phase_done",      (1,)),
        R.TOOL_OUTPUT:    ("_do_tool_output",     (1,)),
        R.TOOL_SUMMARY:   ("_do_tool_summary",    (1, 2)),
        R.USER_MSG:       ("_do_user_message",    (1,)),
        R.PARSE_INFO:     ("_do_parse_info",      (1, 2, 3)),
        R.CMD_OUTPUT:     ("_do_cmd_output",      (1,)),
        R.NOTIFICATION:   ("_do_notification",    (1,)),
        R.WRITE_LINE:     ("_do_write_line",      (1,)),
        R.DISPLAY_MSGS:   ("_do_display_messages", (1, 2)),
        R.TOOL_COUNT_INC: ("_do_tool_count_inc",  ()),
        R.TOOL_FAIL_INC:  ("_do_tool_fail_inc",   ()),
        R.ERROR:          ("_do_error",           (1,)),
    }


class ContentRenderer:
    """渲染命令执行器，持有 _RenderState / _BottomBar 等依赖完成终端输出。

    所有 _do_* 方法仅在 Reader 线程中同步调用（由 output_lock 串行化）。
    """

    def __init__(self, rs: _RenderState, bottom_bar: _BottomBar):
        self._rs = rs
        self._bottom_bar = bottom_bar
        # 渲染命令分发表（类级别，O(1) 查找）
        self._dispatch: dict[int, tuple[str, tuple[int, ...]]] = _build_render_dispatch()

    # ── 渲染器访问 ──────────────────────────────────

    @property
    def _tool_adapter(self) -> OutputAdapter:
        return self._rs.get_tool_adapter()

    # ── 渲染分发入口 ────────────────────────────────

    def render(self, cmd: tuple) -> None:
        """根据命令类型分发到对应渲染方法（O(1) 字典查找）。"""
        cid = cmd[0]

        entry = self._dispatch.get(cid)
        if entry is None:
            self._push_error(f"未知渲染命令: {_cmd_name(cid)}")
            return

        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

    def _push_error(self, message: str) -> None:
        """渲染错误命令（由外部 engine push 到队列）。"""
        self._do_error(message)

    # ── 内容渲染 ──────────────────────────────────────

    def _do_reasoning(self, text: str) -> None:
        """渲染推理内容块。"""
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is not None:
            if is_first:
                rr.write(_THINKING_HEADER)
            rr.write(text)

    def _do_content(self, text: str) -> None:
        """渲染内容块。"""
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)

    def _do_phase_done(self, phase: str) -> None:
        """阶段完成处理。"""
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    # ── 工具渲染 ──────────────────────────────────────

    def _do_tool_count_inc(self) -> None:
        """工具计数+1。"""
        self._bottom_bar.increment_tool()

    def _do_tool_fail_inc(self) -> None:
        """工具失败计数+1。"""
        self._bottom_bar.increment_tool_fail()

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（dim 样式 + 左侧竖线指示）。"""
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
            ta.write(Text.assemble(("  │ ", _STYLE_DIM_GREY), (text, _STYLE_DIM)))

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
                ("  ● ", _STYLE_SUCCESS),
                (f"{len(successful)}个工具完成", _STYLE_SUCCESS),
            ))

    # ── 用户消息/通知/错误 ───────────────────────────

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（青色 ▸ 前缀 + 粗体）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  ▸ ", _STYLE_USER),
            (text, _STYLE_BOLD),
        ))

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（绿色 ● 前缀）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  ● ", _STYLE_SUCCESS),
            (text, _STYLE_SUCCESS),
        ))

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ◆ 样式）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  ◆ ", _STYLE_ERROR),
            (message, _STYLE_ERROR),
        ))

    # ── 通用文本/命令输出 ───────────────────────────

    def _do_cmd_output(self, text: str) -> None:
        """渲染 / 命令执行输出。"""
        self._write_text_or_ansi(text)

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行。"""
        self._write_text_or_ansi(text)

    def _do_parse_info(self, tool_names: str, tokens: int, elapsed: float) -> None:
        """渲染工具参数接收进度（行内覆盖）。"""
        if tokens == _CLEAR_PARSE_LINE:
            self._tool_adapter.write_raw("\n")
            return
        self._tool_adapter.write_raw(
            f"\r\033[K  \u25c7 {tool_names} {tokens}t {elapsed:.2f}s",
        )

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。"""
        from ..ui.tui._message_display import _display_messages
        _display_messages(messages, speed=speed)

    # ── 辅助方法 ──────────────────────────────────────

    def _write_text_or_ansi(self, text: str) -> None:
        """按需渲染文本：含 ANSI 转义序列时解析着色，纯文本时直写。"""
        if '\033[' in text:
            self._tool_adapter.write(Text.from_ansi(text))
        else:
            self._tool_adapter.write_raw(text + "\n")

    @staticmethod
    def _truncate_by_visual_width(s: str, max_width: int) -> str:
        """按终端列宽截断，保留的尾部替换为省略号。"""
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
    def _render_failure_summary(cls, ta: OutputAdapter, failed: tuple, total: int) -> None:
        """渲染失败工具汇总行 + 失败详情（最多 3 条）。"""
        failed_names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            ta.write(Text.assemble(
                ("  ◆ ", _STYLE_FAIL),
                (f"全部失败: {failed_names}", _STYLE_FAIL),
            ))
        else:
            ta.write(Text.assemble(
                ("  ◆ ", _STYLE_WARN),
                (f"{len(failed)}/{total} 失败: {failed_names}", _STYLE_WARN),
            ))

        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    short = cls._truncate_by_visual_width(short, 80)
            ta.write(Text.assemble(
                (f"    {name}", _STYLE_DIM_GREY),
                (f": {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
        if len(failed) > 3:
            ta.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM_GREY),
            ))
