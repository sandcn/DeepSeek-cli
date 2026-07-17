"""单行输出块 — WriteLineBlock。

支持 ANSI 转义序列，用于 OutputEvent / write_line 等非消息流的样式化行输出。

动效（2026-07-15 重构）：
  - 使用 Color256/Style 替代 build_left_border_ansi
  - 使用 StyleSheet 注册的语义色
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from rich.text import Text

from ..animation.animator import AnimatorContext
from ..core.style import Style, StyleSheet
from ..core.effects import sine_color
from ..terminal.terminal import is_narrow
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
        frame = AnimatorContext.get_default().frame
        # 使用 Style 构建左边缘呼吸边框（替代 build_left_border_ansi）
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_color = sine_color(frame, border_breath.fg if border_breath.fg is not None else 23,
                                   min(255, (border_breath.fg if border_breath.fg is not None else 23) + 2), 24)
        border_style = Style(fg=border_color)
        edge_ansi = f"{border_style.to_ansi()}\u2502\033[0m"

        if '\033[' in text:
            try:
                if is_narrow():
                    adapter.write(Text.from_ansi(text))
                else:
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
                adapter.write_raw(f"  {edge_ansi} {text}\n")
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
