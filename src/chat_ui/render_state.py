"""渲染状态管理 — _RenderState + _ReasoningState。

Layer 0 — 被 Layer 1 (_components) 和 Layer 2 (_renderer) 平等引用，
消除 _components 依赖 _renderer 的分层违规。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..renderer import IncrementalRenderer
    from ..renderer.output import OutputAdapter

from .const import _THINKING_SEPARATOR

_logger = logging.getLogger(__name__)


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


@dataclass
class _RenderState:
    """推理/内容 IncrementalRenderer 生命周期管理。"""
    reasoning: "IncrementalRenderer | None" = None
    content: "IncrementalRenderer | None" = None
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE
    _shared_adapter: "OutputAdapter | None" = None

    def set_output_adapter(self, adapter: "OutputAdapter") -> None:
        self._shared_adapter = adapter

    def get_reasoning(self) -> "IncrementalRenderer | None":
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning is None:
            assert self.reasoning_state.can_transition_to(_ReasoningState.ACTIVE), (
                f"非法状态转换: {self.reasoning_state} -> ACTIVE"
            )
            from ..renderer import IncrementalRenderer  # 保留运行时惰性 import（避免循环）
            self.reasoning = IncrementalRenderer(
                style="dim", _file=sys.__stdout__,
                typing_speed=1000, show_indicator=False,
            )
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        if self.content is None:
            if self._shared_adapter is None:
                _logger.warning("get_content: _shared_adapter 未设置")
            from ..renderer import IncrementalRenderer  # 保留运行时惰性 import（避免循环）
            self.content = IncrementalRenderer(
                style="", _file=sys.__stdout__,
                typing_speed=1000, show_indicator=False,
                output_adapter=self._shared_adapter,
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
            rr.write(_THINKING_SEPARATOR)
            rr.close()
            # ★ 防御性刷出：确保 close() 中即时渲染的 flush token
            #   已物理写入 stdout（即使 close() 内已有 flush，此处
            #   作为兜底保障，尤其适用于共享 OutputAdapter 的场景）
            try:
                rr._output.flush()
            except Exception:
                _logger.debug("close_reasoning: flush 异常", exc_info=True)
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
            # ★ 防御性刷出：确保 close() 中即时渲染的 flush token
            #   已物理写入 stdout（即使 close() 内已有 flush，此处
            #   作为兜底保障，尤其适用于共享 OutputAdapter 的场景）
            try:
                cr._output.flush()
            except Exception:
                _logger.debug("close_content: flush 异常", exc_info=True)
            self.content = None

    def close_all(self) -> None:
        try:
            self.close_reasoning()
        except Exception:
            _logger.debug("close_reasoning 异常", exc_info=True)
        try:
            self.close_content()
        except Exception:
            _logger.debug("close_content 异常", exc_info=True)
