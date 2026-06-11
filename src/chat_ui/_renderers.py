"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）。
不再使用 Control 控件体系，每个 _do_* 方法直接通过 OutputAdapter 或 sys.__stdout__ 输出。
"""

from __future__ import annotations

import logging
import sys
import unicodedata
from typing import TYPE_CHECKING, Callable

_logger = logging.getLogger(__name__)

from ._const import (
    _CLEAR_PARSE_LINE,
    _MAX_ERROR_LENGTH,
    _STYLE_BOLD,
    _STYLE_DIM,
    _STYLE_ERROR,
    _STYLE_FAIL,
    _STYLE_SUCCESS,
    _STYLE_WARN,
    _THINKING_HEADER,
    _ReasoningState,
    RenderCommand,
)
from ._utils import _cmd_name, _truncate_msg
from ..ui._cursor_tracker import CursorTracker
from rich.text import Text

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter
    from ._protocols import BottomBarProtocol
    from ._render_state import _RenderState


# ── 渲染命令分发表（O(1) 字典查找） ──
# 已废弃的命令值（保留位，不重用不处理）: {3, 4, 5, 10}
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
    # 值 18-20 已废弃 — 补全弹窗由 _CmplHandler 直接设置状态 + 请求 render 线程重绘
}


class ContentRenderer:
    """内容渲染器 — 执行 RenderCommand 并直接输出到终端。

    每个 _do_* 方法对应一种渲染命令，由 _render() 通过模块级 O(1)
    字典分发调用。所有方法在 render 线程中串行执行，无需额外同步。

    不再使用 Control 控件体系，每个方法直接通过 OutputAdapter
    或 sys.__stdout__ 输出终端内容。
    """

    def __init__(
        self,
        rs: "_RenderState",
        output_adapter: "OutputAdapter",
        bottom_bar: "BottomBarProtocol",
        on_display_messages: Callable[..., None] | None = None,
        cursor_tracker: CursorTracker | None = None,
    ):
        self._rs = rs
        self._bb = bottom_bar
        self._on_display_messages: Callable[..., None] | None = on_display_messages
        self._adapter = output_adapter
        self._tracker = cursor_tracker or CursorTracker()

    # ── 行数估计辅助 ──────────────────────────────────

    @staticmethod
    def _estimate_content_lines(text: str) -> int:
        """估计输出纯文本后光标前进的行数。

        ★ 近似估算：仅按 \\n 计数，未考虑 Rich Console 自动换行、
          IncrementalRenderer Markdown 渲染展开等额外行数。
          对于纯文本和简单消息，此估算足够准确。
          对于复杂 Markdown 内容（代码块、表格、自动折行），
          实际行数可能多于估算值。但 tracker 的误差在每个
          drain cycle 末尾被 ensure_cursor_upper/position_cursor
          修正，不影响功能正确性。

        Rich Console 的 console.print(text) 在尾部自动追加换行，
        因此至少产生 1 行。若 text 本身包含 \\n 则产生多行。

        Args:
            text: 输出文本（纯字符串）。

        Returns:
            估计的行数（至少 1）。
        """
        if not text:
            return 1
        return text.count('\n') + 1

    # ── 渲染分发 ──────────────────────────────────────

    def render(self, cmd: tuple) -> None:
        """根据命令类型分发到对应渲染方法（模块级 O(1) 字典查找）。"""
        cid = cmd[0]
        entry = _RENDER_DISPATCH.get(cid)
        if entry is None:
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
        # ★ 坐标追踪：推理头单独记录行数
        rr = self._rs.get_reasoning()
        if rr is not None:
            lines = 0
            if is_first:
                rr.write(_THINKING_HEADER)
                lines += self._estimate_content_lines(_THINKING_HEADER)
            rr.write(text)
            lines += self._estimate_content_lines(text)
            self._tracker.record_newlines(lines)

    def _do_content(self, text: str) -> None:
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)
        # ★ 坐标追踪：内容输出后更新光标位置
        self._tracker.record_newlines(self._estimate_content_lines(text))

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    # ── 工具渲染 ──────────────────────────────────────

    def _do_tool_count_inc(self) -> None:
        self._bb.increment_tool()

    def _do_tool_count_dec(self) -> None:
        self._bb.decrement_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    # ── 补全弹窗（已移除 via cmd 18~20，改为 _CmplHandler 直接设置状态 + 请求重绘） ──

    # ── 工具输出（直接通过 OutputAdapter 写入，不再使用 ToolOutputControl） ──

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出 — 直接格式化后通过 OutputAdapter 写入。

        处理 \r 覆盖输出和 ANSI 转义序列。
        超长文本截断（>10000 字符）。

        坐标追踪：
        - \r 覆盖输出（不含尾部 \r）：产生 1 行新行
        - \r 覆盖输出（以 \r 结尾）：原地覆写，不产生新行
        - 标准输出：text 的 \n 数 + 1 行
        """
        MAX_OUTPUT_LEN = 10000
        if len(text) > MAX_OUTPUT_LEN:
            text = text[:MAX_OUTPUT_LEN] + "...(truncated)"

        has_carriage = '\r' in text
        tracker = self._tracker

        if has_carriage:
            # \r 覆盖输出路径
            if '\033[' in text:
                clean_text = text.replace('\r', '')
                try:
                    self._adapter.write(Text.from_ansi(clean_text))
                except Exception:
                    self._adapter.write_raw(clean_text)
            else:
                self._adapter.write_raw(text.split('\r')[-1])
            if not text.endswith('\r'):
                self._adapter.write_raw('\n')
                # ★ 含 \r 的文本中可能还包含 \n（如进度条中间夹杂换行），
                #    使用 clean_text 估算实际行数，而非硬编码 1。
                clean = text.replace('\r', '') if '\033[' in text else text.split('\r')[-1]
                tracker.record_newlines(self._estimate_content_lines(clean))
            # else: \r 结尾，原地覆写，不产生新行
        else:
            # 标准输出（3 空格缩进 + dim 样式）
            self._adapter.write(
                Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM))
            )
            tracker.record_newlines(self._estimate_content_lines(text))

    # ── 工具汇总（直接通过 OutputAdapter 写入，不再使用 ToolSummaryControl） ──

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """渲染工具执行汇总 — 直接格式化输出。

        Args:
            successful: 成功工具列表
            failed: 失败工具列表（(name, error) 元组）
        """
        successful = successful or ()
        failed = failed or ()
        total = len(successful) + len(failed)
        tracker = self._tracker
        if failed:
            self._render_failure_summary(failed, total)
            # _render_failure_summary 内部已记录行数
        elif successful:
            self._adapter.write(Text.assemble(
                ("  · ", _STYLE_SUCCESS),
                (f"{len(successful)}工具完成", _STYLE_SUCCESS),
            ))
            tracker.record_newlines(1)

    def _render_failure_summary(self, failed: tuple, total: int) -> None:
        """渲染失败工具汇总（着色图标 + 彩色计数 + 详情列表）。"""
        safe_failed = []
        for item in failed:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                error = str(item[1]) if item[1] is not None else ""
                if len(item) > 2:
                    extras = ", ".join(str(x) for x in item[2:])
                    if error:
                        error += f" [{extras}]"
                    else:
                        error = f"[{extras}]"
                safe_failed.append((str(item[0]), error))
            else:
                safe_failed.append((str(item), ""))
        failed = tuple(safe_failed)

        tracker = self._tracker
        lines = 1  # 汇总标题行

        failed_names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            self._adapter.write(Text.assemble(
                ("  ! ", _STYLE_FAIL),
                (f"全部失败: {failed_names}", _STYLE_FAIL),
            ))
        else:
            self._adapter.write(Text.assemble(
                ("  ! ", _STYLE_WARN),
                (f"{len(failed)}/{total} 失败: {failed_names}", _STYLE_WARN),
            ))

        detail_lines = 0
        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    max_width = 80
                    s = short
                    w = 0
                    cut = len(s)
                    for i, ch in enumerate(s):
                        cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
                        if w + cw > max_width - 3:
                            cut = i
                            break
                        w += cw
                    if cut < len(s):
                        short = s[:cut] + "..."
            self._adapter.write(Text.assemble(
                (f"    {name}", _STYLE_DIM),
                (f"  {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
            detail_lines += 1
        if len(failed) > 3:
            self._adapter.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM),
            ))
            detail_lines += 1

        tracker.record_newlines(lines + detail_lines)

    # ── 解析进度（直接通过 sys.__stdout__ 写入，不再使用 ParseInfoControl） ──

    def _do_parse_info(
        self, tool_names: str, tokens: int | float, elapsed: float,
    ) -> None:
        """渲染解析进度 — 同行原地更新（\\r\\033[K 覆写，不产生新行）。

        坐标追踪：\r 覆写同行不产生新行；\n 清理时产生 1 行新行。
        """
        if tokens == _CLEAR_PARSE_LINE:
            sys.__stdout__.write("\n")
            sys.__stdout__.flush()
            self._tracker.record_newlines(1)
            return
        if isinstance(tokens, (int, float)):
            import math
            if math.isfinite(tokens):
                tokens_str = f"{tokens}t"
            else:
                tokens_str = "?"
        else:
            tokens_str = str(tokens)
        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        sys.__stdout__.write(output)
        sys.__stdout__.flush()
        # \r 覆写同行，不产生新行，因此不更新 tracker

    # ── 样式化行渲染 — 直接通过 OutputAdapter ──────

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（> 前缀 + 加粗）。"""
        self._adapter.write(
            Text.assemble(("\n  > ", _STYLE_BOLD), (text, _STYLE_BOLD))
        )
        # \n + text 行 = 至少 2 行（开头的 \n 产生 1 行，text 产生 1 行）
        self._tracker.record_newlines(self._estimate_content_lines(f"\n{text}"))

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（· 前缀 + 成功样式）。"""
        self._adapter.write(
            Text.assemble(("\n  · ", _STYLE_SUCCESS), (text, _STYLE_SUCCESS))
        )
        self._tracker.record_newlines(self._estimate_content_lines(f"\n{text}"))

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ! 样式）。

        超长消息（> 200 字符）自动截断并追加 ... 标记。
        """
        message = _truncate_msg(message, _MAX_ERROR_LENGTH)
        self._adapter.write(
            Text.assemble(("\n  ! ", _STYLE_ERROR), (message, _STYLE_ERROR))
        )
        self._tracker.record_newlines(self._estimate_content_lines(f"\n{message}"))

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行。

        含 ANSI 转义序列时走 Text.from_ansi 解析，
        否则直接写入并追加换行。
        """
        if '\033[' in text:
            try:
                self._adapter.write(Text.from_ansi(text))
            except Exception:
                self._adapter.write_raw(text + "\n")
        else:
            self._adapter.write_raw(text + "\n")
        self._tracker.record_newlines(self._estimate_content_lines(text))

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。"""
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
        # 消息列表的行数不易估算，保守记录至少 1 行
        self._tracker.record_newlines(1)

    # ── 底部栏刷新已迁移至 RenderEngine.request_bottom_redraw() ──
