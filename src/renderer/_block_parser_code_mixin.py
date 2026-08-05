"""_BlockParserCodeMixin — RegexFreeBlockParser 的 Code 职责分组。

从 _block_parser.py 拆分（2026-08-06 架构整理）。
与 RegexFreeBlockParser 主体共享实例状态（self._state / self._buffer 等），
由主体类多继承组合，不直接实例化。
"""
from __future__ import annotations

import logging

from .types import Token, TokenType
from ._block_parser_common import _State
from ._block_helpers import (
    _LANG_BLACKLIST,
    _get_fence_lang,
    _is_empty_line,
    _strip_blockquote_prefix,
)
from ._utils import _COMMON_LANGUAGES, _get_fence_info

_logger = logging.getLogger(__name__)

# Mermaid 图表内容首行关键词（用于延迟 fence 后自动识别 mermaid 块）
_MERMAID_KEYWORDS: frozenset[str] = frozenset({
    "graph", "flowchart", "sequenceDiagram", "classDiagram",
    "stateDiagram", "stateDiagram-v2", "erDiagram", "gantt",
    "pie", "gitgraph", "mindmap", "timeline", "journey",
    "block", "packet", "quadrantChart", "requirementDiagram",
    "C4Context", "C4Container", "C4Component", "C4Deployment",
    "gitGraph",
})

# Setext 标题/HR 标记字符（避免每次调用创建元组）
_SETEXT_HR_CHARS: frozenset[str] = frozenset({'-', '=', '*', '_'})


class _BlockParserCodeMixin:
    """见模块 docstring。"""

    def _should_auto_close_fence(self, stripped: str, line: str = '') -> bool:
        if not stripped:
            self._auto_close_streak = 0
            return False
        if self._block_lang.lower() not in ('text', 'txt', 'plain', ''):
            self._auto_close_streak = 0
            return False
        if line and (line[0] in ' \t'):
            self._auto_close_streak = 0
            return False
        if stripped[0] not in '#-*_|':
            self._auto_close_streak = 0
            return False
        matched = False
        if len(stripped) >= 3 and stripped[0] == '#':
            level = 0
            for ch in stripped:
                if ch == '#':
                    level += 1
                else:
                    break
            if 2 <= level <= 6 and level < len(stripped) and stripped[level] == ' ':
                matched = True
        if not matched and len(stripped) >= 3:
            first = stripped[0]
            if first in _SETEXT_HR_CHARS:
                only = True
                for ch in stripped:
                    if ch not in (' ', first):
                        only = False
                        break
                if only and len(stripped.replace(' ', '')) >= 3:
                    matched = True
        if not matched and stripped[0] == '|' and stripped.count('|') >= 2:
            # 只有包含分隔行模式（:- 等）才是真正的表格行，避免对含 pipe 的普通内容误触发
            if ':-' in stripped or '-:' in stripped or ':-:' in stripped:
                matched = True
        if matched:
            self._auto_close_streak += 1
            return self._auto_close_streak >= 5
        else:
            self._auto_close_streak = 0
            return False

    def _feed_code_fence_line(self, line: str, stripped: str, tokens: list[Token]):
        if stripped and not (stripped[0] in '#-*_|' and len(stripped) >= 3):
            self._auto_close_streak = 0

        if self._auto_close_streak > 0 and stripped and stripped[0] == '|':
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if any(c.isalnum() for c in cells if c):
                self._auto_close_streak = 0

        if self._should_auto_close_fence(stripped, line):
            tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                                {"lang": self._block_lang, "indented": False}))
            self._state = _State.NORMAL
            self._parse_normal_line(line, tokens)
            return
        fchar, flen, _ = _get_fence_info(stripped)
        if not fchar:
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        if fchar != self._block_fence_char:
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        if flen < self._block_fence_len:
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        close_lang = _get_fence_lang(stripped[flen:].strip())
        is_markdown = self._block_lang.lower() in ('markdown', 'md')
        if close_lang and is_markdown:
            self._block_nested_fence += 1
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        if not close_lang and is_markdown and self._block_nested_fence > 0:
            self._block_nested_fence -= 1
            tokens.append(Token(TokenType.CODE_LINE, line.rstrip('\n')))
            return
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                            {"lang": self._block_lang, "indented": False}))
        self._state = _State.NORMAL

    def _detect_streaming_fence_merge(
        potential: str, remaining: str, lang_end: int,
    ) -> tuple[str, str]:
        """检测流式 chunk 边界合并导致的语言标识粘连。"""
        found_prefix = False
        for i in range(min(len(potential), 12), 2, -1):
            prefix = potential[:i]
            if prefix in _COMMON_LANGUAGES:
                lang = prefix
                extra_chars = potential[i:]
                remaining = extra_chars + remaining[lang_end:]
                found_prefix = True
                break
        if not found_prefix:
            lang = potential
            remaining = remaining[lang_end:]
        return lang, remaining

    def _try_code_fence_start(self, stripped: str) -> dict | None:
        try:
            first = stripped[0]
            if first not in ('`', '~'):
                return None
            count = 0
            for ch in stripped:
                if ch == first:
                    count += 1
                else:
                    break
            if count < 3:
                return None
            remaining = stripped[count:].strip()
            lang = ''
            attrs = ''
            title = ''
            extra = ''
            if remaining:
                lang_end = 0
                for ch in remaining:
                    if ch.isalnum() or ch in '+.#-_':
                        lang_end += 1
                    else:
                        break
                if lang_end > 0:
                    potential = remaining[:lang_end].lower()
                    if (potential in _COMMON_LANGUAGES
                            or (potential.isalpha() and len(potential) <= 8
                                and potential not in _LANG_BLACKLIST)):
                        lang = potential
                        remaining = remaining[lang_end:]
                    elif len(potential) <= 20 and potential[0].isalpha():
                        lang, remaining = self._detect_streaming_fence_merge(
                            potential, remaining, lang_end,
                        )
                if remaining and remaining[0] == '{':
                    brace_end = remaining.find('}')
                    if brace_end >= 0:
                        attrs = remaining[:brace_end + 1]
                        # Extract title= from within {} attrs if not already set
                        if not title:
                            inner = remaining[1:brace_end]
                            ti = inner.find('title=')
                            if ti >= 0:
                                after_eq = inner[ti + 6:]
                                if after_eq and after_eq[0] in '"\'':
                                    q = after_eq[0]
                                    end = after_eq.find(q, 1)
                                    if end > 0:
                                        title = after_eq[1:end]
                                elif after_eq:
                                    # Unquoted title: take up to next space or end
                                    end_space = after_eq.find(' ')
                                    if end_space > 0:
                                        title = after_eq[:end_space]
                                    else:
                                        title = after_eq
                        remaining = remaining[brace_end + 1:]
                remaining = remaining.strip()
                if remaining.startswith('title='):
                    after = remaining[6:]
                    if after and after[0] in '"\'':
                        q = after[0]
                        end = after.find(q, 1)
                        if end > 0:
                            title = after[1:end]
                            remaining = ''
                remaining = remaining.strip()
                if remaining and not remaining.startswith('title='):
                    extra = remaining
            return {
                'fence_char': first,
                'fence_len': count,
                'lang': lang if lang else None,
                'attrs': attrs,
                'title': title,
                'deferred': not lang,
                'extra': extra,
            }
        except Exception:
            _logger.debug("_try_code_fence_start异常，返回None", exc_info=True)
            return None

    def _start_code_fence(self, info: dict, tokens: list[Token]):
        self._state = _State.CODE_FENCE
        self._block_fence_char = info['fence_char']
        self._block_fence_len = info['fence_len']
        self._block_lang = info.get('lang') or 'text'
        self._block_attrs = info.get('attrs', '')
        self._block_title = info.get('title', '')
        self._block_lines = []
        self._block_nested_fence = 0
        self._auto_close_streak = 0
        lang = self._block_lang
        if lang.lower() in ('mermaid',) or lang.lower().startswith('mermaid'):
            self._state = _State.MERMAID_BLOCK
            tokens.append(Token(TokenType.MERMAID_BLOCK_OPEN, "",
                                {"lang": lang, "attrs": info['attrs']}))
        else:
            tokens.append(Token(TokenType.CODE_FENCE_OPEN, "", {
                "lang": lang, "attrs": info['attrs'], "title": info['title'],
            }))

    def _handle_deferred_fence(self, stripped: str, tokens: list[Token]):
        fence = self._deferred_fence
        self._deferred_fence = None

        if self._bq_active and stripped and stripped[0] == '>':
            s = stripped
            while s.startswith('>'):
                s = _strip_blockquote_prefix(s)
            stripped = s

        if _is_empty_line(stripped):
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
            tokens.append(Token(TokenType.CODE_LINE, ""))
            return
        if stripped and stripped[0] in ('`', '~'):
            fchar, flen, _ = _get_fence_info(stripped)
            if fchar and flen >= fence['fence_len'] and fchar == fence['fence_char']:
                self._start_code_fence(fence, tokens)
                if fence.get('extra'):
                    if self._state == _State.MERMAID_BLOCK:
                        tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                    else:
                        tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
                self._feed_code_fence_line(stripped + '\n', stripped, tokens)
                return
        lang = _get_fence_lang(stripped)
        if lang and lang in _COMMON_LANGUAGES:
            fence['lang'] = lang
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
            return
        if lang and lang in _MERMAID_KEYWORDS:
            fence['lang'] = 'mermaid'
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
            self._block_lines.append(stripped)
            return
        self._start_code_fence(fence, tokens)
        if fence.get('extra'):
            if self._state == _State.MERMAID_BLOCK:
                tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
            else:
                tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
        self._feed_code_fence_line(stripped + '\n', stripped, tokens)

    def _flush_code_fence(self, tokens: list[Token]):
        if self._block_lines:
            for l in self._block_lines:
                tokens.append(Token(TokenType.CODE_LINE, l))
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                            {"lang": self._block_lang}))

    def _feed_indented_code_line(self, line: str, stripped: str, tokens: list[Token]):
        if _is_empty_line(stripped):
            tokens.append(Token(TokenType.CODE_LINE, ""))
            return
        if line[:4] == '    ' or (line and line[0] == '\t'):
            content = line[4:] if line[:4] == '    ' else line[1:]
            tokens.append(Token(TokenType.CODE_LINE, content.rstrip('\n')))
            return
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
            "lang": "text", "indented": True,
        }))
        self._state = _State.NORMAL
        self._parse_normal_line(line, tokens)

    def _start_indented_code(self, line: str, tokens: list[Token]):
        self._state = _State.INDENTED_CODE
        tokens.append(Token(TokenType.CODE_FENCE_OPEN, "", {
            "lang": "text", "indented": True, "attrs": "",
        }))
        content = line[4:] if line[:4] == '    ' else line[1:]
        tokens.append(Token(TokenType.CODE_LINE, content.rstrip('\n')))

    def _flush_indented_code(self, tokens: list[Token]):
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
            "lang": "text", "indented": True,
        }))
