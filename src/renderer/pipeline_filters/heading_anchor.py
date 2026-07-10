"""HeadingAnchorFilter — 收集 TOC 条目。"""

from __future__ import annotations

import logging

from ..pipeline import TokenFilter
from ..types import Token, TokenType, RenderContext

_logger = logging.getLogger(__name__)


class HeadingAnchorFilter(TokenFilter):
    """收集标题 TOC 条目到 ctx.toc。

    用法：
      filter = HeadingAnchorFilter(collect_toc=True)
      pipeline.add_filter(filter)
      # close() 后可通过 ctx.toc 获取所有标题条目
    """

    def __init__(self, collect_toc: bool = True):
        super().__init__()
        self._collect_toc = collect_toc

    def process(self, tokens: list[Token], ctx: RenderContext) -> list[Token]:
        """收集标题 TOC 条目。"""
        if self._collect_toc and ctx.toc is None:
            ctx.toc = []

        for token in tokens:
            if token.type is not TokenType.HEADING:
                continue

            level = token.meta.get("level", 1)
            text = token.content

            if self._collect_toc:
                heading_id = token.meta.get("id", "")
                ctx.toc.append({
                    "level": level,
                    "text": text,
                    "id": heading_id,
                })

        return tokens
