"""DetailsHandler — Details 折叠块"""
import logging
from rich.text import Text
from rich.style import Style
from ..types import Token, TokenType
from .base import TokenHandler


_logger = logging.getLogger(__name__)

# 折叠块最大嵌套深度（超过此深度直接忽略）
_MAX_DETAILS_DEPTH = 10


class DetailsHandler(TokenHandler):
    """处理 Details 折叠块 Token。"""

    def get_token_types(self) -> set[TokenType]:
        return {
            TokenType.DETAILS_OPEN,
            TokenType.DETAILS_LINE,
            TokenType.DETAILS_CLOSE,
        }

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.DETAILS_OPEN: self._handle_details_open,
            TokenType.DETAILS_LINE: self._handle_details_line,
            TokenType.DETAILS_CLOSE: self._handle_details_close,
        }

    def _handle_details_open(self, token: Token, engine):
        """Details 块打开：▶ summary"""
        try:
            if engine.details_state.depth >= _MAX_DETAILS_DEPTH:
                return
            engine.details_state.depth += 1
            summary = token.meta.get("summary", "")
            indent = "  " * (engine.details_state.depth - 1)

            arrow = Text(f"{indent}▶ ", style=Style(bold=True, color="bright_yellow"))
            if summary:
                summary_text = engine.render_inline(summary)
            else:
                summary_text = Text("展开", style=Style(dim=True, italic=True))
            assembled = Text.assemble(arrow, summary_text)
            engine.output_assembled(assembled)
        except Exception:
            _logger.debug("Details块打开渲染异常，跳过", exc_info=True)

    def _handle_details_line(self, token: Token, engine):
        """Details 内容行（缩进）。"""
        try:
            indent = "  " * engine.details_state.depth
            content = engine.render_inline(token.content)
            assembled = Text.assemble(indent, content)
            engine.output_assembled(assembled)
        except Exception:
            _logger.debug("Details行渲染异常，跳过", exc_info=True)

    def _handle_details_close(self, token: Token, engine):
        """Details 块关闭。"""
        try:
            engine.details_state.depth -= 1
            if engine.details_state.depth < 0:
                engine.details_state.depth = 0
                return
            indent = "  " * engine.details_state.depth
            close_marker = Text(f"{indent}{'─' * 3}", style=Style(dim=True))
            engine.write(close_marker)
        except Exception:
            _logger.debug("Details块关闭渲染异常，跳过", exc_info=True)
