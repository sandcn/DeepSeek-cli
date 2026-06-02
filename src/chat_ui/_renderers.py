"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）+ _controls（控件体系）。

上屏历史管理（ScreenHistoryManager）已屏蔽为 No-op，
所有相关调用已移除，减轻每帧方法调用开销。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

_logger = logging.getLogger(__name__)

from ._const import (
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
from ._controls import (
    ControlList,
    ParseInfoControl,
    TextControl,
    ToolOutputControl,
    ToolSummaryControl,
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

        # ── ControlList 控件列表管理 ──
        adapter = self._tool_adapter  # 惰性初始化 OutputAdapter
        self._control_list = ControlList()
        # ★ 注入到 _RenderState，使推理/内容 MarkdownControl 创建时自动注册
        self._rs._control_list = self._control_list

        # ── TextControl 实例（按前缀+样式分组，注册到 ControlList）──
        self._user_msg_ctrl = TextControl(adapter, prefix="\n  > ", style=_STYLE_BOLD, level=0)
        self._control_list.add(self._user_msg_ctrl)
        self._notif_ctrl = TextControl(adapter, prefix="\n  · ", style=_STYLE_SUCCESS, level=0)
        self._control_list.add(self._notif_ctrl)
        self._error_ctrl = TextControl(adapter, prefix="\n  ! ", style=_STYLE_ERROR, level=0)
        self._control_list.add(self._error_ctrl)
        self._line_ctrl = TextControl(adapter, level=0)
        self._control_list.add(self._line_ctrl)

        # ── 工具控件（注册到 ControlList）──
        self._tool_output_ctrl = ToolOutputControl(adapter, dim_style=_STYLE_DIM, level=0)
        self._control_list.add(self._tool_output_ctrl)
        self._tool_summary_ctrl = ToolSummaryControl(
            adapter,
            style_success=_STYLE_SUCCESS,
            style_fail=_STYLE_FAIL,
            style_warn=_STYLE_WARN,
            style_dim=_STYLE_DIM,
            level=0,
        )
        self._control_list.add(self._tool_summary_ctrl)
        self._parse_info_ctrl = ParseInfoControl(adapter, level=0)
        self._control_list.add(self._parse_info_ctrl)

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
            self._control_list.refresh_width_all()

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

    # ── 工具输出：通过 ToolOutputControl 渲染 ──

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（通过 ToolOutputControl 控件）。

        超长截断和 \\r 处理由 ToolOutputControl 封装。
        控件关闭后自动重建——防御异常路径导致的控件意外关闭。
        """
        if self._tool_output_ctrl.is_closed:
            self._tool_output_ctrl = ToolOutputControl(
                self._tool_adapter, dim_style=_STYLE_DIM, level=0,
            )
            self._control_list.add(self._tool_output_ctrl)
        self._tool_output_ctrl.write(text)

    # ── 工具汇总：通过 ToolSummaryControl 渲染 ──

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """渲染工具执行汇总（通过 ToolSummaryControl 控件，一次性渲染后关闭）。"""
        self._tool_summary_ctrl.summarize(successful, failed)
        self._tool_summary_ctrl.close()

    # ── 解析进度：通过 ParseInfoControl 渲染 ──

    def _do_parse_info(self, tool_names: str, tokens: int | float, elapsed: float) -> None:
        """渲染解析进度（通过 ParseInfoControl 控件，不再使用 \\r\\033[K 进度条）。"""
        self._parse_info_ctrl.update(tool_names, tokens, elapsed)

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
