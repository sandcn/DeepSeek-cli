"""FrontMatterHandler — 文档头元信息块（YAML / TOML / JSON）渲染（Rich 路径）"""

import logging

from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .._front_matter import parse_front_matter_items
from ..types import Token, TokenType
from .base import TokenHandler

_logger = logging.getLogger(__name__)


class FrontMatterHandler(TokenHandler):
    """处理 Front Matter Token（元信息卡片）。"""

    def get_token_types(self) -> set[TokenType]:
        return {TokenType.FRONT_MATTER}

    def get_method_map(self) -> dict[TokenType, callable]:
        return {TokenType.FRONT_MATTER: self._handle_front_matter}

    def _handle_front_matter(self, token: Token, engine):
        try:
            fmt = str(token.meta.get("format", "yaml")).upper()
            text = token.content or ""
            items = parse_front_matter_items(text, fmt.lower())
            if items:
                grid = Table.grid(padding=(0, 1))
                grid.add_column(style=Style(color="bright_cyan", bold=True), no_wrap=True)
                grid.add_column()
                for key, value in items:
                    label = f"{key}:" if key else ""
                    grid.add_row(label, engine.render_inline(value) if value else Text(""))
                body = grid
            else:
                body = engine.render_inline(text)
            engine.print(Panel(body, title=f"元信息 ({fmt})",
                               border_style=Style(color="grey37"), expand=False))
        except Exception:
            _logger.debug("Front Matter 渲染异常，跳过", exc_info=True)


class TableCaptionHandler(TokenHandler):
    """处理表格表注 Token（``: 说明`` / ``Table: 说明``）。"""

    def get_token_types(self) -> set[TokenType]:
        return {TokenType.TABLE_CAPTION}

    def get_method_map(self) -> dict[TokenType, callable]:
        return {TokenType.TABLE_CAPTION: self._handle_table_caption}

    def _handle_table_caption(self, token: Token, engine):
        try:
            text = engine.render_inline(token.content or "")
            text.stylize(Style(dim=True, italic=True, color="grey62"))
            assembled = Text("  ")
            assembled.append_text(text)
            engine.output_assembled(assembled)
        except Exception:
            _logger.debug("表格表注渲染异常，跳过", exc_info=True)


__all__ = ["FrontMatterHandler", "TableCaptionHandler"]
