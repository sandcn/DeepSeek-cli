"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）。

上屏历史管理（ScreenHistoryManager）已屏蔽为 No-op，
所有相关调用已移除，减轻每帧方法调用开销。
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Callable

_logger = logging.getLogger(__name__)

from rich.text import Text
from wcwidth import wcswidth

from ._const import (
    _CLEAR_PARSE_LINE,
    _MAX_ERROR_LENGTH,
    _ReasoningState,
    _STYLE_BOLD,
    _STYLE_DIM,
    _STYLE_ERROR,
    _STYLE_FAIL,
    _STYLE_SUCCESS,
    _STYLE_WARN,
    _build_render_dispatch,
    _cmd_name,
    _truncate_msg,
)
from ._controls import TextControl

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

    # ── 最小 resize 检查间隔（秒），避免高频 shutil.get_terminal_size() 调用 ──
    _RESIZE_CHECK_INTERVAL: float = 0.2

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

        # ── 终端大小变化检测 ──
        # _last_width_check: 上次调用 shutil.get_terminal_size() 的时间戳
        # _cached_term_size: 上次缓存的终端 (columns, lines) 元组
        self._last_width_check: float = 0.0
        self._cached_term_size: tuple[int, int] = (0, 0)

        # ── TextControl 实例（按前缀+样式分组，替代 _render_styled_line / _write_text_or_ansi） ──
        adapter = self._tool_adapter  # 惰性初始化 OutputAdapter
        self._user_msg_ctrl = TextControl(adapter, prefix="\n  > ", style=_STYLE_BOLD)
        self._notif_ctrl = TextControl(adapter, prefix="\n  · ", style=_STYLE_SUCCESS)
        self._error_ctrl = TextControl(adapter, prefix="\n  ! ", style=_STYLE_ERROR)
        self._line_ctrl = TextControl(adapter)  # 无前缀、无样式，用于通用文本行

    @property
    def _tool_adapter(self) -> "OutputAdapter":
        return self._rs.get_tool_adapter()

    # ── 渲染分发 ──────────────────────────────────────

    def _check_and_refresh_width(self) -> None:
        """检测终端大小是否变化，变化时强制刷新所有适配器宽度缓存。

        200ms 最小检查间隔避免每次调用都执行系统调用。
        尺寸未变时零副作用（跳过所有刷新操作）。
        该方法由 RenderEngine._drain_queue() 在 output_lock 之外调用，
        避免 shutil.get_terminal_size() 系统调用持锁阻塞渲染管线。
        """
        now = time.monotonic()
        if now - self._last_width_check < self._RESIZE_CHECK_INTERVAL:
            return
        self._last_width_check = now

        try:
            import shutil
            current = shutil.get_terminal_size()
            new_size = (current.columns, current.lines)
        except OSError:
            return

        if new_size != self._cached_term_size:
            self._cached_term_size = new_size
            self._rs.force_refresh_width()

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

    def _do_tool_count_dec(self) -> None:
        self._bb.decrement_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    # ── 工具输出保护常量 ──
    _MAX_TOOL_OUTPUT_LEN = 10000  # 超过此长度的工具输出被截断

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（dim 样式 + 缩进）。

        超长文本（> 10000 字符）自动截断并追加 ...(truncated) 标记。
        """
        if len(text) > self._MAX_TOOL_OUTPUT_LEN:
            text = text[:self._MAX_TOOL_OUTPUT_LEN] + "...(truncated)"

        ta = self._tool_adapter

        # ── 无 \r：标准输出 ─────────────────────────────────
        if '\r' not in text:
            if self._rs.last_was_carriage:
                ta.write_raw("\n")
                self._rs.last_was_carriage = False
            ta.write(Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))
            return

        # ── 含 \r：进度条覆盖输出 ────────────────────────────
        if '\033[' in text:
            # 含 ANSI 转义序列 → 移除 \r 后用 Text.from_ansi() 解析
            self._do_tool_output_with_ansi(ta, text)
        else:
            # 纯 \r 文本 → 按 \r 分割取最后一段（中间段被覆盖，无意义）
            ta.write_raw(text.split('\r')[-1])

        # ── \r 结尾标记 ──────────────────────────────────────
        if text.endswith('\r'):
            self._rs.last_was_carriage = True
        else:
            ta.write_raw('\n')
            self._rs.last_was_carriage = False

    def _do_tool_output_with_ansi(
        self, ta, text: str,
    ) -> None:
        """处理含 ANSI 转义序列的工具输出（移除 \r 后解析渲染）。

        整个路径在 try/except 保护中——Text.from_ansi 解析失败或
        write_raw 回退失败都不抛出，日志记录后静默跳过。
        """
        clean_text = text.replace('\r', '')
        try:
            try:
                ta.write(Text.from_ansi(clean_text))
            except Exception:
                _logger.warning(
                    "_do_tool_output: Text.from_ansi 解析失败",
                    exc_info=True,
                )
                ta.write_raw(clean_text)
        except Exception:
            _logger.warning(
                "_do_tool_output: ANSI 渲染路径异常（含回退写入）",
                exc_info=True,
            )

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """渲染工具执行汇总（着色图标 + 彩色计数）。

        None 保护：参数为 None 时视为空元组，避免 TypeError。
        """
        if successful is None or failed is None:
            _logger.debug(
                "_do_tool_summary 收到 None 参数: successful=%s, failed=%s",
                successful, failed,
            )
        successful = successful or ()
        failed = failed or ()

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
        # ★ 解包保护：若元素非 (name, error) 格式，整体转为字符串显示
        safe_failed = []
        for item in failed:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                error = str(item[1]) if item[1] is not None else ""
                # 若元素含 3+ 元素，将额外信息追加到 error 字符串
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

    def _do_parse_info(self, tool_names: str, tokens: int | float, elapsed: float) -> None:
        if tokens == _CLEAR_PARSE_LINE:
            self._tool_adapter.write_raw("\n")
            return
        # ★ 类型保护：tokens 非 (int, float) 时显示原始字符串
        if isinstance(tokens, (int, float)):
            if math.isfinite(tokens):
                tokens_str = f"{tokens}t"
            else:
                tokens_str = "?"
        else:
            tokens_str = str(tokens)
        self._tool_adapter.write_raw(
            f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s",
        )

    def _do_cmd_output(self, text: str) -> None:
        """渲染 / 命令执行输出，通过 TextControl 写入。"""
        self._write_line_via_ctrl(text)

    # ── 样式化行渲染 — 通过 TextControl 实例委托 ──────

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（> 前缀 + 加粗）。"""
        self._user_msg_ctrl.write(text)

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（· 前缀）。"""
        self._notif_ctrl.write(text)

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ! 样式）。

        超长消息（> 200 字符）自动截断并追加 ... 标记。
        """
        message = _truncate_msg(message, _MAX_ERROR_LENGTH)
        self._error_ctrl.write(message)

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行，通过 TextControl 写入。"""
        self._write_line_via_ctrl(text)

    def _write_line_via_ctrl(self, text: str) -> None:
        """通过 _line_ctrl 写入文本行：ANSI 文本走 write_ansi，否则走 write_raw。"""
        if '\033[' in text:
            self._line_ctrl.write_ansi(text)
        else:
            self._line_ctrl.write_raw(text + "\n")

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。

        通过 self._on_display_messages 回调调用（由 ChatUIConsumer 注入），
        消除对 tui._message_display 的直接 import 依赖。
        """
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
