"""chat_ui 渲染状态模块 — _RenderState 渲染器生命周期管理。

Layer 1 — 依赖 _const（_ReasoningState + _THINKING_SEPARATOR）。
不再使用 Control 体系，直接管理 IncrementalRenderer 实例。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ._const import (
    _ReasoningState,
    _THINKING_SEPARATOR,
)

if TYPE_CHECKING:
    from ..api.renderer import IncrementalRenderer


@dataclass
class _RenderState:
    """管理推理/内容渲染器的创建、切换与关闭。

    集中在单一对象中管理所有渲染器状态，替代原来散落在 ChatUIConsumer
    中的多个 __rr/__cr/double-underscore 属性和 property getter/setter。
    每个 ChatUIConsumer 实例持有一个 _RenderState 实例。

    推理状态通过 _ReasoningState 三值枚举管理（INACTIVE/ACTIVE/CLOSED），
    替代旧版两个布尔值（thinking_header_printed + reasoning_closed），
    消除 4 种布尔组合中部分组合的歧义。

    不再依赖 Control 体系——直接创建 IncrementalRenderer 实例。
    """

    # ── 渲染器实例（None=未创建或已关闭） ──
    reasoning: "IncrementalRenderer | None" = None
    content: "IncrementalRenderer | None" = None

    # ── 推理状态机 ──
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE

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
            from ..api.renderer import IncrementalRenderer
            self.reasoning = IncrementalRenderer(
                style="dim",
                _file=sys.__stdout__,
                typing_speed=1000,
                show_indicator=False,
            )
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        """获取内容渲染器，惰性创建。"""
        if self.content is None:
            from ..api.renderer import IncrementalRenderer
            self.content = IncrementalRenderer(
                style="",
                _file=sys.__stdout__,
                typing_speed=1000,
                show_indicator=False,
            )
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
        """关闭所有渲染器。

        单个渲染器关闭异常不阻塞其他渲染器（try/except 隔离）。
        """
        try:
            self.close_reasoning()
        except Exception:
            pass
        try:
            self.close_content()
        except Exception:
            pass
