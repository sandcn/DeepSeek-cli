"""AdmonitionHandler — 告示块"""
import logging

from rich.text import Text
from rich.style import Style

from ..types import Token, TokenType
from ..admonition import get_admonition_config
from .base import TokenHandler
from ._box_base import BaseBoxMixin
from .._rendering import render_box_close

_logger = logging.getLogger(__name__)


class AdmonitionHandler(TokenHandler, BaseBoxMixin):
    """处理告示块 Token（继承 BaseBoxMixin 复用框线渲染逻辑）。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.ADMONITION_OPEN,
            TokenType.ADMONITION_LINE,
            TokenType.ADMONITION_CLOSE,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.ADMONITION_OPEN: self._handle_admonition_open,
            TokenType.ADMONITION_LINE: self._handle_admonition_line,
            TokenType.ADMONITION_CLOSE: self._handle_admonition_close,
        }

    def _get_color(self, box_type: str) -> str:
        return get_admonition_config(box_type)["color"]

    def _get_prefix(self, box_type: str) -> str:
        config = get_admonition_config(box_type)
        return f"{config['icon']} {config['label']}"

    def _handle_admonition_open(self, token: Token, engine):
        box_type = self._get_box_type(token)
        if box_type == "CITE":
            self._handle_cite_open(token, engine)
        else:
            self._handle_box_open(token, engine)

    def _handle_admonition_line(self, token: Token, engine):
        box_type = self._get_box_type(token)
        if box_type == "CITE":
            self._handle_cite_line(token, engine)
        else:
            self._handle_box_line(token, engine)

    def _handle_admonition_close(self, token: Token, engine):
        box_type = self._get_box_type(token)
        if box_type == "CITE":
            self._handle_cite_close(token, engine)
        else:
            self._handle_box_close(token, engine)

    def _handle_cite_open(self, token: Token, engine):
        """CITE 引用块打开：输出 📖  前缀 + 内容（斜体+dim 样式），无框线。"""
        try:
            content = engine.render_inline(token.content)
            prefix = Text("  📖 引用", style=Style(dim=True, bold=True, color="bright_black"))
            assembled = Text.assemble(prefix, Text(" ", style=Style(dim=True)), content)
            engine.write(assembled)
        except Exception:
            _logger.debug("CITE 引用块打开渲染异常", exc_info=True)

    def _handle_cite_line(self, token: Token, engine):
        """CITE 引用块内容行：斜体+dim 样式，带缩进竖线。"""
        try:
            content = engine.render_inline(token.content)
            assembled = Text.assemble(
                Text("    ", style=Style(dim=True)),
                Text("│ ", style=Style(dim=True, color="bright_black")),
                content,
            )
            engine.output_assembled(assembled)
        except Exception:
            _logger.debug("CITE 引用行渲染异常", exc_info=True)

    def _handle_cite_close(self, token: Token, engine):
        """CITE 引用块关闭：输出底部 ─── 标记。"""
        try:
            engine.write(render_box_close("bright_black", engine.output_width))
        except Exception:
            _logger.debug("CITE 引用块关闭渲染异常", exc_info=True)
