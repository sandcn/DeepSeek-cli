"""chat_ui 渲染器模块 — 14 种渲染命令的执行逻辑。

Layer 2 — 依赖 _const（Style常量 + RenderCommand + _ReasoningState + _MAIN_LABEL）
          + _render_state（_RenderState）+ _controls（控件体系）。

上屏历史管理（ScreenHistoryManager）已屏蔽为 No-op，
所有相关调用已移除，减轻每帧方法调用开销。
"""

from __future__ import annotations

import sys
import logging
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
    _THINKING_HEADER,
    RenderCommand,
)

from ._utils import _cmd_name, _truncate_msg
from ._controls import (
    ControlList,
    MarkdownControl,
    ParseInfoControl,
    SubAgentPanelControl,
    TextControl,
    ToolOutputControl,
    ToolSummaryControl,
)

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
        R.REASONING:      ("_do_reasoning",       (1,)),
        R.CONTENT:        ("_do_content",         (1,)),
        R.PHASE_DONE:     ("_do_phase_done",      (1,)),
        R.TOOL_OUTPUT:    ("_do_tool_output",     (1,)),
        R.TOOL_SUMMARY:   ("_do_tool_summary",    (1, 2)),
        R.USER_MSG:       ("_do_user_message",    (1,)),
        R.PARSE_INFO:     ("_do_parse_info",      (1, 2, 3)),
        R.NOTIFICATION:   ("_do_notification",    (1,)),
        R.WRITE_LINE:     ("_do_write_line",      (1,)),
        R.DISPLAY_MSGS:   ("_do_display_messages", (1, 2)),
        R.TOOL_COUNT_INC: ("_do_tool_count_inc",  ()),
        R.TOOL_COUNT_DEC: ("_do_tool_count_dec",  ()),
        R.TOOL_FAIL_INC:  ("_do_tool_fail_inc",   ()),
        R.ERROR:          ("_do_error",           (1,)),
        R.SUBAGENT_REFRESH: ("_do_subagent_refresh", (1,)),
        R.BOTTOM_BAR_REFRESH: ("_do_bottom_bar_refresh", ()),
    }

    # 断言：确保没有废弃命令被误加到分发表中
    for cid in dispatch:
        assert cid not in _DEPRECATED_COMMANDS, (
            f"废弃的 RenderCommand 值 {cid} 被误加到 _RENDER_DISPATCH 中"
        )

    return dispatch


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
        output_adapter: "OutputAdapter",
        bottom_bar: "BottomBarProtocol",
        on_display_messages: Callable[..., None] | None = None,
    ):
        self._rs = rs
        self._bb = bottom_bar
        # ── display_messages 回调（由 ChatUIConsumer 注入） ──
        # 保持为实例属性，不受 ScreenHistoryManager 封装
        self._on_display_messages: Callable[..., None] | None = on_display_messages

        # ── OutputAdapter（由 ChatUIConsumer 构造注入） ──
        # 替代原来 ContentRenderer 内部 self._rs._tool_adapter 的注入模式。
        # ContentRenderer 不再负责创建 Console 和 OutputAdapter，
        # 关注点分离更清晰：ContentRenderer 只消费 adapter，不负责创建。
        self._adapter = output_adapter

        # ── 注册 MarkdownControl 工厂回调到 _RenderState ──
        # 替代原来 _RenderState._create_markdown_control() 静态方法，
        # 使 MarkdownControl 创建逻辑统一由 ContentRenderer 管理。
        self._rs.control_factory = self._create_markdown_control

        # ── ControlList 控件列表管理（通过工厂方法创建） ──
        # 通过回调注册解耦 _RenderState 对 ControlList 的直接依赖
        self._control_list = self._create_controls()
        self._rs.on_control_created = self._control_list.add
        self._rs.on_control_removed = self._control_list.remove

    # ── 控件工厂方法 ────────────────────────────────────

    def _create_markdown_control(self, style: str = "") -> "MarkdownControl":
        """创建 MarkdownControl 实例。

        作为 control_factory 注入到 _RenderState，
        替代原来 _RenderState._create_markdown_control() 静态方法，
        使 MarkdownControl 创建逻辑统一由 ContentRenderer 管理。
        """
        return MarkdownControl(
            style=style,
            typing_speed=1000,
            show_indicator=False,
        )

    # ── 公开方法 ─────────────────────────────────────

    def refresh_width(self) -> None:
        """刷新终端宽度缓存（公开方法）。

        委托 ControlList.refresh_width_all() 遍历所有活跃控件刷新宽度。
        推理/内容 MarkdownControl 已通过 on_control_created 回调加入
        ControlList，无需额外遍历。统一通过 ControlList 管理。
        """
        self._control_list.refresh_width_all()

    # ── 控件创建配置（由 _create_controls() 循环消费） ──
    # 每个条目标识一个控件实例，包含属性名、控件类和构造参数。
    # adapter 由 _create_controls() 在运行时注入，避免在类属性中硬编码。
    # 不同控件类的构造参数签名可差异——kwargs 直接解包传入构造函数。
    _CONTROL_CONFIG: list[dict] = [
        {"attr": "_user_msg_ctrl",      "cls": TextControl,          "kwargs": {"prefix": "\n  > ", "style": _STYLE_BOLD, "level": 0}},
        {"attr": "_notif_ctrl",         "cls": TextControl,          "kwargs": {"prefix": "\n  · ", "style": _STYLE_SUCCESS, "level": 0}},
        {"attr": "_error_ctrl",         "cls": TextControl,          "kwargs": {"prefix": "\n  ! ", "style": _STYLE_ERROR, "level": 0}},
        {"attr": "_line_ctrl",          "cls": TextControl,          "kwargs": {"level": 0}},
        {"attr": "_tool_output_ctrl",   "cls": ToolOutputControl,    "kwargs": {"dim_style": _STYLE_DIM, "level": 0}},
        {"attr": "_tool_summary_ctrl",  "cls": ToolSummaryControl,   "kwargs": {"style_success": _STYLE_SUCCESS, "style_fail": _STYLE_FAIL, "style_warn": _STYLE_WARN, "style_dim": _STYLE_DIM, "level": 0}},
        {"attr": "_parse_info_ctrl",    "cls": ParseInfoControl,     "kwargs": {"level": 0}},
    ]

    def _create_controls(self) -> ControlList:
        """创建并返回所有 Control 控件实例（工厂方法，配置驱动）。

        遍历 _CONTROL_CONFIG 配置列表，自动注入 adapter 到各控件，
        通过 setattr 注册到 self，并加入 ControlList。

        将控件创建与 __init__ 分离，使构造函数聚焦于依赖注入。
        控件创建逻辑可通过子类重写此方法或修改 _CONTROL_CONFIG 扩展。
        返回的 ControlList 包含所有已创建的控件。
        """
        adapter = self._adapter  # 由 ChatUIConsumer 构造注入
        control_list = ControlList()

        for entry in self._CONTROL_CONFIG:
            attr_name = entry["attr"]
            cls = entry["cls"]
            kwargs = dict(entry["kwargs"])
            # 注入 output_adapter（所有控件构造函数的第一参数均为 output_adapter）
            kwargs["output_adapter"] = adapter
            instance = cls(**kwargs)
            setattr(self, attr_name, instance)
            control_list.add(instance)

        return control_list

    # ── 工具输出控件重建工厂方法 ──────────────────────

    def _recreate_tool_output_control(self) -> None:
        """重建 _tool_output_ctrl（防御异常路径导致的控件意外关闭）。

        在 _do_tool_output() 检测到控件已关闭时调用。
        从 ControlList 移除旧引用，创建新控件并重新注册。
        提取为独立方法而非内联重建，与 _create_controls() 工厂方法风格一致。
        """
        self._control_list.remove(self._tool_output_ctrl)
        self._tool_output_ctrl = ToolOutputControl(
            self._adapter, dim_style=_STYLE_DIM, level=0,
        )
        self._control_list.add(self._tool_output_ctrl)

    # ── 渲染分发 ──────────────────────────────────────

    def render(self, cmd: tuple) -> None:
        """根据命令类型分发到对应渲染方法（模块级 O(1) 字典查找）。

        SubAgent 相关命令（工具输出/计数变更等）渲染完毕后，
        主动刷新 ParallelDisplay 面板以展示最新子代理状态。
        强制刷新（force=True）跳过版本号检查，确保面板
        在处理完渲染命令后始终保持最新。
        """
        cid = cmd[0]

        entry = _RENDER_DISPATCH.get(cid)
        if entry is None:
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return

        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

        # ★ SubAgent 相关命令处理完后，强制刷新 SubAgentPanelControl 面板
        #   命令集合（工具输出/计数变更/解析进度等）与子代理面板刷新相关。
        _SUBAGENT_RENDER_COMMANDS: frozenset[int] = frozenset({
            RenderCommand.TOOL_OUTPUT,
            RenderCommand.TOOL_COUNT_INC,
            RenderCommand.TOOL_COUNT_DEC,
            RenderCommand.TOOL_FAIL_INC,
            RenderCommand.TOOL_SUMMARY,
            RenderCommand.PARSE_INFO,
        })
        if cid in _SUBAGENT_RENDER_COMMANDS:
            self._do_subagent_refresh(True)

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

    # ── 工具输出：通过 ToolOutputControl 渲染 ──

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（通过 ToolOutputControl 控件）。

        超长截断和 \\r 处理由 ToolOutputControl 封装。
        控件关闭后自动重建——防御异常路径导致的控件意外关闭。
        """
        if self._tool_output_ctrl.is_closed:
            self._recreate_tool_output_control()
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

    # ── SubAgent 面板刷新 ────────────────────────────

    def _do_subagent_refresh(self, force: bool = False) -> None:
        """刷新 SubAgent 面板帧（通过 SubAgentPanelControl）。

        从 _state._active_subagent_panel 获取活跃面板控件并触发帧渲染。
        面板渲染通过 OutputAdapter.write_raw() 走 output_lock 路径，
        与 ChatUI 其他文本输出串行化。

        Args:
            force: 跳过版本号检查强制渲染（SubAgent 命令后使用）
        """
        try:
            from . import _state as _chat_ui_state
            panel = _chat_ui_state._active_subagent_panel
            if panel is not None:
                panel.render_frame(force=force)
        except Exception:
            _logger.debug(
                "_do_subagent_refresh: 面板刷新异常",
                exc_info=True,
            )

    # ── 底部栏刷新 ──────────────────────────────────

    def _do_bottom_bar_refresh(self) -> None:
        """在 Reader 线程中重绘底部栏（通过 force_redraw）。

        由 BOTTOM_BAR_REFRESH 命令触发，确保终端 I/O 在
        reader 线程中执行，避免在 EscapeMonitor 回调线程
        中直接写终端导致的竞态。
        """
        try:
            self._bb.force_redraw()
        except Exception:
            _logger.debug(
                "_do_bottom_bar_refresh: force_redraw 异常",
                exc_info=True,
            )

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
