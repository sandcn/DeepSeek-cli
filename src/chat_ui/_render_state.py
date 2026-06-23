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
    from ..api.renderer import IncrementalRenderer
    from ..api.renderer.output import OutputAdapter

from ._const import _THINKING_SEPARATOR

_logger = logging.getLogger(__name__)


class _ReasoningState(Enum):
    """推理渲染器状态机，替代两个布尔值（thinking_header_printed + reasoning_closed）。

    状态转换：
      INACTIVE → 首个推理块到达 → ACTIVE（创建渲染器+打印标题）
      ACTIVE   → close_reasoning() → CLOSED（写入分隔线+关闭渲染器）
      INACTIVE → close_reasoning() → CLOSED（推理块从未到达即关闭）
      CLOSED   → reopen_reasoning() → INACTIVE（二次推理重新打开）
      CLOSED   → 其他转换不生效（幂等）
    """
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


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
            from ..api.renderer import IncrementalRenderer  # 保留运行时惰性 import（避免循环）
            self.reasoning = IncrementalRenderer(
                style="dim", _file=sys.__stdout__,
                typing_speed=1000, show_indicator=False,
                output_adapter=self._shared_adapter,
            )
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        if self.content is None:
            if self._shared_adapter is None:
                _logger.warning("get_content: _shared_adapter 未设置")
            from ..api.renderer import IncrementalRenderer  # 保留运行时惰性 import（避免循环）
            self.content = IncrementalRenderer(
                style="", _file=sys.__stdout__,
                typing_speed=1000, show_indicator=False,
                output_adapter=self._shared_adapter,
            )
        return self.content

    def close_reasoning(self) -> None:
        if self.reasoning_state == _ReasoningState.CLOSED:
            return
        rr = self.reasoning
        if rr is not None:
            rr.write(_THINKING_SEPARATOR)
            rr.close()
            self.reasoning = None
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        if self.reasoning_state != _ReasoningState.CLOSED:
            return
        self.reasoning = None
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        cr = self.content
        if cr is not None:
            cr.close()
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
