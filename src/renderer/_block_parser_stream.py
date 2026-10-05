"""_block_parser 流式/块级状态处理 mixin — 从 _block_parser.py 拆分。

承载非 NORMAL 状态下的行处理（代码 fence / 数学 / Mermaid / Details /
缩进代码 / HTML / 表格活动）与块级语法尝试（标题 / 代码 fence 起始 /
Setext / 延迟 fence）方法群。``RegexFreeBlockParser`` 继承本 mixin。

拆分目的：降低 ``_block_parser.py`` 单文件体量，保持行为与调用关系不变
（方法间通过 ``self`` 分派，跨 mixin 正常）。
"""

from __future__ import annotations

import logging

from ._block_parser_state import _State, _MERMAID_KEYWORDS, _SETEXT_HR_CHARS
from ._utils import _COMMON_LANGUAGES, _get_fence_info
from .types import Token, TokenType
from ._table_utils import _is_table_row, _parse_table_row
from ._block_helpers import (
    _is_empty_line, _strip_left, _rstrip_line,
    _is_code_fence_line, _strip_blockquote_prefix,
    _get_fence_lang, _rstrip_trailing_hashes,
    _LANG_BLACKLIST,
)

_logger = logging.getLogger(__name__)


class _BlockParserStreamMixin:
    """非 NORMAL 状态行处理 + 块级语法尝试方法群（详见模块 docstring）。"""

    def _feed_block_line(self, line: str, tokens: list[Token]):
        """处理非 NORMAL 状态下的行。"""
        stripped = _strip_left(line).rstrip('\n')

        if self._bq_active and stripped and stripped[0] == '>':
            s = stripped
            while s.startswith('>'):
                s = _strip_blockquote_prefix(s)
            line = s + '\n'
            stripped = s

        if self._state == _State.CODE_FENCE:
            try:
                self._feed_code_fence_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Code fence块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.MATH_BLOCK:
            try:
                if stripped == '$$':
                    self._emit_math_block(tokens)
                elif stripped.endswith('$$') and len(stripped) > 2:
                    content_line = stripped[:-2].rstrip()
                    if content_line:
                        self._block_lines.append(content_line)
                    self._emit_math_block(tokens)
                else:
                    self._block_lines.append(line.rstrip('\n'))
            except Exception:
                _logger.debug("数学块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.DISPLAY_MATH_BLOCK:
            try:
                if stripped == r'\]':
                    self._emit_math_block(tokens)
                elif stripped.endswith(r'\]') and len(stripped) > 2:
                    content_line = stripped[:-2].rstrip()
                    if content_line:
                        self._block_lines.append(content_line)
                    self._emit_math_block(tokens)
                else:
                    self._block_lines.append(line.rstrip('\n'))
            except Exception:
                _logger.debug("显示数学块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.MERMAID_BLOCK:
            try:
                if _is_code_fence_line(stripped):
                    self._emit_mermaid_block(tokens)
                else:
                    self._block_lines.append(line)
            except Exception:
                _logger.debug("Mermaid块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.DETAILS_BLOCK:
            try:
                self._feed_details_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Details块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.INDENTED_CODE:
            try:
                self._feed_indented_code_line(line, stripped, tokens)
            except Exception:
                _logger.debug("缩进代码块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.FENCED_DIV:
            try:
                self._feed_fenced_div_line(line, stripped, tokens)
            except Exception:
                _logger.debug("Fenced div块内行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

        elif self._state == _State.HTML_BLOCK:
            if self._is_html_close(stripped, self._block_html_tag):
                tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, "",
                                    {"tag": self._block_html_tag}))
                self._state = _State.NORMAL
            else:
                tokens.append(Token(TokenType.HTML_BLOCK_LINE,
                                    line.rstrip('\n')))

        elif self._state == _State.TABLE_ACTIVE:
            try:
                check = _strip_blockquote_prefix(stripped)
                if check != stripped:
                    if _is_table_row(check):
                        self._table_rows.append(_parse_table_row(check))
                    else:
                        self._emit_table(tokens)
                        self._state = _State.NORMAL
                        self._parse_normal_line(line, tokens)
                elif _is_table_row(stripped):
                    self._table_rows.append(_parse_table_row(stripped))
                else:
                    self._emit_table(tokens)
                    self._state = _State.NORMAL
                    self._parse_normal_line(line, tokens)
            except Exception:
                _logger.debug("表格活动状态行处理异常，降级为段落", exc_info=True)
                self._state = _State.NORMAL
                self._handle_paragraph_line(line, tokens)

    # ── 代码 fence 块内 ──────────────────────────────────

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
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        if fchar != self._block_fence_char:
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        if flen < self._block_fence_len:
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        close_lang = _get_fence_lang(stripped[flen:].strip())
        is_markdown = self._block_lang.lower() in ('markdown', 'md')
        if close_lang and is_markdown:
            self._block_nested_fence += 1
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        if not close_lang and is_markdown and self._block_nested_fence > 0:
            self._block_nested_fence -= 1
            self._emit_code_line(line.rstrip('\n'), tokens)
            return
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "",
                            {"lang": self._block_lang, "indented": False}))
        self._state = _State.NORMAL

    # ── Details 块内 ─────────────────────────────────────

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

    # ── 缩进代码块内 ────────────────────────────────────

    def _feed_indented_code_line(self, line: str, stripped: str, tokens: list[Token]):
        if _is_empty_line(stripped):
            self._emit_code_line("", tokens)
            return
        if line[:4] == '    ' or (line and line[0] == '\t'):
            content = line[4:] if line[:4] == '    ' else line[1:]
            self._emit_code_line(content.rstrip('\n'), tokens)
            return
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, "", {
            "lang": "text", "indented": True,
        }))
        self._state = _State.NORMAL
        self._parse_normal_line(line, tokens)

    # ═══════════════════════════════════════════════════════════
    # 块级语法尝试方法
    # ═══════════════════════════════════════════════════════════

    def _try_heading(self, stripped: str) -> Token | None:
        try:
            level = 0
            i = 0
            while i < len(stripped) and stripped[i] == '#':
                level += 1
                i += 1
            if level < 1 or level > 6:
                return None
            if i >= len(stripped):
                return None
            # ★ P0 修复: CommonMark 要求 # 后必须跟空格才构成 ATX 标题。
            #   #text（无空格）应降级为段落。
            if stripped[i] != ' ':
                return None
            text = stripped[i + 1:].strip()
            if not text:
                auto_id = self._generate_heading_id(text)
                return Token(TokenType.HEADING, '',
                             {"level": level, "id": auto_id})
            text = _rstrip_trailing_hashes(text)

            heading_id = ''
            attrs: dict | None = None
            if text.endswith('}'):
                brace_start = text.rfind('{')
                if brace_start >= 0:
                    raw = text[brace_start + 1:-1].strip()
                    if raw:
                        parsed = self._parse_heading_attrs(raw)
                        if parsed is not None:
                            heading_id = parsed.get('id', '')
                            attrs = parsed
                            text = text[:brace_start].strip()

            meta: dict = {"level": level}
            if heading_id:
                meta["id"] = heading_id
            else:
                auto_id = self._generate_heading_id(text)
                meta["id"] = auto_id
            if attrs:
                meta["attrs"] = attrs
            return Token(TokenType.HEADING, text, meta)
        except Exception:
            _logger.debug("_try_heading异常，返回None", exc_info=True)
            return None

    def _generate_heading_id(self, text: str) -> str:
        """从标题文本自动生成锚点 ID（带重复去重后缀 -1, -2...）。"""
        if not text:
            base = "section"
        else:
            result = text.lower()
            cleaned = []
            for ch in result:
                if ch.isalnum() or ch in ' -_':
                    cleaned.append(ch)
                else:
                    cleaned.append('-')
            result = ''.join(cleaned)
            result = result.replace(' ', '-')
            while '--' in result:
                result = result.replace('--', '-')
            result = result.strip('-')
            if len(result) > 80:
                result = result[:80].rstrip('-')
            base = result if result else "section"
        # Deduplication: append -1, -2, etc. for duplicate IDs
        if base in self._used_heading_ids:
            self._used_heading_ids[base] += 1
            return f"{base}-{self._used_heading_ids[base]}"
        else:
            self._used_heading_ids[base] = 0
            return base

    @staticmethod
    def _parse_heading_attrs(raw: str) -> dict | None:
        if not raw:
            return None

        def _split_attrs(s: str) -> list[str]:
            parts = []
            buf: list[str] = []
            quote = None
            for c in s:
                if c in '"\'' and quote is None:
                    quote = c
                    buf.append(c)
                elif c == quote:
                    quote = None
                    buf.append(c)
                elif c == ' ' and quote is None:
                    if buf:
                        parts.append(''.join(buf))
                        buf = []
                else:
                    buf.append(c)
            if buf:
                parts.append(''.join(buf))
            return parts

        result: dict = {}
        classes: list[str] = []
        for part in _split_attrs(raw):
            if not part:
                continue
            if part.startswith('#') and len(part) > 1:
                id_val = ''.join(c if c.isalnum() or c in '-_' else '_' for c in part[1:])
                result['id'] = id_val
            elif part.startswith('.') and len(part) > 1:
                cls = ''.join(c if c.isalnum() or c in '-_' else '_' for c in part[1:])
                classes.append(cls)
            elif '=' in part:
                key, _, val = part.partition('=')
                key = key.strip()
                val = val.strip()
                if key and val:
                    if len(val) >= 2 and val[0] in '"\'' and val[-1] == val[0]:
                        val = val[1:-1]
                    result[key] = val
        if classes:
            result['classes'] = classes
        return result if result else None

    @staticmethod
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
        self._preview_code_lines = []
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
                    self._emit_code_line(fence['extra'], tokens)
            self._emit_code_line("", tokens)
            return
        if stripped and stripped[0] in ('`', '~'):
            fchar, flen, _ = _get_fence_info(stripped)
            if fchar and flen >= fence['fence_len'] and fchar == fence['fence_char']:
                self._start_code_fence(fence, tokens)
                if fence.get('extra'):
                    if self._state == _State.MERMAID_BLOCK:
                        tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                    else:
                        self._emit_code_line(fence['extra'], tokens)
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
                    self._emit_code_line(fence['extra'], tokens)
            return
        if lang and lang in _MERMAID_KEYWORDS:
            fence['lang'] = 'mermaid'
            self._start_code_fence(fence, tokens)
            if fence.get('extra'):
                if self._state == _State.MERMAID_BLOCK:
                    tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
                else:
                    self._emit_code_line(fence['extra'], tokens)
            self._block_lines.append(stripped)
            return
        self._start_code_fence(fence, tokens)
        if fence.get('extra'):
            if self._state == _State.MERMAID_BLOCK:
                tokens.append(Token(TokenType.MERMAID_LINE, fence['extra']))
            else:
                tokens.append(Token(TokenType.CODE_LINE, fence['extra']))
        self._feed_code_fence_line(stripped + '\n', stripped, tokens)


__all__ = ["_BlockParserStreamMixin"]
