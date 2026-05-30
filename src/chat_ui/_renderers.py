"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from wcwidth import wcswidth

from ._const import (
    _CLEAR_PARSE_LINE,
    _ReasoningState,
    _STYLE_BOLD,
    _STYLE_DIM,
    _STYLE_DIM_GREY,
    _STYLE_ERROR,
    _STYLE_FAIL,
    _STYLE_SUCCESS,
    _STYLE_USER,
    _STYLE_WARN,
    _cmd_name,
)

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
    """

    def __init__(self, rs: "_RenderState", bottom_bar):
        self._rs = rs
        self._bb = bottom_bar

    @property
    def _tool_adapter(self) -> "OutputAdapter":
        return self._rs.get_tool_adapter()

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
            # 入队错误命令（通过 self._bb 回调方式不可行，改用日志）
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
        """渲染推理内容块。

        状态机驱动：
        - CLOSED → 重新打开推理渲染器（reopen_reasoning），
                    让工具调用后的二次推理能正常显示
        - INACTIVE → 惰性创建渲染器 + 打印思考标题 + 写入内容
        - ACTIVE → 直接写入内容
        """
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        # ★ 在 get_reasoning() 调用前保存 INACTIVE 标记，因为 get_reasoning()
        #    内部会将状态从 INACTIVE 切换为 ACTIVE
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
        """通过命令队列入队的工具计数+1，Reader 线程串行执行。"""
        self._bb.increment_tool()

    def _do_tool_fail_inc(self) -> None:
        """通过命令队列入队的工具失败计数+1，Reader 线程串行执行。"""
        self._bb.increment_tool_fail()

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（dim 样式 + 左侧竖线指示）。"""
        ta = self._tool_adapter
        if '\r' in text:
            ta.write_raw(text)
            # text 已在 _on_tool_output handler 中 rstrip('\n')，直接判断
            if text.endswith('\r'):
                self._rs.last_was_carriage = True
            else:
                # 含 \r 但不以 \r 结尾：write_raw 后光标不换行，
                # 补 \n 换行使后续 styled 输出正确在新行显示
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

    @staticmethod
    def _truncate_by_visual_width(s: str, max_width: int) -> str:
        """按终端列宽截断，保留的尾部替换为省略号。

        使用 wcswidth 计算视觉宽度（中文=2，英文=1），
        超过 max_width 时在截断位置前插入"..."。
        """
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

    def _do_parse_info(self, tool_names: str, tokens: int, elapsed: float) -> None:
        """渲染工具参数接收进度（行内覆盖）。

        使用 _CLEAR_PARSE_LINE（-1）作为清除哨兵——tokens < 0 时清除进度行。
        """
        if tokens == _CLEAR_PARSE_LINE:
            self._tool_adapter.write_raw("\n")
            return
        self._tool_adapter.write_raw(
            f"\r\033[K  \u25c7 {tool_names} {tokens}t {elapsed:.2f}s",
        )

    def _do_cmd_output(self, text: str) -> None:
        """渲染 / 命令执行输出，委托 _write_text_or_ansi。"""
        self._write_text_or_ansi(text)

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
        """渲染系统错误信息（红色 ◆ 样式）。

        由 RenderCommand.ERROR 命令触发（Reader 线程串行执行），
        通过 _tool_adapter.write() 输出到终端内容区。

        注意：此方法不应产生任何日志调用。若意外触发 logger，
        ChatUIErrorHandler.emit() 中的 _chatui_reported 标记
        会跳过自引用循环。
        """
        self._tool_adapter.write(Text.assemble(
            ("\n  ◆ ", _STYLE_ERROR),
            (message, _STYLE_ERROR),
        ))

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行，委托 _write_text_or_ansi。"""
        self._write_text_or_ansi(text)

    def _write_text_or_ansi(self, text: str) -> None:
        """按需渲染文本：含 ANSI 转义序列时解析着色，纯文本时直写。

        ANSI 路径：Text.from_ansi 解析 → OutputAdapter.write() → console.print 保证末尾换行。
        纯文本路径：write_raw 直写终端，显式追加 \\n，绕过 Rich 解析开销。
        注：检测 '\\033[' 覆盖本项目所有 CSI 序列，非 CSI 转义序列（OSC/DEC 等）未使用。
        """
        if '\033[' in text:
            self._tool_adapter.write(Text.from_ansi(text))
        else:
            self._tool_adapter.write_raw(text + "\n")

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。"""
        from ..ui.tui._message_display import _display_messages
        _display_messages(messages, speed=speed)
