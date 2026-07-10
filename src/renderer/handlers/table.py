"""TableHandler — 表格渲染"""
import logging
from ..types import Token, TokenType
from .._rendering import build_rich_table
from .base import TokenHandler


_logger = logging.getLogger(__name__)


class TableHandler(TokenHandler):
    """处理表格 Token。"""

    def get_token_types(self) -> set[TokenType]:
        return {TokenType.TABLE}

    def get_method_map(self) -> dict[TokenType, callable]:
        return {
            TokenType.TABLE: self._handle_table,
        }

    def _handle_table(self, token: Token, engine):
        """表格渲染（使用共享 build_rich_table）。"""
        try:
            rows: list[list[str]] = token.meta.get("rows", [])
            alignments: list[str] = token.meta.get("alignments", [])

            if not rows:
                return
            if not alignments:
                alignments = ['left'] * len(rows[0])

            table = build_rich_table(
                rows, alignments,
                engine.render_inline,
                engine.output_width,
            )
            # engine.print(table) 内部 console.print 输出已自带 \n，
            # 去掉多余的 engine.write_line() 避免双换行
            engine.print(table)
        except Exception:
            _logger.debug("表格渲染异常，跳过", exc_info=True)
