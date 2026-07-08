"""MathRenderer — LaTeX 数学公式渲染器（门面类）。

公共接口：
  render(expr, is_block=False) -> Text
  render_inline(expr) -> Text
  render_block(expr) -> Text

内部委托给 MathParser 进行实际解析。
MathParser 位于 math_parser.py。
"""

from __future__ import annotations

import logging

from rich.text import Text
from rich.style import Style
from rich.panel import Panel


from .math_parser import MathParser
from .math_symbols import _STYLE_INLINE

logger = logging.getLogger(__name__)


class MathRenderer:
    """LaTeX 数学公式渲染器，将 LaTeX 表达式转换为 Rich Text 对象。

    门面类，内部委托给 MathParser。
    """

    def __init__(self) -> None:
        self._parser: MathParser | None = None

    def _get_parser(self) -> MathParser:
        if self._parser is None:
            self._parser = MathParser()
        return self._parser

    def render(self, expr: str, is_block: bool = False) -> Text:
        parser = self._get_parser()
        parser._is_block = is_block
        try:
            result = parser.parse(expr)
        except Exception as e:
            logger.warning("MathParser 解析失败，降级为纯文本: %s", e, exc_info=True)
            if expr:
                result = Text("⚠️ ", style=Style(color="yellow", bold=True))
                result.append(expr, style=Style(dim=True, italic=True))
            else:
                result = Text()

        if is_block:
            result.stylize(Style(bold=True, color="bright_white"))
            return result
        else:
            result.stylize(_STYLE_INLINE)
            return result

    def render_inline(self, expr: str) -> Text:
        return self.render(expr, is_block=False)

    def render_block(self, expr: str) -> Panel:
        """渲染块级公式，返回 Rich Panel 包裹的公式。"""
        inner = self.render(expr, is_block=True)
        return Panel(
            inner,
            border_style="bright_magenta",
            padding=(0, 1),
        )
