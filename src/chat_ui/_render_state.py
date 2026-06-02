"""chat_ui 渲染状态模块 — _RenderState 渲染器生命周期管理。

Layer 1 — 依赖 _const（_ReasoningState + _THINKING_SEPARATOR）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._const import (
    _ReasoningState,
    _THINKING_SEPARATOR,
)

if TYPE_CHECKING:
    from ._controls import Control, MarkdownControl
    from ._protocols import ControlFactory, ControlLifecycleHook


@dataclass
class _RenderState:
    """管理推理/内容渲染器的创建、切换与关闭。

    集中在单一对象中管理所有渲染器状态，替代原来散落在 ChatUIConsumer
    中的多个 __rr/__cr/double-underscore 属性和 property getter/setter。
    每个 ChatUIConsumer 实例持有一个 _RenderState 实例。

    推理状态通过 _ReasoningState 三值枚举管理（INACTIVE/ACTIVE/CLOSED），
    替代旧版两个布尔值（thinking_header_printed + reasoning_closed），
    消除 4 种布尔组合中部分组合的歧义。
    """

    # ── 渲染器实例（None=未创建或已关闭） ──
    reasoning: "MarkdownControl | None" = None
    content: "MarkdownControl | None" = None

    # ── 推理状态机 ──
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE

    # ── 控件工厂回调（由 ContentRenderer 注册，解耦双向依赖）──
    # get_reasoning()/get_content() 通过此回调创建 MarkdownControl，
    # 替代原来的 _create_markdown_control() 静态方法，
    # 使控件创建逻辑统一由 ContentRenderer 管理。
    # 使用 ControlFactory 协议类型，提供比 Callable 更严格的类型约束。
    control_factory: "ControlFactory | None" = None

    # ── 控件生命周期回调（由 ContentRenderer 注册，解耦双向依赖）──
    # get_reasoning()/get_content() 创建控件后调用 on_control_created，
    # close_reasoning()/close_content() 关闭控件后调用 on_control_removed。
    # 使用 ControlLifecycleHook 协议类型。
    on_control_created: "ControlLifecycleHook | None" = None
    on_control_removed: "ControlLifecycleHook | None" = None

    def get_reasoning(self) -> "MarkdownControl | None":
        """获取推理渲染器，通过 control_factory 惰性创建并注册。

        状态机驱动：
        - INACTIVE → 通过 control_factory 创建渲染器 + 切换到 ACTIVE
        - ACTIVE   → 直接返回已有渲染器
        - CLOSED   → 返回 None（防止惰性重建）

        Raises:
            RuntimeError: 若 control_factory 未注册（ContentRenderer 未初始化）
        """
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning is None:
            if self.control_factory is None:
                raise RuntimeError(
                    "_RenderState.control_factory 未注册。"
                    "请确保 ContentRenderer 已初始化并设置了 control_factory。"
                )
            self.reasoning = self.control_factory(style="dim")
            self.reasoning_state = _ReasoningState.ACTIVE
            if self.on_control_created is not None:
                self.on_control_created(self.reasoning)
        return self.reasoning

    def get_content(self) -> "MarkdownControl":
        """获取内容渲染器，通过 control_factory 惰性创建并注册。"""
        if self.content is None:
            if self.control_factory is None:
                raise RuntimeError(
                    "_RenderState.control_factory 未注册。"
                    "请确保 ContentRenderer 已初始化并设置了 control_factory。"
                )
            self.content = self.control_factory(style="")
            if self.on_control_created is not None:
                self.on_control_created(self.content)
        return self.content

    def close_reasoning(self) -> None:
        """关闭推理渲染器（写入分隔线后关闭）。幂等。"""
        if self.reasoning_state == _ReasoningState.CLOSED:
            return
        rr = self.reasoning
        if rr is not None:
            rr.write(_THINKING_SEPARATOR)
            rr.close()
            if self.on_control_removed is not None:
                self.on_control_removed(rr)
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
        # ★ 移除旧控件引用（已在 close_reasoning 中从 ControlList 移除）
        self.reasoning = None
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        """关闭内容渲染器并从 ControlList 移除。"""
        cr = self.content
        if cr is not None:
            cr.close()
            if self.on_control_removed is not None:
                self.on_control_removed(cr)
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
