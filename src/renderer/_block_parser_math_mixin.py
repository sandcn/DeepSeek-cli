"""_BlockParserMathMixin — RegexFreeBlockParser 的 Math 职责分组。

从 _block_parser.py 拆分（2026-08-06 架构整理）。
与 RegexFreeBlockParser 主体共享实例状态（self._state / self._buffer 等），
由主体类多继承组合，不直接实例化。
"""
from __future__ import annotations

import logging

from .types import Token, TokenType
from ._block_parser_common import _State

class _BlockParserMathMixin:
    """见模块 docstring。"""

    def _start_display_math(self, tokens: list[Token]):
        self._state = _State.DISPLAY_MATH_BLOCK
        self._block_lines = []
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _start_math_block(self, tokens: list[Token]):
        self._state = _State.MATH_BLOCK
        self._block_lines = []
        tokens.append(Token(TokenType.MATH_BLOCK_OPEN))

    def _emit_math_block(self, tokens: list[Token]):
        source = '\n'.join(self._block_lines)
        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                            {"source": source}))
        self._state = _State.NORMAL

    def _emit_mermaid_block(self, tokens: list[Token]):
        source = ''.join(self._block_lines).strip()
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source))
        self._state = _State.NORMAL

    def _flush_math_block(self, tokens: list[Token]):
        source = '\n'.join(self._block_lines)
        tokens.append(Token(TokenType.MATH_BLOCK_CLOSE, source,
                            {"source": source}))

    def _flush_mermaid_block(self, tokens: list[Token]):
        source = ''.join(self._block_lines).strip()
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, source))
