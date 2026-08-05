"""_BlockParserContainerMixin — RegexFreeBlockParser 的 Container 职责分组。

从 _block_parser.py 拆分（2026-08-06 架构整理）。
与 RegexFreeBlockParser 主体共享实例状态（self._state / self._buffer 等），
由主体类多继承组合，不直接实例化。
"""
from __future__ import annotations

import logging

from .types import Token, TokenType
from ._block_parser_common import _State
from ._block_helpers import (
    _BLOCK_HTML_TAGS,
    _is_empty_line,
)

_logger = logging.getLogger(__name__)

class _BlockParserContainerMixin:
    """见模块 docstring。"""

    def _try_details_open(self, stripped: str) -> bool:
        try:
            lower = stripped.lower()
            return lower.startswith('<details')
        except Exception:
            _logger.debug("_try_details_open异常，返回False", exc_info=True)
            return False

    def _start_details(self, stripped: str, tokens: list[Token]):
        self._state = _State.DETAILS_BLOCK
        self._details_depth = 1
        self._details_summary = ''
        self._details_open_emitted = False
        self._block_lines = []
        lower = stripped.lower()
        sm_start = lower.find('<summary')
        if sm_start >= 0:
            self._handle_details_summary_line(stripped, tokens)
            lower_rest = stripped.lower()
            close_pos = lower_rest.find('</details>', sm_start)
            if close_pos >= 0:
                self._emit_details_close(tokens)

    def _handle_details_summary_line(self, stripped: str, tokens: list[Token]):
        lower = stripped.lower()
        summary = ''
        rest = stripped
        sm_start = lower.find('<summary')
        if sm_start >= 0:
            tag_end = stripped.find('>', sm_start)
            if tag_end >= 0:
                content_start = tag_end + 1
                close = lower.find('</summary>', content_start)
                if close >= 0:
                    summary = stripped[content_start:close].strip()
                    rest = stripped[close + 10:].strip()
        self._details_summary = summary
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": summary}))
            self._details_open_emitted = True
        if rest:
            self._block_lines.append(rest)

    def _emit_details_close(self, tokens: list[Token]):
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": self._details_summary}))
            self._details_open_emitted = True
        if self._block_lines:
            for l in self._block_lines:
                tokens.append(Token(TokenType.DETAILS_LINE, l))
            self._block_lines = []
        tokens.append(Token(TokenType.DETAILS_CLOSE))
        self._state = _State.NORMAL

    def _feed_details_line(self, line: str, stripped: str, tokens: list[Token]):
        lower = stripped.lower()
        if lower.startswith('</details'):
            self._details_depth -= 1
            if self._details_depth > 0:
                self._block_lines.append(stripped)
                return
            self._emit_details_close(tokens)
            return
        if lower.startswith('<details'):
            self._details_depth += 1
            self._block_lines.append(stripped)
            return
        if '<summary' in lower:
            if self._details_depth > 1:
                self._block_lines.append(stripped)
            else:
                self._handle_details_summary_line(stripped, tokens)
            return
        if not self._details_open_emitted:
            tokens.append(Token(TokenType.DETAILS_OPEN, "",
                                {"summary": self._details_summary}))
            self._details_open_emitted = True
        self._block_lines.append(stripped)

    def _flush_details_block(self, tokens: list[Token]):
        self._emit_details_close(tokens)

    def _try_block_html(self, stripped: str) -> str | None:
        lower = stripped.lower()
        i = 0
        while i < len(lower) and lower[i] in ' \t':
            i += 1
        if i >= len(lower) or lower[i] != '<':
            return None
        i += 1
        if i < len(lower) and lower[i] == '/':
            i += 1
        tag_start = i
        while i < len(lower) and (lower[i].isalnum() or lower[i] in '-:'):
            i += 1
        tag = lower[tag_start:i]
        if tag and tag in _BLOCK_HTML_TAGS:
            return tag
        return None

    def _is_html_close(self, stripped: str, tag: str) -> bool:
        lower = stripped.lower().strip()
        return lower == f'</{tag}>' or lower.startswith(f'</{tag} ')

    def _start_html_block(self, tag: str, tokens: list[Token],
                           line_content: str = ""):
        self._state = _State.HTML_BLOCK
        self._block_html_tag = tag
        tokens.append(Token(TokenType.HTML_BLOCK_OPEN, "",
                            {"tag": tag}))
        if line_content:
            close_tag = f'</{tag}>'
            lower_line = line_content.lower()
            if close_tag in lower_line:
                tag_end = -1
                search_start = lower_line.find(tag)
                if search_start >= 0:
                    tag_end = line_content.find('>', search_start + len(tag))
                if tag_end >= 0:
                    inner = line_content[tag_end + 1:]
                    close_pos = inner.lower().find(close_tag)
                    if close_pos >= 0:
                        content = inner[:close_pos].strip()
                        if content:
                            tokens.append(Token(TokenType.HTML_BLOCK_LINE,
                                                content))
                        tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                            {"tag": tag}))
                        self._state = _State.NORMAL

    def _flush_html_block(self, tokens: list[Token]):
        tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                            {"tag": self._block_html_tag}))

    def _handle_fenced_div_open(self, stripped: str, tokens: list[Token]):
        rest = stripped[3:].lstrip()
        div_type = ''
        text = ''
        if rest:
            space = rest.find(' ')
            if space > 0:
                div_type = rest[:space]
                text = rest[space + 1:].strip()
            else:
                div_type = rest
        if not div_type:
            div_type = 'NOTE'
        self._block_div_type = div_type.upper()
        self._block_lines = []
        tokens.append(Token(TokenType.FENCED_DIV_OPEN, text, {"type": self._block_div_type}))
        self._state = _State.FENCED_DIV

    def _feed_fenced_div_line(self, line: str, stripped: str, tokens: list[Token]):
        if not stripped or _is_empty_line(line):
            # ★ 空行在 fenced div 内：不关闭 div，发射空行标记作为一条空白线
            tokens.append(Token(TokenType.FENCED_DIV_LINE, "", {"type": self._block_div_type, "empty": True}))
            return
        if stripped.strip() == ':::':
            tokens.append(Token(TokenType.FENCED_DIV_CLOSE, "", {"type": self._block_div_type}))
            self._state = _State.NORMAL
            return
        tokens.append(Token(TokenType.FENCED_DIV_LINE, stripped, {"type": self._block_div_type}))

    def _flush_fenced_div(self, tokens: list[Token]):
        tokens.append(Token(TokenType.FENCED_DIV_CLOSE, "", {"type": self._block_div_type}))
