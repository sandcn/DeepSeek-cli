"""inline_parser — 内联 Markdown 递归下降解析器（无正则表达式）。

从 recursive_parser.py 拆分而来，包含 _InlineParser 及其辅助函数。
纯字符级扫描，无任何正则表达式。

原位置：recursive_parser.py（第298-1225行）

拆分说明：
  - _inline_html.py       — HTML 标签/注释/实体解析 Mixin
  - _inline_links.py      — 链接/图片/自动链接/脚注解析 Mixin
  - _inline_formatting.py — 粗体/斜体/删除线/高亮/上下标解析 Mixin
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

from .inline_nodes import (
    InlineNode, TextNode,
    InlineCodeNode, InlineMathNode,
    LineBreakNode,
    render_inline_to_text,
)
from .emoji_map import EMOJI_MAP

from ._inline_html import InlineHTMLMixin
from ._inline_links import InlineLinksMixin
from ._inline_formatting import InlineFormattingMixin


# ═══════════════════════════════════════════════════════════
# 内联解析器（真正的递归下降，无正则）
# ═══════════════════════════════════════════════════════════

class _InlineParser(InlineHTMLMixin, InlineLinksMixin, InlineFormattingMixin):
    """真正的递归下降内联 Markdown 解析器（字符级扫描，无正则）。"""
    _MAX_DEPTH = 20
    _FORMAT_CHARS: frozenset[str] = frozenset('\\`:*_~=$[<!&^@hHfFwW+|{%')
    _URL_PROTOCOLS: tuple[str, ...] = ('https://', 'http://', 'ftp://', 'ftps://')

    def __init__(self, text: str):
        self._text = text
        self._pos = 0
        self._n = len(text)

    @staticmethod
    def _make_nestable(cls: type, children: list[InlineNode]) -> InlineNode:
        text = render_inline_to_text(children)
        return cls(content=text, children=children)

    def parse(self) -> list[InlineNode]:
        nodes, _ = self._parse_until('', 0)
        return nodes

    def _find_next_format_char(self, start: int, end: int) -> int:
        """找到下一个格式字符的位置（O(n) 单遍扫描）。"""
        pos = start
        while pos < end:
            ch = self._text[pos]
            if ch in self._FORMAT_CHARS:
                return pos
            pos += 1
        return end

    def _parse_until(self, close_delim: str,
                     depth: int = 0) -> tuple[list[InlineNode], bool]:
        if depth > self._MAX_DEPTH:
            return [TextNode(content=self._text[self._pos:])], False

        nodes: list[InlineNode] = []
        plain_buf: list[str] = []
        close_len = len(close_delim)

        def _emit_plain():
            if plain_buf:
                nodes.append(TextNode(content=''.join(plain_buf)))
                plain_buf.clear()

        while self._pos < self._n:
            if close_len > 0 and self._try_match_str(close_delim):
                _emit_plain()
                return nodes, True

            ch = self._text[self._pos]
            if ch not in self._FORMAT_CHARS:
                search_end = self._n
                if close_delim:
                    close_pos = self._text.find(close_delim, self._pos)
                    if close_pos >= 0 and close_pos < search_end:
                        search_end = close_pos
                fmt_pos = self._find_next_format_char(self._pos, search_end)
                if fmt_pos > self._pos:
                    plain_buf.append(self._text[self._pos:fmt_pos])
                    self._pos = fmt_pos
                    continue
                plain_buf.append(self._text[self._pos:])
                self._pos = self._n
                continue

            pos_before = self._pos
            try:
                node = self._try_format(depth)
            except Exception:
                node = None
            if node is not None:
                # ★ 修复（review 方向）：裸邮箱检测从 '@' 向前回溯局部部分，
                #   这些字符此前已被缓冲进 plain_buf——emit 前按局部部分长度
                #   截掉，避免 "foo@bar.com" 渲染为 "foofoo@bar.com"（仅当文本
                #   同时含其他行内标记时走本路径）。
                local_start = getattr(self, '_last_email_local_start', None)
                if (local_start is not None
                        and type(node).__name__ == 'AutoLinkEmailNode'):
                    self._trim_plain_buf(plain_buf, pos_before - local_start)
                self._last_email_local_start = None
                _emit_plain()
                nodes.append(node)
                continue

            plain_buf.append(self._text[self._pos])
            self._pos += 1

        _emit_plain()
        return nodes, False

    @staticmethod
    def _trim_plain_buf(plain_buf: list[str], n: int) -> None:
        """从 plain_buf 尾部截掉 n 个字符（跨字符串条目）。"""
        while n > 0 and plain_buf:
            s = plain_buf[-1]
            take = min(n, len(s))
            if take >= len(s):
                plain_buf.pop()
            else:
                plain_buf[-1] = s[:-take]
            n -= take

    def _try_format(self, depth: int) -> InlineNode | None:
        try:
            self._last_email_local_start = None
            ch = self._text[self._pos] if self._pos < self._n else ''

            entries = self._METHOD_CACHE.get(ch)
            if entries is not None:
                for method, needs_depth in entries:
                    if needs_depth:
                        node = method(self, depth)
                    else:
                        node = method(self)
                    if node is not None:
                        return node
                return None

            # ── 裸 URL 检测：仅在字母 h/f/w 且紧跟 :// 或 www. 前缀时触发 ──
            if ch in 'hHfFwW' and self._pos + 5 < self._n:
                c = self._text[self._pos]
                is_url_candidate = False
                if c in 'hH' and self._pos + 7 <= self._n:
                    is_url_candidate = (self._text[self._pos:self._pos+5].lower() == 'http:' or
                                        self._text[self._pos:self._pos+6].lower() == 'https:')
                elif c in 'fF' and self._pos + 6 <= self._n:
                    is_url_candidate = (self._text[self._pos:self._pos+4].lower() == 'ftp:' or
                                        self._text[self._pos:self._pos+5].lower() == 'ftps:')
                elif c in 'wW' and self._pos + 4 <= self._n:
                    is_url_candidate = self._text[self._pos:self._pos+4].lower() == 'www.'

                if is_url_candidate and (node := self._try_bare_url()):
                    return node

            return None
        except Exception:
            _logger.debug("_try_format 异常，跳过格式标记", exc_info=True)
            self._pos += 1
            return None

    def _try_match_str(self, s: str) -> bool:
        if self._pos + len(s) <= self._n:
            return self._text[self._pos:self._pos + len(s)] == s
        return False

    # ── 转义 ──────────────────────────────────────────────

    def _try_escape(self) -> InlineNode | None:
        try:
            if self._pos + 1 < self._n and self._text[self._pos] == '\\':
                ch = self._text[self._pos + 1]
                if ch in r'\`*_{}[]()#+-.!~^<>$=:|':
                    self._pos += 2
                    return TextNode(content=ch)
            return None
        except Exception:
            _logger.debug("_try_escape 异常，降级处理", exc_info=True)
            return None

    # ── 行内代码 ─────────────────────────────────────────

    def _try_inline_code(self) -> InlineNode | None:
        try:
            if self._text[self._pos] != '`':
                return None
            saved = self._pos
            self._pos += 1
            is_double = False
            if self._pos < self._n and self._text[self._pos] == '`':
                is_double = True
                self._pos += 1
            content_start = self._pos
            delim = '``' if is_double else '`'
            found = False
            while self._pos < self._n:
                if self._try_match_str(delim):
                    found = True
                    break
                self._pos += 1
            if not found:
                self._pos = saved
                return None
            content = self._text[content_start:self._pos]
            self._pos += len(delim)
            return InlineCodeNode(content=content)
        except Exception:
            _logger.debug("_try_inline_code 异常，降级处理", exc_info=True)
            return None

    # ── \(行内数学\) ──────────────────────────────────

    def _try_paren_math(self) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos:self._pos + 2] == r'\('):
                saved = self._pos
                self._pos += 2
                content_start = self._pos
                while self._pos < self._n:
                    if (self._pos + 1 < self._n
                            and self._text[self._pos:self._pos + 2] == r'\)'):
                        content = self._text[content_start:self._pos]
                        self._pos += 2
                        return InlineMathNode(content=content)
                    self._pos += 1
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_paren_math 异常，降级处理", exc_info=True)
            return None

    # ── $行内数学$ ─────────────────────────────────────

    def _try_inline_math(self) -> InlineNode | None:
        try:
            saved = self._pos
            if self._text[self._pos] == '$':
                if self._pos + 1 < self._n and self._text[self._pos + 1] == '$':
                    return None
                self._pos += 1
                content_start = self._pos
                while self._pos < self._n:
                    if self._text[self._pos] == '$' and not (self._pos + 1 < self._n
                                                              and self._text[self._pos + 1] == '$'):
                        content = self._text[content_start:self._pos]
                        self._pos += 1
                        return InlineMathNode(content=content)
                    self._pos += 1
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_inline_math 异常，降级处理", exc_info=True)
            return None

    # ── Emoji ────────────────────────────────────────────

    def _try_emoji(self) -> InlineNode | None:
        try:
            if self._text[self._pos] != ':':
                return None
            saved = self._pos
            self._pos += 1
            name_start = self._pos
            while (self._pos < self._n
                   and (self._text[self._pos].isalnum()
                        or self._text[self._pos] in '_-+')):
                self._pos += 1
            if self._pos < self._n and self._text[self._pos] == ':':
                name = self._text[name_start:self._pos]
                full = f':{name}:'
                if full in EMOJI_MAP:
                    self._pos += 1
                    return TextNode(content=EMOJI_MAP[full])
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_emoji 异常，降级处理", exc_info=True)
            return None

    # ── <br> 换行 ──────────────────────────────────────

    def _try_line_break(self) -> InlineNode | None:
        """尝试解析 <br> / <br/> / <br /> 换行标签。"""
        try:
            # 检查至少有 4 个字符 <br> 或 <br/
            if self._pos + 3 < self._n:
                lower4 = self._text[self._pos:self._pos + 4].lower()
                if lower4 in ('<br>', '<br/'):
                    self._pos += 4
                    if self._pos < self._n and self._text[self._pos] == '>':
                        self._pos += 1
                    return LineBreakNode()
                # <br /> 带空格的自闭合
                if lower4 == '<br ' and self._pos + 5 < self._n                         and self._text[self._pos + 4:self._pos + 6] == '/>':
                    self._pos += 6
                    return LineBreakNode()
            return None
        except Exception:
            _logger.debug("_try_line_break 异常，降级处理", exc_info=True)
            return None


# ── 字符级格式调度表（类属性，避免每次调用重新构造） ──
_InlineParser._FORMAT_DISPATCH = {
    '\\': (('_try_paren_math', False), ('_try_escape', False)),
    '`':  (('_try_inline_code', False),),
    ':':  (('_try_emoji', False),),
    '*':  (('_try_bold_italic', True), ('_try_bold', True), ('_try_italic', True)),
    '_':  (('_try_bold_italic', True), ('_try_bold', True), ('_try_italic', True)),
    '~':  (('_try_strikethrough', True), ('_try_subscript', True)),
    '=':  (('_try_highlight', True),),
    '$':  (('_try_inline_math', False),),
    '[':  (('_try_footnote_ref', False), ('_try_wikilink', True), ('_try_link', True)),
    '<':  (('_try_line_break', False), ('_try_angle_autolink', False), ('_try_html_tag', True), ('_try_html_comment', False)),
    '!':  (('_try_image', True),),
    '&':  (('_try_html_entity', False),),
    '^':  (('_try_superscript', True),),
    '@':  (('_try_bare_email', False),),
    '+':  (('_try_underline', True),),
    '|':  (('_try_spoiler', True),),
    '{':  (('_try_critic_addition', True), ('_try_critic_deletion', True),
            ('_try_critic_substitution', True), ('_try_critic_comment', True),
            ('_try_small_text', True), ('_try_color_text', True)),
    '%':  (('_try_inline_comment', True),),
}

# ★ 优化：将 _FORMAT_DISPATCH 中的方法名预解析为实际方法引用
_InlineParser._METHOD_CACHE: dict[str, list[tuple]] = {}
for ch, entries in _InlineParser._FORMAT_DISPATCH.items():
    resolved = []
    for method_name, needs_depth in entries:
        resolved.append((getattr(_InlineParser, method_name), needs_depth))
    _InlineParser._METHOD_CACHE[ch] = resolved




