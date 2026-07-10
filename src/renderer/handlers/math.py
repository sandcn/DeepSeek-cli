"""MathHandler — 数学块相关"""
import logging
from rich.text import Text
from rich.style import Style
from ..types import Token, TokenType
from .base import TokenHandler


_logger = logging.getLogger(__name__)


class MathHandler(TokenHandler):
    """处理数学块 Token。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.MATH_BLOCK_OPEN,
            TokenType.MATH_LINE,
            TokenType.MATH_BLOCK_CLOSE,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.MATH_BLOCK_OPEN: self._handle_math_block_open,
            TokenType.MATH_LINE: self._handle_math_line,
            TokenType.MATH_BLOCK_CLOSE: self._handle_math_block_close,
        }

    def _handle_math_block_open(self, token: Token, engine):
        """数学块打开：输出占位提示，避免长公式传输期间用户看到空白。"""
        engine.write(Text("[渲染数学公式中…]", style=Style(dim=True)))

    def _handle_math_line(self, token: Token, engine):
        """数学行（不再需要，源码由 MATH_BLOCK_CLOSE 携带）。"""
        pass

    def _handle_math_block_close(self, token: Token, engine):
        """数学块闭合：用完整源码一次性渲染并输出（Rich Panel 美化）。

        输出 "📐 " 前缀标识渲染后的数学公式。
        """
        try:
            source = token.meta.get("source", "") or token.content
            source = source.strip()
            if not source:
                return

            try:
                panel = engine.math_renderer.render_block(source)
            except Exception:
                _logger.warning("数学块渲染失败，降级为纯文本输出", exc_info=True)
                panel = Text(f"[Math: {source.strip()[:80]}]", style=Style(dim=True, italic=True))
            engine.write(Text("📐 ", style=Style(bold=True)))
            engine.write_typing(panel, 0, end="\n")
        except Exception:
            _logger.debug("数学块关闭异常，跳过", exc_info=True)
