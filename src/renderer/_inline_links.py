"""_inline_links — _InlineParser 链接与图片解析 Mixin。

包含链接、图片、自动链接、脚注引用的相关方法。
"""

from __future__ import annotations

import logging

from .inline_nodes import (
    InlineNode, TextNode, LinkNode, ImageNode,
    AutoLinkNode, AutoLinkEmailNode, FootnoteRefNode,
    render_inline_to_text,
)

_logger = logging.getLogger(__name__)


class InlineLinksMixin:
    """_InlineParser 链接与图片解析 Mixin。

    提供以下方法：
      _try_image()
      _try_footnote_ref()
      _try_link()
      _try_angle_autolink()
      _try_bare_url()
      _try_bare_email()
      _parse_link_url()
    """

    # ── 图片 ──────────────────────────────────────────────

    def _scan_url_with_parens(self) -> str | None:
        """扫描 URL，支持 <url> 和裸 URL（含括号平衡）。

        从 self._pos 开始扫描，前进 self._pos 到 URL 之后。
        返回 URL 字符串或 None（出错时）。
        """
        if self._pos >= self._n:
            return None
        url_start = self._pos
        if self._text[self._pos] == '<':
            self._pos += 1
            while self._pos < self._n and self._text[self._pos] != '>':
                self._pos += 1
            if self._pos >= self._n:
                return None
            url = self._text[url_start + 1:self._pos]
            self._pos += 1
            return url
        # 裸 URL：扫描到空格或平衡的 )
        paren_depth = 0
        while self._pos < self._n:
            ch = self._text[self._pos]
            if ch in ' \t\n' and paren_depth == 0:
                break
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                if paren_depth == 0:
                    break
                paren_depth -= 1
            self._pos += 1
        return self._text[url_start:self._pos]

    def _scan_title(self) -> str:
        """扫描可选的 title="..." 属性，跳过前导/尾随空白。

        从 self._pos 开始扫描，返回 title 字符串（可为空）。
        完成后 self._pos 指向 title 末尾空白之后。
        """
        while self._pos < self._n and self._text[self._pos] in ' \t':
            self._pos += 1
        if self._pos >= self._n or self._text[self._pos] not in '"\'':
            return ''
        quote = self._text[self._pos]
        self._pos += 1
        t_start = self._pos
        while self._pos < self._n and self._text[self._pos] != quote:
            self._pos += 1
        title = self._text[t_start:self._pos] if self._pos < self._n else ''
        if self._pos < self._n:
            self._pos += 1
        # 跳过尾随空白
        while self._pos < self._n and self._text[self._pos] in ' \t':
            self._pos += 1
        return title

    def _try_image(self, depth: int) -> InlineNode | None:
        try:
            if self._pos + 2 < self._n and self._text[self._pos:self._pos + 2] == '![':
                saved = self._pos
                self._pos += 2
                alt_start = self._pos
                while self._pos < self._n and self._text[self._pos] != ']':
                    self._pos += 1
                if self._pos >= self._n:
                    self._pos = saved
                    return None
                alt = self._text[alt_start:self._pos]
                self._pos += 1
                if self._pos < self._n and self._text[self._pos] == '(':
                    self._pos += 1
                    # ── 手动解析 URL + 可选尺寸 + 可选标题，兼容 =WxH 语法 ──
                    # 1) 解析 URL
                    url = self._scan_url_with_parens()
                    if not url:
                        self._pos = saved
                        return None

                    # 2) 跳过空白，尝试解析 =WxH 尺寸
                    width = height = 0
                    while self._pos < self._n and self._text[self._pos] in ' \t':
                        self._pos += 1

                    # 检查 =WxH 尺寸
                    if (self._pos + 3 < self._n
                            and self._text[self._pos] == '='):
                        xp = self._text.find('x', self._pos, self._pos + 10)
                        if xp > self._pos + 1:
                            w_str = self._text[self._pos + 1:xp]
                            if w_str.isdigit():
                                hp = xp + 1
                                while hp < self._n and self._text[hp].isdigit():
                                    hp += 1
                                if hp > xp + 1:
                                    h_str = self._text[xp + 1:hp]
                                    if h_str.isdigit():
                                        width = int(w_str)
                                        height = int(h_str)
                                        self._pos = hp

                    # 3) 解析可选的 title
                    title = self._scan_title()

                    # 4) 检查关闭 )
                    if self._pos >= self._n or self._text[self._pos] != ')':
                        self._pos = saved
                        return None
                    self._pos += 1

                    node = ImageNode(content=alt, url=url, title=title)
                    if width and height:
                        node.meta = {"width": width, "height": height}
                    return node
                elif self._pos < self._n and self._text[self._pos] == '[':
                    self._pos += 1
                    ref_start = self._pos
                    while self._pos < self._n and self._text[self._pos] != ']':
                        self._pos += 1
                    if self._pos >= self._n:
                        self._pos = saved
                        return None
                    ref_id = self._text[ref_start:self._pos]
                    self._pos += 1
                    return ImageNode(content=alt, url=f'[ref:{ref_id}]')
                else:
                    self._pos = saved
                    return None
            return None
        except Exception:
            _logger.debug("_try_image 异常，降级处理", exc_info=True)
            return None

    def _parse_link_url(self) -> tuple[str | None, str]:
        try:
            while self._pos < self._n and self._text[self._pos] in ' \t':
                self._pos += 1
            if self._pos >= self._n or self._text[self._pos] == ')':
                return None, ''
            url = self._scan_url_with_parens()
            if not url:
                return None, ''
            title = self._scan_title()
            if self._pos >= self._n or self._text[self._pos] != ')':
                return None, ''
            self._pos += 1
            return url, title
        except Exception:
            _logger.debug("_parse_link_url 异常，降级处理", exc_info=True)
            return None, ''

    # ── 脚注引用 ─────────────────────────────────────────

    def _try_footnote_ref(self) -> InlineNode | None:
        try:
            if (self._pos + 2 < self._n
                    and self._text[self._pos] == '['
                    and self._text[self._pos + 1] == '^'):
                saved = self._pos
                self._pos += 2
                ref_start = self._pos
                while self._pos < self._n and self._text[self._pos] not in '] \t\n\r':
                    self._pos += 1
                if self._pos >= self._n or self._text[self._pos] != ']':
                    self._pos = saved
                    return None
                ref_id = self._text[ref_start:self._pos]
                self._pos += 1
                return FootnoteRefNode(ref_id=ref_id)
            return None
        except Exception:
            _logger.debug("_try_footnote_ref 异常，降级处理", exc_info=True)
            return None

    # ── 链接 ──────────────────────────────────────────────

    def _try_link(self, depth: int) -> InlineNode | None:
        try:
            if self._text[self._pos] != '[':
                return None
            saved = self._pos
            self._pos += 1
            if self._pos < self._n and self._text[self._pos] == '^':
                self._pos = saved
                return None
            text_start = self._pos
            bracket_depth = 0
            while self._pos < self._n:
                ch = self._text[self._pos]
                if ch == '[':
                    bracket_depth += 1
                elif ch == ']':
                    if bracket_depth == 0:
                        break
                    bracket_depth -= 1
                self._pos += 1
            if self._pos >= self._n:
                self._pos = saved
                return None
            link_text = self._text[text_start:self._pos]
            self._pos += 1
            if self._pos >= self._n:
                self._pos = saved
                return None
            if self._text[self._pos] == '(':
                self._pos += 1
                url, title = self._parse_link_url()
                if url is None:
                    self._pos = saved
                    return None
                inner_parser = self.__class__(link_text)
                children = inner_parser.parse()
                link_content = render_inline_to_text(children)
                return LinkNode(url=url, content=link_content, children=children, title=title)
            elif self._text[self._pos] == '[':
                self._pos += 1
                ref_start = self._pos
                while self._pos < self._n and self._text[self._pos] != ']':
                    self._pos += 1
                if self._pos >= self._n:
                    self._pos = saved
                    return None
                ref_id = self._text[ref_start:self._pos]
                self._pos += 1
                # ★ 修复：为参考式链接解析 children，确保渲染时能看到链接文字
                inner_parser = self.__class__(link_text)
                children = inner_parser.parse()
                link_content = render_inline_to_text(children)
                return LinkNode(url=f'[ref:{ref_id}]', content=link_content, children=children)
            else:
                self._pos = saved
                return None
        except Exception:
            _logger.debug("_try_link 异常，降级处理", exc_info=True)
            return None

    # ── 尖括号自动链接 ──────────────────────────────────

    def _try_angle_autolink(self) -> InlineNode | None:
        try:
            if self._text[self._pos] != '<':
                return None
            saved = self._pos
            self._pos += 1
            content_start = self._pos
            while self._pos < self._n and self._text[self._pos] != '>':
                if self._text[self._pos] == '<':
                    self._pos = saved
                    return None
                self._pos += 1
            if self._pos >= self._n:
                self._pos = saved
                return None
            content = self._text[content_start:self._pos]
            self._pos += 1
            if any(content.startswith(p) for p in ('http://', 'https://', 'ftp://', 'ftps://')):
                return AutoLinkNode(url=content, content=content)
            if '@' in content and '.' in content:
                at_idx = content.index('@')
                if at_idx > 0 and at_idx < len(content) - 1:
                    domain = content[at_idx + 1:]
                    if ('.' in domain and not domain.startswith('.')
                            and not content.startswith('.') and not content.endswith('.')):
                        return AutoLinkEmailNode(email=content, content=content)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_angle_autolink 异常，降级处理", exc_info=True)
            return None

    # ── 裸 URL ──────────────────────────────────────────

    _URL_PROTOCOLS: tuple[str, ...] = ('https://', 'http://', 'ftp://', 'ftps://')

    @staticmethod
    def _balance_url_parens(url: str) -> tuple[str, int]:
        """从 URL 末尾剥离多余的右括号，保留平衡的括号。

        Returns:
            (balanced_url, chars_removed)
        """
        chars_removed = 0
        open_count = url.count('(')
        close_count = url.count(')')
        stripped = url
        while stripped and close_count > open_count:
            if stripped[-1] == ')':
                stripped = stripped[:-1]
                chars_removed += 1
                close_count -= 1
            else:
                break
        # 再剥离尾部常规标点
        while stripped and stripped[-1] in '.,;:!?\'"':
            stripped = stripped[:-1]
            chars_removed += 1
        return stripped, chars_removed

    def _try_bare_url(self) -> InlineNode | None:
        try:
            ch = self._text[self._pos]
            if ch not in 'hHfFwW':
                return None
            has_scheme = (ch not in 'hHfF' or
                          self._text.find('://', self._pos, min(self._pos + 10, self._n)) != -1)
            if has_scheme:
                for protocol in self._URL_PROTOCOLS:
                    plen = len(protocol)
                    if (self._pos + plen <= self._n
                            and self._text[self._pos:self._pos + plen].lower() == protocol):
                        saved = self._pos
                        self._pos += len(protocol)
                        url_start = self._pos
                        while (self._pos < self._n
                               and not self._text[self._pos].isspace()
                               and self._text[self._pos] not in '<>"\'[]{},，。、！？；：】》》）—–—'):
                            if self._text[self._pos] in ',.!?;:' and self._pos + 1 < self._n and self._text[self._pos + 1].isspace():
                                break
                            self._pos += 1
                        url = self._text[saved:self._pos]
                        # 括号平衡处理：剥离多余右括号
                        balanced_url, removed = self._balance_url_parens(url)
                        self._pos -= removed
                        url = balanced_url
                        if len(url) > len(protocol):
                            return AutoLinkNode(url=url, content=url)
                        self._pos = saved
                        return None
            _www_prefix = 'www.'
            if (self._pos + len(_www_prefix) <= self._n
                    and self._text[self._pos:self._pos + len(_www_prefix)].lower() == _www_prefix):
                saved = self._pos
                self._pos += len(_www_prefix)
                url_start = self._pos
                while (self._pos < self._n
                       and not self._text[self._pos].isspace()
                       and self._text[self._pos] not in '<>"\'[]{},，。、！？；：】》》）—–—'):
                    if self._text[self._pos] in ',.!?;:' and self._pos + 1 < self._n and self._text[self._pos + 1].isspace():
                        break
                    self._pos += 1
                url = self._text[saved:self._pos]
                # 括号平衡处理：剥离多余右括号
                balanced_url, removed = self._balance_url_parens(url)
                self._pos -= removed
                url = balanced_url
                dot_count = 0
                for ch in url:
                    if ch == '.':
                        dot_count += 1
                if len(url) > len(_www_prefix) and dot_count >= 1:
                    return AutoLinkNode(url=f'http://{url}', content=url)
                self._pos = saved
                return None
            return None
        except Exception:
            _logger.debug("_try_bare_url 异常，降级处理", exc_info=True)
            return None

    # ── 裸 Email ────────────────────────────────────────

    _EMAIL_LOCAL_MAX_SCAN = 64

    def _try_bare_email(self) -> InlineNode | None:
        try:
            if self._pos >= self._n or self._text[self._pos] != '@':
                return None
            saved = self._pos
            local_start = self._pos
            _local_count = 0
            while local_start > 0 and _local_count < self._EMAIL_LOCAL_MAX_SCAN:
                ch = self._text[local_start - 1]
                if ch.isalnum() or ch in '._%+-':
                    local_start -= 1
                    _local_count += 1
                else:
                    break
            if _local_count == 0:
                return None
            self._pos += 1
            domain_start = self._pos
            while (self._pos < self._n
                   and (self._text[self._pos].isalnum()
                        or self._text[self._pos] in '.-')):
                # ☆ 注意: 已移除原先冗余的 `and self._text[self._pos] not in '])>'`，
                #    因为 isalnum() 或 in '.-' 的字符不可能同时是 ])> 之一。
                self._pos += 1
            if self._pos - domain_start < 3 or '.' not in self._text[domain_start:self._pos]:
                self._pos = saved
                return None
            addr = self._text[local_start:self._pos]
            stripped_addr = addr.rstrip('.,;:!?)\'"')
            self._pos -= (len(addr) - len(stripped_addr))
            addr = stripped_addr
            if '@' in addr and '.' in addr[addr.index('@') + 1:]:
                return AutoLinkEmailNode(email=addr, content=addr)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_bare_email 异常，降级处理", exc_info=True)
            return None
