"""渲染状态管理 — RenderState 基类 + ChatRenderState + _ReasoningState。

Layer 0 — 被 Layer 1 (_components) 和 Layer 2 (_renderer) 平等引用，
消除 _components 依赖 _renderer 的分层违规。

架构分层（2026-07-22 泛化）：
  RenderState          — 框架通用基类：output_adapter 管理 + _safe_flush 工具方法
  ChatRenderState      — 聊天域子类：reasoning/content 双通道 + _ReasoningState 状态机
  _RenderState         — 向后兼容别名 → ChatRenderState

动效（2026-07-12）：
  - close_reasoning() 分隔线：宽屏时使用 make_sep_gradient_enhanced 叠加 wave 波动效果
  - 窄屏时降级为静态 make_sep_gradient 渐变分隔线
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ...renderer import IncrementalRenderer
    from ...renderer.output import OutputAdapter

from ..animation.animator import AnimatorContext
from ..terminal.terminal import is_narrow

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# IRenderState — 渲染状态接口协议
# ═══════════════════════════════════════════════════════════

@runtime_checkable
class IRenderState(Protocol):
    """渲染状态接口协议。

    定义推理/内容渲染器生命周期管理的抽象契约。
    ChatRenderState（及其别名 _RenderState）满足此协议。
    """

    reasoning_state: "_ReasoningState"

    def set_output_adapter(self, adapter: "OutputAdapter") -> None: ...
    def get_reasoning(self) -> "IncrementalRenderer | None": ...
    def get_content(self) -> "IncrementalRenderer | None": ...
    def close_reasoning(self) -> None: ...
    def reopen_reasoning(self) -> None: ...
    def close_content(self) -> None: ...
    def close_all(self) -> None: ...


class _ReasoningState(Enum):
    """推理渲染器状态机，替代两个布尔值（thinking_header_printed + reasoning_closed）。

    状态转换：
      INACTIVE → 首个推理块到达 → ACTIVE（创建渲染器+打印标题）
      ACTIVE   → close_reasoning() → CLOSED（写入分隔线+关闭渲染器）
      INACTIVE → close_reasoning() → CLOSED（推理块从未到达即关闭）
      CLOSED   → reopen_reasoning() → INACTIVE（二次推理重新打开）
      CLOSED   → 其他转换不生效（幂等）

    合法转换（集中定义在 _TRANSITIONS）：
      (INACTIVE, ACTIVE), (ACTIVE, CLOSED), (INACTIVE, CLOSED), (CLOSED, INACTIVE)
    """
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"

    def can_transition_to(self, target: "_ReasoningState") -> bool:
        """检查从当前状态到目标状态的转换是否合法。

        查询 _REASONING_STATE_TRANSITIONS 字典，未明确定义的转换返回 False。
        """
        return _REASONING_STATE_TRANSITIONS.get((self, target), False)


# 合法状态转换表 — 集中验证，消除分散的条件判断
# Python Enum 会将其它非 dunder 名称转为枚举成员，故定义为模块级常量
_REASONING_STATE_TRANSITIONS: dict[tuple[_ReasoningState, _ReasoningState], bool] = {
    (_ReasoningState.INACTIVE, _ReasoningState.ACTIVE): True,
    (_ReasoningState.ACTIVE, _ReasoningState.CLOSED): True,
    (_ReasoningState.INACTIVE, _ReasoningState.CLOSED): True,
    (_ReasoningState.CLOSED, _ReasoningState.INACTIVE): True,
}


# ═══════════════════════════════════════════════════════════
# RenderState — 框架通用渲染状态基类
# ═══════════════════════════════════════════════════════════

class RenderState:
    """框架通用渲染状态基类。

    提供 output_adapter 管理、_safe_flush 工具方法和 close_all 抽象接口。
    应用层通过子类化添加领域特定的渲染器生命周期管理。

    子类必须实现：
      - close_all(): 关闭所有活跃的渲染器，释放资源
    """

    def __init__(self) -> None:
        self._shared_adapter: "OutputAdapter | None" = None

    def set_output_adapter(self, adapter: "OutputAdapter") -> None:
        """设置共享的 OutputAdapter 实例。

        供子类的渲染器创建方法使用，确保所有渲染器共用同一输出适配器。
        """
        self._shared_adapter = adapter

    def close_all(self) -> None:
        """关闭所有活跃的渲染器。

        子类必须实现此方法，关闭该领域特有的渲染器实例。
        基类默认抛出 NotImplementedError。
        """
        raise NotImplementedError("子类必须实现 close_all()")

    def _safe_flush(self, renderer_attr: str) -> None:
        """防御性刷出渲染器缓冲内容。

        通用工具方法：通过属性名获取渲染器实例，安全调用其 flush 方法。
        渲染器不存在或 flush 异常时静默处理。

        Args:
            renderer_attr: 渲染器在子类实例上的属性名（如 "reasoning"、"content"）。
        """
        rr = getattr(self, renderer_attr, None)
        if rr is not None:
            try:
                rr._output.flush()
            except Exception:
                _logger.debug("%s 防御性 flush 异常", renderer_attr, exc_info=True)


# ═══════════════════════════════════════════════════════════
# ChatRenderState — 聊天域渲染状态（reasoning/content 双通道）
# ═══════════════════════════════════════════════════════════

@dataclass
class ChatRenderState(RenderState):
    """推理/内容 IncrementalRenderer 生命周期管理。

    继承自 RenderState，添加聊天域特有的 reasoning/content 双通道管理和
    _ReasoningState 状态机。

    captured_reasoning_output（v2026-07-24）：
      存储推理 IncrementalRenderer 渲染后的 ANSI 输出，供 ThinkingBlock.render()
      写入 RenderBuffer。每个 write() 调用追加一条渲染后的文本块。

    captured_content_output（v2026-07-24）：
      存储回答 IncrementalRenderer 渲染后的 ANSI 输出，供 AnswerBlock.render()
      写入 RenderBuffer。
    """
    reasoning: "IncrementalRenderer | None" = None
    content: "IncrementalRenderer | None" = None
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE
    captured_reasoning_output: list[str] = None  # type: ignore[assignment]
    captured_content_output: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """初始化基类的 _shared_adapter 属性和捕获列表。"""
        super().__init__()
        if self.captured_reasoning_output is None:
            self.captured_reasoning_output = []
        if self.captured_content_output is None:
            self.captured_content_output = []

    def get_reasoning(self) -> "IncrementalRenderer | None":
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning is None:
            assert self.reasoning_state.can_transition_to(_ReasoningState.ACTIVE), (
                f"非法状态转换: {self.reasoning_state} -> ACTIVE"
            )
            from ...renderer import IncrementalRenderer  # 保留运行时惰性 import（避免循环）
            self.reasoning = IncrementalRenderer(
                style="dim", _file=sys.__stdout__,
                show_indicator=False,
                captured_output=self.captured_reasoning_output,
            )
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        if self.content is None:
            if self._shared_adapter is None:
                _logger.warning("get_content: _shared_adapter 未设置")
            from ...renderer import IncrementalRenderer  # 保留运行时惰性 import（避免循环）
            self.content = IncrementalRenderer(
                style="", _file=sys.__stdout__,
                show_indicator=False,
                output_adapter=self._shared_adapter,
                captured_output=self.captured_content_output,
            )
        return self.content

    def close_reasoning(self) -> None:
        if self.reasoning_state == _ReasoningState.CLOSED:
            return
        assert self.reasoning_state.can_transition_to(_ReasoningState.CLOSED), (
            f"非法状态转换: {self.reasoning_state} -> CLOSED"
        )
        rr = self.reasoning
        if rr is not None:
            # 动态呼吸分隔线（使用 Separator 组件替代手写）
            # 运行时惰性 import 避免循环依赖：render_state → components → ... → render_state
            from ..components._separator import Separator
            _bf = AnimatorContext.get_default().breath_frame
            _style = "wave" if not is_narrow() else "static"
            _sep_renderer = Separator(style=_style, width=None, start_color=45, end_color=237, frame=_bf)
            _sep = _sep_renderer.render()
            rr.write(f"\n  {_sep}")
            rr.close()
            self._safe_flush("reasoning")
            self.reasoning = None
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        if self.reasoning_state != _ReasoningState.CLOSED:
            return
        assert self.reasoning_state.can_transition_to(_ReasoningState.INACTIVE), (
            f"非法状态转换: {self.reasoning_state} -> INACTIVE"
        )
        self.reasoning = None
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        cr = self.content
        if cr is not None:
            cr.close()
            self._safe_flush("content")
            self.content = None

    def close_all(self) -> None:
        """关闭所有活跃的渲染器（实现基类抽象方法）。

        按顺序关闭 reasoning → content，异常时静默继续。
        """
        try:
            self.close_reasoning()
        except Exception:
            _logger.debug("close_reasoning 异常", exc_info=True)
        try:
            self.close_content()
        except Exception:
            _logger.debug("close_content 异常", exc_info=True)


# ═══════════════════════════════════════════════════════════
# 向后兼容别名
# ═══════════════════════════════════════════════════════════

# @deprecated — 使用 ChatRenderState 替代，_RenderState 保留为向后兼容别名
_RenderState = ChatRenderState
