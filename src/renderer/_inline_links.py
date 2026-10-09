"""_inline_links — _InlineParser 链接与图片解析 Mixin。

包含链接、图片、自动链接、脚注引用的相关方法。
"""

from __future__ import annotations

import logging

from .inline_nodes import (
    InlineNode, LinkNode, ImageNode,
    AutoLinkNode, AutoLinkEmailNode, FootnoteRefNode,
    SpanNode, CitationNode,
    render_inline_to_text,
)

_logger = logging.getLogger(__name__)


def _split_attr_tokens(body: str) -> list[str]:
    """属性块内按空白分隔 token（引号内的空白不作为分隔符）。

    供 ``{.class #id key="a b"}`` 解析使用：``title="a b"`` 保持为单个 token。
    """
    tokens: list[str] = []
    buf: list[str] = []
    quote = ''
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ''
            continue
        if ch in '"\'':
            quote = ch
            buf.append(ch)
            continue
        if ch.isspace():
            if buf:
                tokens.append(''.join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append(''.join(buf))
    return tokens


def _apply_media_attrs(attrs: dict, meta: dict) -> None:
    """把行内属性块写入媒体 ``meta``（``width`` / ``height`` / ``title``）。

    尺寸值支持 ``100`` / ``100px`` / ``50%``（百分比解析为整数 50 忽略 ``%``，
    终端按字符宽度近似）；非法值忽略（不写入，不丢其它属性）。
    """
    kv = attrs.get("attrs") or {}
    for key in ("width", "height"):
        if key not in kv:
            continue
        raw = str(kv[key]).strip()
        raw = raw.removesuffix("px").removesuffix("%").strip()
        try:
            meta[key] = int(float(raw))
        except (TypeError, ValueError):
            continue
    if "title" in kv:
        meta["title"] = kv["title"]


#: 引用 key 允许出现的字符（字母数字 + 常见分隔符：作者-年份 / DOI）
_CITATION_KEY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:/+#"
)


def _parse_citation_keys(link_text: str) -> list[str]:
    """``@key`` / ``[-@key]`` / ``[@a; @b, p. 3]`` → key 列表（``-`` 前缀保留）。

    逐个 ``@`` 起点向后收集 key 字符，直到遇到空白/逗号/分号/``]``。
    无有效 key（``@`` 后无字符）时返回空列表（调用方回退普通链接路径）。
    """
    keys: list[str] = []
    i = 0
    n = len(link_text)
    while i < n:
        if link_text[i] != '@':
            i += 1
            continue
        suppress = i > 0 and link_text[i - 1] == '-'
        j = i + 1
        while j < n and link_text[j] in _CITATION_KEY_CHARS:
            j += 1
        key = link_text[i + 1:j]
        if not key:
            i += 1
            continue
        keys.append(('-' if suppress else '') + key)
        i = j
    return keys


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
        """扫描可选的链接标题，跳过前导/尾随空白。

        支持 CommonMark 三种定界：双引号 ``"..."``、单引号 ``'...'`` 与
        圆括号 ``(...)``（修复前仅支持前两种，``[t](url (title))`` 因 URL
        扫描后被 ``(`` 卡住而整体解析失败、原样泄漏）。

        从 self._pos 开始扫描，返回标题字符串（可为空；无标题/未闭合时
        位置不变）。完成后 self._pos 指向标题末尾空白之后。
        """
        while self._pos < self._n and self._text[self._pos] in ' \t':
            self._pos += 1
        if self._pos >= self._n:
            return ''
        opener = self._text[self._pos]
        if opener in '"\'':
            closer = opener
        elif opener == '(':
            # 圆括号标题（CommonMark）：内部不允许未转义的 ``(``
            closer = ')'
        else:
            return ''
        title_start = self._pos
        self._pos += 1
        t_start = self._pos
        while self._pos < self._n and self._text[self._pos] != closer:
            if closer == ')' and self._text[self._pos] == '(':
                self._pos = title_start
                return ''
            self._pos += 1
        if self._pos >= self._n:
            # 未闭合：回退到标题之前（后续按普通文本处理，不吞字符）
            self._pos = title_start
            return ''
        title = self._text[t_start:self._pos]
        self._pos += 1
        # 跳过尾随空白
        while self._pos < self._n and self._text[self._pos] in ' \t':
            self._pos += 1
        return title

    def _parse_inline_attrs(self) -> dict | None:
        """解析 ``{.class #id key=value}`` 行内属性块（Pandoc 属性语法）。

        仅在 ``self._pos`` 处为 ``{`` 且内容构成**至少一个**有效属性时成功，
        前进位置并返回 ``{"classes": [...], "id": str, "attrs": {...}}``；否则
        位置不变、返回 ``None``（不吞掉普通 ``{`` 文本，与 ``{color:red}``
        等既有花括号语法不冲突——本语法要求属性形如 ``.cls`` / ``#id`` /
        ``key=value``，含 ``:`` 的 ``{color:red}`` 不满足）。
        """
        if self._pos >= self._n or self._text[self._pos] != '{':
            return None
        end = self._text.find('}', self._pos + 1)
        if end < 0 or end - self._pos > 512:
            return None
        body = self._text[self._pos + 1:end]
        if not body.strip():
            return None
        classes: list[str] = []
        ident = ''
        attrs: dict = {}
        for token in _split_attr_tokens(body):
            if not token:
                continue
            if token.startswith('.'):
                cls = token[1:]
                if cls and cls not in classes:
                    classes.append(cls)
            elif token.startswith('#'):
                ident = token[1:]
            elif '=' in token:
                key, _, value = token.partition('=')
                key = key.strip().lower()
                value = value.strip().strip('"\'')
                if key:
                    attrs[key] = value
            else:
                # 非属性 token（如 ``color:red``）→ 非本语法
                return None
        if not (classes or ident or attrs):
            return None
        self._pos = end + 1
        return {"classes": classes, "id": ident, "attrs": attrs}

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

                    # 5) 可选的行内属性块 ``{width=.. height=.. title=..}``
                    #    （Pandoc image attributes）——修复前直接泄漏为正文。
                    attrs = self._parse_inline_attrs()
                    meta: dict = {}
                    if attrs:
                        _apply_media_attrs(attrs, meta)
                        title = meta.pop("title", title)
                        width = meta.pop("width", width)
                        height = meta.pop("height", height)
                    node = ImageNode(content=alt, url=url, title=title)
                    if width or height:
                        meta["width"] = width
                        meta["height"] = height
                    if meta:
                        node.meta = meta
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
                    # ★ 折叠引用式图片 ``![alt][]``（CommonMark collapsed
                    #   reference image）：ref_id 为空时以 alt 文本为标签
                    #   ——与折叠引用式链接 ``[text][]`` 同一规则。修复前
                    #   ref_id 恒为空 → url 占位 ``[ref:]``，即便文档中定义了
                    #   ``[alt]: url`` 也渲染为 ``🖼️ alt ([ref:])``（永不展开）。
                    collapsed = False
                    if not ref_id:
                        ref_id = alt.strip()
                        collapsed = True
                        if not ref_id or '\n' in ref_id:
                            self._pos = saved
                            return None
                    node = ImageNode(content=alt, url=f'[ref:{ref_id}]')
                    if collapsed:
                        node.meta = {'shortcut': True, 'collapsed': True}
                    return node
                else:
                    # ★ 快捷引用式图片 ``![alt]``（CommonMark shortcut reference
                    #   image）：``![alt]`` 后不跟 ``(`` / ``[`` → 以 alt 为标签
                    #   查引用定义表。修复前直接返回 None（按普通文本渲染），
                    #   与已支持的快捷引用式链接 ``[ref]`` 不对称——文档定义
                    #   ``[alt]: url`` 时图片不展开。未命中定义时由渲染层回退
                    #   原文 ``![alt]``（与链接未命中保留方括号文本一致）。
                    ref_id = alt.strip()
                    if ref_id and len(ref_id) <= 256 and '\n' not in ref_id:
                        node = ImageNode(content=alt, url=f'[ref:{ref_id}]')
                        node.meta = {'shortcut': True}
                        return node
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
            # ── 行内属性 span ``[文本]{.class #id key=val}``（Pandoc span）──
            #    ``]`` 后紧跟属性块时，解析为带样式的 SpanNode（内容仍按行内
            #    Markdown 解析）；属性块无效（如 ``{color:red}`` / ``{b}``）
            #    时位置不变，继续走链接/快捷引用判定（不误吞普通花括号文本）。
            if self._pos < self._n and self._text[self._pos] == '{':
                attrs = self._parse_inline_attrs()
                if attrs is not None:
                    inner_parser = self.__class__(link_text)
                    children = inner_parser.parse()
                    return SpanNode(
                        content=render_inline_to_text(children),
                        children=children, meta=attrs,
                    )
            # ── 引用 citation ``[@key]`` / ``[-@key]`` / ``[@a; @b]`` ──
            #    （Pandoc citation）：无文献数据库，渲染为引用标记；
            #    修复前落入快捷引用式链接分支、未命中定义即原样回退。
            #    后随 ``(`` / ``[`` 时是普通链接（``[@k](url)`` /
            #    ``[@k][ref]``），不按引用解析。
            if ((link_text.startswith('@') or link_text.startswith('-@'))
                    and (self._pos >= self._n
                         or self._text[self._pos] not in '([')):
                keys = _parse_citation_keys(link_text)
                if keys:
                    return CitationNode(content=link_text,
                                        meta={"keys": keys})
            # ★ 修复（行尾快捷引用式链接）：``]`` 是文本最后一个字符时
            #   （``原文 [docs]``）此处原先直接返回 None，导致行尾的
            #   ``[ref]`` 不被解析为快捷引用式链接（文档中定义了
            #   ``[docs]: url`` 也不展开），而同一语法在行中/行首位置正常
            #   展开——同一语法两种结果。现不再提前返回：后续 ``(`` / ``[``
            #   判断带边界检查，均不匹配时落入 else 的快捷引用分支。
            if self._pos < self._n and self._text[self._pos] == '(':
                self._pos += 1
                url, title = self._parse_link_url()
                if url is None:
                    self._pos = saved
                    return None
                inner_parser = self.__class__(link_text)
                children = inner_parser.parse()
                link_content = render_inline_to_text(children)
                return LinkNode(url=url, content=link_content, children=children, title=title)
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
                collapsed = False
                # ★ 折叠引用式链接 ``[text][]``：ref_id 为空时用链接文字作 ref_id
                if not ref_id:
                    ref_id = link_text.strip()
                    collapsed = True
                    if not ref_id:
                        self._pos = saved
                        return None
                # ★ 修复：为参考式链接解析 children，确保渲染时能看到链接文字
                inner_parser = self.__class__(link_text)
                children = inner_parser.parse()
                link_content = render_inline_to_text(children)
                node = LinkNode(url=f'[ref:{ref_id}]', content=link_content,
                                children=children)
                if collapsed:
                    node.meta = {'shortcut': True, 'collapsed': True}
                return node
            else:
                # ── 快捷引用式链接 ``[ref]``（引用定义在文档其他位置）──
                # 解析为带 ``[ref:...]`` 的 LinkNode；渲染层查 ref_map 命中则
                # 展开为链接 + URL，未命中回退显示原文 ``[ref]``。
                ref_id = link_text.strip()
                if ref_id and len(ref_id) <= 256 and '\n' not in ref_id:
                    inner_parser = self.__class__(link_text)
                    children = inner_parser.parse()
                    node = LinkNode(url=f'[ref:{ref_id}]', content=link_text,
                                    children=children)
                    node.meta = {'shortcut': True}
                    return node
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
                # ★ 修复（review 方向）：记录局部部分起点供 _parse_until 截掉
                #   已缓冲的局部字符（"foo" 已在 plain_buf），避免重复渲染。
                self._last_email_local_start = local_start
                return AutoLinkEmailNode(email=addr, content=addr)
            self._pos = saved
            return None
        except Exception:
            _logger.debug("_try_bare_email 异常，降级处理", exc_info=True)
            return None
