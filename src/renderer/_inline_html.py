"""_inline_html — _InlineParser HTML 标签解析 Mixin。

包含 HTML 标签、注释、实体解析的相关方法。
"""

from __future__ import annotations

from .inline_nodes import (
    InlineNode, TextNode, LineBreakNode, ImageNode,
    AbbrNode,
    _HTML_TAG_MAP,
    render_inline_to_text,
)
from ._utils import decode_html_entities


# HTML void 元素（自闭合标签）集合（统一定义于 _block_helpers.py）
from ._block_helpers import _VOID_HTML_TAGS


class InlineHTMLMixin:
    """_InlineParser HTML 标签解析 Mixin。

    提供以下方法：
      _try_html_entity()
      _try_html_comment()
      _try_html_tag()
      _skip_html_attrs()
      _skip_to_tag_end()
      _parse_html_content()
      _parse_html_content_nested()
      _make_img_node()
      _extract_attr()
    """

    # ── HTML 实体 ─────────────────────────────────────────

    def _try_html_entity(self) -> InlineNode | None:
        if self._text[self._pos] != '&':
            return None
        saved = self._pos
        self._pos += 1
        start = self._pos
        while self._pos < self._n and self._text[self._pos] not in (';', ' ', '\n', '\t'):
            self._pos += 1
        if self._pos >= self._n or self._text[self._pos] != ';':
            self._pos = saved
            return None
        entity = self._text[saved:self._pos + 1]
        self._pos += 1
        decoded = decode_html_entities(entity)
        if decoded != entity or len(entity) <= 2:
            return TextNode(content=decoded)
        self._pos = saved
        return None

    # ── HTML 内联标签 + 注释 ────────────────────────────

    def _try_html_comment(self) -> InlineNode | None:
        """尝试解析 HTML 注释 <!-- ... -->，返回空的 TextNode。"""
        if (self._pos + 3 < self._n
                and self._text[self._pos:self._pos + 4] == '<!--'):
            end = self._text.find('-->', self._pos + 4)
            if end >= 0:
                self._pos = end + 3
                return TextNode(content='')
        return None

    def _try_html_tag(self, depth: int) -> InlineNode | None:
        if self._text[self._pos] != '<':
            return None
        saved = self._pos
        self._pos += 1

        # HTML 注释 <!-- ... --> → 直接跳过
        if (self._pos < self._n
                and self._text[self._pos:self._pos + 3] == '!--'):
            end = self._text.find('-->', saved + 4)
            if end >= 0:
                self._pos = end + 3
                return TextNode(content='')
            self._pos = saved
            return None

        is_close = False
        if self._pos < self._n and self._text[self._pos] == '/':
            is_close = True
            self._pos += 1

        tag_start = self._pos
        while self._pos < self._n and (self._text[self._pos].isalnum()
                                        or self._text[self._pos] in '-:'):
            self._pos += 1
        tag_name = self._text[tag_start:self._pos].lower()

        if not tag_name:
            self._pos = saved
            return None

        # ★ P0 修复: 关闭 void 标签（如 </br>、</hr>、</img>）短路径返回 None，
        #   让文本原样输出，避免错误输出 LineBreakNode 或空 TextNode。
        if is_close and tag_name in _VOID_HTML_TAGS:
            self._pos = saved
            return None

        # 已知的 HTML void 元素（自闭合）→ 跳过属性直接返回空
        if tag_name in _VOID_HTML_TAGS:
            attr_start = self._pos
            self._skip_html_attrs()
            self._skip_to_tag_end()
            if tag_name == 'br':
                return LineBreakNode()
            if tag_name == 'hr':
                return LineBreakNode()
            if tag_name == 'img':
                end_before_gt = self._pos - 1
                attr_text = self._text[attr_start:end_before_gt].strip()
                return self._make_img_node(attr_text)
            return TextNode(content='')

        # 非 void 标签：必须在 _HTML_TAG_MAP 中注册
        if tag_name not in _HTML_TAG_MAP:
            self._pos = saved
            return None

        if is_close:
            self._skip_to_tag_end()
            return TextNode(content='')

        attr_start = self._pos
        self._skip_html_attrs()
        # 提取 title 属性（用于 <abbr title="...">）
        title = self._extract_attr(self._text[attr_start:self._pos].strip(), 'title')

        if self._pos >= self._n:
            self._pos = saved
            return None

        if self._text[self._pos] == '/':
            self._pos += 1
            if self._pos < self._n and self._text[self._pos] == '>':
                self._pos += 1
                return TextNode(content='')
            self._pos = saved
            return None

        if self._pos >= self._n or self._text[self._pos] != '>':
            self._pos = saved
            return None
        self._pos += 1

        node_cls, nestable = _HTML_TAG_MAP.get(tag_name, (None, False))

        if node_cls is None and nestable:
            # 可嵌套但无节点类的标签（如 span）：递归解析内部格式标记
            children = self._parse_html_content_nested(tag_name, depth)
            if children is not None:
                text = render_inline_to_text(children)
                return TextNode(content=text)
            self._pos = saved
            return None

        if node_cls is None:
            self._pos = saved
            return None

        if nestable:
            children = self._parse_html_content_nested(tag_name, depth)
            if children is not None:
                return self._make_nestable(node_cls, children)
        else:
            content = self._parse_html_content(tag_name, depth)
            if content is not None:
                if tag_name == 'abbr':
                    return AbbrNode(content=content, title=title)
                return node_cls(content=content)

        self._pos = saved
        return None

    def _skip_html_attrs(self) -> None:
        while self._pos < self._n:
            ch = self._text[self._pos]
            if ch in ('>', '/'):
                break
            if ch in ('"', "'"):
                quote = ch
                self._pos += 1
                while self._pos < self._n and self._text[self._pos] != quote:
                    if self._text[self._pos] == '\\':
                        self._pos += 1
                    self._pos += 1
                if self._pos < self._n:
                    self._pos += 1
            else:
                self._pos += 1

    def _skip_to_tag_end(self) -> None:
        while self._pos < self._n and self._text[self._pos] != '>':
            if self._text[self._pos] in ('"', "'"):
                quote = self._text[self._pos]
                self._pos += 1
                while self._pos < self._n and self._text[self._pos] != quote:
                    self._pos += 1
            self._pos += 1
        if self._pos < self._n:
            self._pos += 1

    def _parse_html_content(self, tag: str, depth: int) -> str | None:
        close_tag = f'</{tag}>'
        start = self._pos
        while self._pos < self._n:
            if self._try_match_str(close_tag):
                content = self._text[start:self._pos]
                self._pos += len(close_tag)
                return content
            self._pos += 1
        return None

    def _parse_html_content_nested(self, tag: str, depth: int
                                   ) -> list[InlineNode] | None:
        if depth > self._MAX_DEPTH:
            return None
        close_tag = f'</{tag}>'
        nodes: list[InlineNode] = []
        plain_buf: list[str] = []

        def _emit_plain():
            if plain_buf:
                nodes.append(TextNode(content=''.join(plain_buf)))
                plain_buf.clear()

        while self._pos < self._n:
            if self._try_match_str(close_tag):
                _emit_plain()
                self._pos += len(close_tag)
                return nodes
            node = self._try_format(depth + 1)
            if node is not None:
                _emit_plain()
                nodes.append(node)
            else:
                plain_buf.append(self._text[self._pos])
                self._pos += 1
        return None

    def _make_img_node(self, attr_text: str) -> ImageNode | None:
        src = self._extract_attr(attr_text, 'src')
        alt = self._extract_attr(attr_text, 'alt')
        if src:
            return ImageNode(content=alt or '', url=src)
        return TextNode(content=f'<img {attr_text}>')

    @staticmethod
    def _extract_attr(text: str, name: str) -> str:
        """提取 HTML 属性值，防止属性名子串匹配（如 src= 误匹配 data-src=）。"""
        pattern = f'{name}='
        idx = text.lower().find(pattern)
        while idx > 0:
            # 确保匹配的是完整属性名开头（前一个字符不是字母数字或连字符）
            prev_ch = text[idx - 1]
            if not (prev_ch.isalnum() or prev_ch == '-'):
                break
            idx = text.lower().find(pattern, idx + 1)
        if idx == -1:
            return ''
        idx += len(name) + 1
        if idx >= len(text):
            return ''
        if text[idx] in ('"', "'"):
            quote = text[idx]
            idx += 1
            end = text.find(quote, idx)
            return text[idx:end] if end > idx else ''
        end = idx
        while end < len(text) and not text[end].isspace() and text[end] not in '>':
            end += 1
        return text[idx:end]