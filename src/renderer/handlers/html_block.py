"""HtmlBlockHandler — HTML 块级元素"""
import logging
from rich.text import Text
from ..types import Token, TokenType
from .._rendering import render_html_block_open, render_html_block_close
from .base import TokenHandler


_logger = logging.getLogger(__name__)


class HtmlBlockHandler(TokenHandler):
    """处理 HTML 块级元素 Token。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.HTML_BLOCK_OPEN,
            TokenType.HTML_BLOCK_LINE,
            TokenType.HTML_BLOCK_CLOSE,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.HTML_BLOCK_OPEN: self._handle_html_block_open,
            TokenType.HTML_BLOCK_LINE: self._handle_html_block_line,
            TokenType.HTML_BLOCK_CLOSE: self._handle_html_block_close,
        }

    def _handle_html_block_open(self, token: Token, engine):
        """HTML 块级元素打开：输出带颜色的视觉框标记。"""
        try:
            tag = token.meta.get("tag", "div")
            t = render_html_block_open(tag, engine.output_width)
            engine.write(t)
        except Exception:
            _logger.debug("HTML块打开渲染异常，跳过", exc_info=True)

    def _handle_html_block_line(self, token: Token, engine):
        """HTML 块级元素内容行：内联渲染后缩进2空格输出。"""
        try:
            content = engine.render_inline(token.content)
            assembled = Text("  ")
            assembled.append_text(content)
            engine.output_assembled(assembled)
        except Exception:
            _logger.debug("HTML块行渲染异常，跳过", exc_info=True)

    def _handle_html_block_close(self, token: Token, engine):
        """HTML 块级元素关闭：输出关闭框线。"""
        try:
            tag = token.meta.get("tag", "div")
            t = render_html_block_close(tag, engine.output_width)
            engine.write(t)
        except Exception:
            _logger.debug("HTML块关闭渲染异常，跳过", exc_info=True)
