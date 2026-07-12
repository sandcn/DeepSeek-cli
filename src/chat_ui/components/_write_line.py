"""单行输出块 — WriteLineBlock。

支持 ANSI 转义序列，用于 OutputEvent / write_line 等非消息流的样式化行输出。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..renderer.output import OutputAdapter

from rich.text import Text

from ._base import TuiComponent, _estimate_content_lines

_logger = logging.getLogger(__name__)


class WriteLineBlock(TuiComponent):
    """单行输出块 — 支持 ANSI 转义序列。

    用于 OutputEvent / write_line 等非消息流的样式化行输出。
    """
    def __init__(self, text: str):
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        text = self.text
        if '\033[' in text:
            try:
                adapter.write(Text.from_ansi(text))
            except Exception:
                _logger.debug("write_line ANSI 解析失败, 回退 raw 输出", exc_info=True)
                adapter.write_raw(text + "\n")
                return _estimate_content_lines(text)
            return _estimate_content_lines(text)
        else:
            adapter.write_raw(text + "\n")
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
