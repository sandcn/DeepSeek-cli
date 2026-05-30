"""_RenderState — 管理推理/内容渲染器的创建、切换与关闭。

集中在单一对象中管理所有渲染器状态，每个 ChatUIConsumer 实例
持有一个 _RenderState 实例。

纯状态管理，不持有任何 ChatUIConsumer 引用。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ._const import (
    _ReasoningState,
    _THINKING_SEPARATOR,
)


@dataclass
class _RenderState:
    """管理推理/内容渲染器的创建、切换与关闭。

    推理状态通过 _ReasoningState 三值枚举管理（INACTIVE/ACTIVE/CLOSED），
    替代旧版两个布尔值（thinking_header_printed + reasoning_closed），
    消除 4 种布尔组合中部分组合的歧义。
    """

    # ── 渲染器实例（None=未创建或已关闭） ──
    reasoning: "IncrementalRenderer | None" = None
    content: "IncrementalRenderer | None" = None

    # ── 推理状态机 ──
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE
    last_was_carriage: bool = False    # 上一行以 \r 结尾（进度条行内覆盖）

    # ── 工具输出适配器（延时初始化） ──
    _tool_adapter: "OutputAdapter | None" = None

    @staticmethod
    def _create_renderer(style: str = "") -> "IncrementalRenderer":
        """创建 IncrementalRenderer 实例。

        IncrementalRenderer 内部自行管理 Console 实例（走独立
        OutputAdapter + 全局 output_lock），与 _tool_adapter 各用
        各的 Console——两个渲染管线（流式 Markdown/工具输出）的
        宽度缓存独立刷新（5s TTL），写入串行化由 output_lock 保证。
        """
        from ..api.renderer import IncrementalRenderer
        return IncrementalRenderer(
            style=style,
            _file=sys.__stdout__,
            typing_speed=1000,
            show_indicator=False,
        )

    def get_tool_adapter(self) -> "OutputAdapter":
        """获取或惰性创建工具输出适配器。"""
        if self._tool_adapter is None:
            from rich.console import Console
            from ..terminal import get_safe_console_config
            console = Console(**get_safe_console_config(), file=sys.__stdout__)
            from ..api.renderer.output import OutputAdapter
            self._tool_adapter = OutputAdapter(console)
        return self._tool_adapter

    def get_reasoning(self) -> "IncrementalRenderer | None":
        """获取推理渲染器，惰性创建。

        状态机驱动：
        - INACTIVE → 创建渲染器 + 切换到 ACTIVE
        - ACTIVE   → 直接返回已有渲染器
        - CLOSED   → 返回 None（防止惰性重建）
        """
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning is None:
            self.reasoning = self._create_renderer(style="dim")
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        """获取内容渲染器，惰性创建。"""
        if self.content is None:
            self.content = self._create_renderer()
        return self.content

    def close_reasoning(self) -> None:
        """关闭推理渲染器（写入分隔线后关闭）。幂等。"""
        if self.reasoning_state == _ReasoningState.CLOSED:
            return
        rr = self.reasoning
        if rr is not None:
            rr.write(_THINKING_SEPARATOR)
            rr.close()
            self.reasoning = None
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        """重新打开推理渲染器，用于工具调用后的二次推理。

        将 CLOSED 状态重置为 INACTIVE，清除旧的渲染器引用，
        让后续推理内容重新走"创建渲染器 → 写标题 → 写内容"流程。
        幂等——已在 ACTIVE/INACTIVE 状态时无操作。
        """
        if self.reasoning_state != _ReasoningState.CLOSED:
            return
        self.reasoning = None
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        """关闭内容渲染器。"""
        cr = self.content
        if cr is not None:
            cr.close()
            self.content = None

    def close_all(self) -> None:
        """关闭所有渲染器。"""
        self.close_reasoning()
        self.close_content()
