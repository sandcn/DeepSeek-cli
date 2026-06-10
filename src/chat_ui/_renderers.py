"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）。
不再使用 Control 控件体系，每个 _do_* 方法直接通过 OutputAdapter 或 sys.__stdout__ 输出。
"""

from __future__ import annotations

import logging
import sys
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
from rich.text import Text

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter
    from ._protocols import BottomBarProtocol
    from ._render_state import _RenderState


def _build_render_dispatch() -> dict[int, tuple[str, tuple[int, ...]]]:
    """构建渲染命令分发表（模块级函数，类定义时即初始化）。

    从 _const.py 移入 _renderers.py，因其仅被 ContentRenderer 使用。
    显式排除已废弃的 RenderCommand 值（3-5 保留位 + 10 CMD_OUTPUT），
    防止未来误添加后出现静默吞没。
    """
    # 已废弃的命令值（保留位，不重用不处理）
    _DEPRECATED_COMMANDS = {3, 4, 5, 10}

    R = RenderCommand
    dispatch = {
        R.REASONING:       ("_do_reasoning",       (1,)),
        R.CONTENT:         ("_do_content",         (1,)),
        R.PHASE_DONE:      ("_do_phase_done",      (1,)),
        R.TOOL_OUTPUT:     ("_do_tool_output",     (1,)),
        R.TOOL_SUMMARY:    ("_do_tool_summary",    (1, 2)),
        R.USER_MSG:        ("_do_user_message",    (1,)),
        R.PARSE_INFO:      ("_do_parse_info",      (1, 2, 3)),
        R.NOTIFICATION:    ("_do_notification",    (1,)),
        R.WRITE_LINE:      ("_do_write_line",      (1,)),
        R.DISPLAY_MSGS:    ("_do_display_messages", (1, 2)),
        R.TOOL_COUNT_INC:  ("_do_tool_count_inc",  ()),
        R.TOOL_COUNT_DEC:  ("_do_tool_count_dec",  ()),
        R.TOOL_FAIL_INC:   ("_do_tool_fail_inc",   ()),
        R.ERROR:           ("_do_error",           (1,)),
        R.BOTTOM_BAR_REFRESH: ("_do_bottom_bar_refresh", ()),
    }

    # 断言：确保没有废弃命令被误加到分发表中
    for cid in dispatch:
        assert cid not in _DEPRECATED_COMMANDS, (
            f"废弃的 RenderCommand 值 {cid} 被误加到 _RENDER_DISPATCH 中"
        )

    return dispatch


# ── 模块级渲染命令分发表（类定义时即构建，O(1) 查找） ──
_RENDER_DISPATCH: dict[int, tuple[str, tuple[int, ...]]] = _build_render_dispatch()


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
    ):
        self._rs = rs
        self._bb = bottom_bar
        self._on_display_messages: Callable[..., None] | None = on_display_messages
        self._adapter = output_adapter

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
        rr = self._rs.get_reasoning()
        if rr is not None:
            if is_first:
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

    def _do_tool_count_dec(self) -> None:
        self._bb.decrement_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    # ── 工具输出（直接通过 OutputAdapter 写入，不再使用 ToolOutputControl） ──

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出 — 直接格式化后通过 OutputAdapter 写入。

        处理 \r 覆盖输出和 ANSI 转义序列。
        超长文本截断（>10000 字符）。
        """
        MAX_OUTPUT_LEN = 10000
        if len(text) > MAX_OUTPUT_LEN:
            text = text[:MAX_OUTPUT_LEN] + "...(truncated)"

        has_carriage = '\r' in text

        if has_carriage:
            # \r 覆盖输出路径
            if '\033[' in text:
                # 含 ANSI：移除 \r 后尝试 ANSI 解析
                clean_text = text.replace('\r', '')
                try:
                    self._adapter.write(Text.from_ansi(clean_text))
                except Exception:
                    self._adapter.write_raw(clean_text)
            else:
                # 纯 \r 文本：取最后一段
                self._adapter.write_raw(text.split('\r')[-1])
            if not text.endswith('\r'):
                self._adapter.write_raw('\n')
        else:
            # 标准输出（3 空格缩进 + dim 样式）
            self._adapter.write(
                Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM))
            )

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
        if failed:
            self._render_failure_summary(failed, total)
        elif successful:
            self._adapter.write(Text.assemble(
                ("  · ", _STYLE_SUCCESS),
                (f"{len(successful)}工具完成", _STYLE_SUCCESS),
            ))

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

        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    # 按视觉宽度截断
                    from wcwidth import wcswidth
                    max_width = 80
                    s = short
                    w = 0
                    cut = len(s)
                    for i, ch in enumerate(s):
                        cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
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
        if len(failed) > 3:
            self._adapter.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM),
            ))

    # ── 解析进度（直接通过 sys.__stdout__ 写入，不再使用 ParseInfoControl） ──

    def _do_parse_info(
        self, tool_names: str, tokens: int | float, elapsed: float,
    ) -> None:
        """渲染解析进度 — 同行原地更新（\\r\\033[K 覆写，不产生新行）。"""
        if tokens == _CLEAR_PARSE_LINE:
            sys.__stdout__.write("\n")
            sys.__stdout__.flush()
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

    # ── 样式化行渲染 — 直接通过 OutputAdapter ──────

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（> 前缀 + 加粗）。"""
        self._adapter.write(
            Text.assemble(("\n  > ", _STYLE_BOLD), (text, _STYLE_BOLD))
        )

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（· 前缀 + 成功样式）。"""
        self._adapter.write(
            Text.assemble(("\n  · ", _STYLE_SUCCESS), (text, _STYLE_SUCCESS))
        )

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ! 样式）。

        超长消息（> 200 字符）自动截断并追加 ... 标记。
        """
        message = _truncate_msg(message, _MAX_ERROR_LENGTH)
        self._adapter.write(
            Text.assemble(("\n  ! ", _STYLE_ERROR), (message, _STYLE_ERROR))
        )

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

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。"""
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)

    # ── 底部栏刷新 ──────────────────────────────────

    def _do_bottom_bar_refresh(self) -> None:
        """占位命令处理 — 实际重绘由 _phase_redraw_bottom() 完成。"""
