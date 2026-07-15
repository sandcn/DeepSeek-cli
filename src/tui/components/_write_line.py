"""单行输出块 — WriteLineBlock。

支持 ANSI 转义序列，用于 OutputEvent / write_line 等非消息流的样式化行输出。

动效（2026-07-15 重构）：
  - 使用 Color256/Style 替代 build_left_border_ansi
  - 使用 StyleSheet 注册的语义色

【inline 模式 · 2026-07-16】
新增 render_to_target() 直写 ANSI 到 IOutputTarget，绕过 Rich Console。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter
    from ...tui_framework.terminal.output_target import IOutputTarget

from rich.text import Text

from ..core.animator import AnimatorContext
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

    def _build_edge_ansi(self) -> str:
        """构建左边缘呼吸边框 ANSI 字符串。"""
        frame = AnimatorContext.get_default().frame
        border_breath = StyleSheet.resolve("border_breath", Style(fg=23))
        border_color = sine_color(
            frame,
            border_breath.fg if border_breath.fg is not None else 23,
            min(255, (border_breath.fg if border_breath.fg is not None else 23) + 2),
            24,
        )
        border_style = Style(fg=border_color)
        return f"{border_style.to_ansi()}\u2502\033[0m"

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        text = self.text
        edge_ansi = self._build_edge_ansi()

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

    def render_to_target(self, target: "IOutputTarget") -> int:
        """渲染到 IOutputTarget（inline 模式），返回行数。"""
        text = self.text
        edge_ansi = self._build_edge_ansi()

        if is_narrow():
            target.write_line(text)
        else:
            target.write_line(f"  {edge_ansi} {text}")
        return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
