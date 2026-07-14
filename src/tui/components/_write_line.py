"""单行输出块 — WriteLineBlock。

支持 ANSI 转义序列，用于 OutputEvent / write_line 等非消息流的样式化行输出。

动效（2026-07-12 TUI 美化）：
  - 宽屏：左侧添加极淡青色呼吸边框字符 │（使用 build_glow_ansi 微呼吸，色号 23↔24）
  - 窄屏：降级为无左边缘的纯文本（与原始行为一致）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from rich.text import Text

from ..core.animator import AnimatorContext
from ..terminal.terminal import is_narrow
from ..core.text_utils import build_left_border_ansi
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
                if is_narrow():
                    adapter.write(Text.from_ansi(text))
                else:
                    frame = AnimatorContext.get_default().frame
                    edge_ansi = build_left_border_ansi(frame, 23, 24)
                    adapter.write(Text.from_ansi(f"  {edge_ansi} {text}"))
            except Exception:
                _logger.debug("write_line ANSI 解析失败, 回退 raw 输出", exc_info=True)
                adapter.write_raw(text + "\n")
                return _estimate_content_lines(text)
            return _estimate_content_lines(text)
        else:
            if is_narrow():
                adapter.write_raw(text + "\n")
            else:
                frame = AnimatorContext.get_default().frame
                edge_ansi = build_left_border_ansi(frame, 23, 24)
                adapter.write_raw(f"  {edge_ansi} {text}\n")
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
