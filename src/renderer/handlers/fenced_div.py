"""FencedDivHandler — 自定义容器块（::: 语法）"""
import logging
from ..types import Token, TokenType
from .base import TokenHandler
from ._box_base import BaseBoxMixin
from ..admonition import get_admonition_config, ADMONITION_STYLES

_logger = logging.getLogger(__name__)


class FencedDivHandler(TokenHandler, BaseBoxMixin):
    """处理 fenced div（::: 语法）Token（继承 BaseBoxMixin 复用框线渲染逻辑）。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.FENCED_DIV_OPEN,
            TokenType.FENCED_DIV_LINE,
            TokenType.FENCED_DIV_CLOSE,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.FENCED_DIV_OPEN: self._handle_fenced_div_open,
            TokenType.FENCED_DIV_LINE: self._handle_fenced_div_line,
            TokenType.FENCED_DIV_CLOSE: self._handle_fenced_div_close,
        }

    def _get_color(self, box_type: str) -> str:
        """获取 fenced div 的颜色，未知类型降级到 bright_black。"""
        if box_type in ADMONITION_STYLES:
            return get_admonition_config(box_type)["color"]
        return "bright_black"

    def _get_prefix(self, box_type: str) -> str:
        return f":: {box_type}"

    def _handle_fenced_div_open(self, token: Token, engine):
        self._handle_box_open(token, engine)

    def _handle_fenced_div_line(self, token: Token, engine):
        self._handle_box_line(token, engine)

    def _handle_fenced_div_close(self, token: Token, engine):
        self._handle_box_close(token, engine)
