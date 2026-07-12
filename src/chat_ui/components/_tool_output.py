"""工具执行输出块 — ToolOutputBlock。

处理工具执行的标准输出/错误，支持 \\r 回车叠加和 ANSI 转义序列。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..renderer.output import OutputAdapter

from rich.text import Text

from ..const import _STYLE_DIM, _MAX_OUTPUT_LEN
from ._base import TuiComponent, _estimate_content_lines

_logger = logging.getLogger(__name__)


class ToolOutputBlock(TuiComponent):
    """工具执行输出块。"""
    def __init__(self, text: str):
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        text = self.text
        if not text:
            adapter.write_raw("\n")
            return 1
        if len(text) > _MAX_OUTPUT_LEN:
            text = text[:_MAX_OUTPUT_LEN] + "...(truncated)"
        bounce = self._get_bounce_prefix()
        has_carriage = '\r' in text
        if has_carriage:
            if '\033[' in text:
                clean = text.replace('\r', '')
                try:
                    if bounce:
                        adapter.write(Text.from_ansi(f"{bounce}{clean}\033[0m"))
                    else:
                        adapter.write(Text.from_ansi(clean))
                except Exception:
                    _logger.debug("tool_output ANSI 解析失败, 回退 raw 输出", exc_info=True)
                    if bounce:
                        adapter.write_raw(f"{bounce}{clean}\033[0m")
                    else:
                        adapter.write_raw(clean)
            else:
                clean = text.split('\r')[-1]
                if bounce:
                    adapter.write_raw(f"{bounce}{clean}\033[0m")
                else:
                    adapter.write_raw(clean)
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                return _estimate_content_lines(clean)
            return 0
        else:
            if bounce:
                adapter.write_raw(f"{bounce}   {text}\033[0m\n")
            else:
                adapter.write(Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
