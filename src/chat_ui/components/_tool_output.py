"""工具执行输出块 — ToolOutputBlock。

处理工具执行的标准输出/错误，支持 \\r 回车叠加和 ANSI 转义序列。

动效（2026-07-12）：
  - 宽屏：左侧添加极淡青色呼吸边框字符 │（使用 build_glow_ansi 微呼吸，色号 23↔24）
  - 窄屏：降级为无左边缘的纯文本（与原始行为一致）
  - \\r 分支（实时工具输出流）不受动效影响，保持原始行为
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..renderer.output import OutputAdapter

from rich.text import Text

from ..const import _STYLE_DIM, _MAX_OUTPUT_LEN
from ...ui.tui._animator import AnimatorContext
from ...ui.tui._terminal import is_narrow
from ...ui.tui._text_utils import build_left_border_ansi
from ...ui.colors import DARK_GRAY_256
from ._base import TuiComponent, _estimate_content_lines

_logger = logging.getLogger(__name__)


class ToolOutputBlock(TuiComponent):
    """工具执行输出块。"""
    def __init__(self, text: str):
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        text = self.text
        if len(text) > _MAX_OUTPUT_LEN:
            text = text[:_MAX_OUTPUT_LEN] + "...(truncated)"
        has_carriage = '\r' in text
        if has_carriage:
            if '\033[' in text:
                clean = text.replace('\r', '')
                try:
                    adapter.write(Text.from_ansi(clean))
                except Exception:
                    _logger.debug("tool_output ANSI 解析失败, 回退 raw 输出", exc_info=True)
                    adapter.write_raw(clean)
            else:
                clean = text.split('\r')[-1]
                adapter.write_raw(clean)
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                return _estimate_content_lines(clean)
            return 0
        else:
            frame = AnimatorContext.get_default().frame
            if is_narrow():
                adapter.write(Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))
            else:
                edge_ansi = build_left_border_ansi(frame, 23, 24)
                adapter.write(Text.from_ansi(f"  {edge_ansi}   {text}"))
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
