"""_inline_html — _InlineParser HTML 标签解析 Mixin。

包含 HTML 标签、注释、实体解析的相关方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .inline_nodes import (
    InlineNode, TextNode, LineBreakNode, ImageNode,
    AbbrNode, ColorTextNode, LinkNode, MathMLNode,
    _HTML_TAG_MAP,
    render_inline_to_text,
)
from ._utils import decode_html_entities


def _extract_style_color(attr_text: str) -> str:
    """从 HTML ``style="..."`` 属性提取 ``color`` 值（小写；无则空串）。

    仅识别独立的 ``color`` 声明（``background-color`` / ``-color`` 等前缀不
    算）；值支持颜色名（``red``）与 ``#rrggbb``（渲染层转换为 256 色）。
    """
    if not attr_text:
        return ''
    low = attr_text.lower()
    i = low.find('color')
    while i >= 0:
        prev_ok = i == 0 or not (low[i - 1].isalnum() or low[i - 1] in '-_')
        j = i + 5
        while j < len(low) and low[j].isspace():
            j += 1
        if prev_ok and j < len(low) and low[j] == ':':
            j += 1
            while j < len(low) and low[j].isspace():
                j += 1
            k = j
            while k < len(low) and (low[k].isalnum() or low[k] in '#(),%.-'):
                k += 1
            value = low[j:k].strip()
            if value:
                return value
        i = low.find('color', i + 5)
    return ''


def _extract_style_background(attr_text: str) -> str:
    """从 HTML ``style="..."`` 属性提取背景色（``background-color`` / ``background``）。

    与 ``_extract_style_color`` 同口径：仅识别独立声明（``background-color``
    优先于 ``background``），值支持颜色名与 ``#rrggbb``；无则返回空串。
    """
    if not attr_text:
        return ''
    low = attr_text.lower()
    i = low.find('background-color')
    if i < 0:
        i = low.find('background')
    while i >= 0:
        prev_ok = i == 0 or not (low[i - 1].isalnum() or low[i - 1] in '-_')
        j = i + (len('background-color') if low.startswith('background-color', i)
                 else len('background'))
        while j < len(low) and low[j].isspace():
            j += 1
        if prev_ok and j < len(low) and low[j] == ':':
            j += 1
            while j < len(low) and low[j].isspace():
                j += 1
            k = j
            while k < len(low) and (low[k].isalnum() or low[k] in '#(),%.-'):
                k += 1
            value = low[j:k].strip()
            if value:
                return value
        i = low.find('background', i + len('background'))
    return ''


# HTML void 元素（自闭合标签）集合（统一定义于 _block_helpers.py）
from ._block_helpers import _VOID_HTML_TAGS

# 原始内容标签（``<script>``/``<style>``/``<template>``/``<noscript>`` 等：
# 行内出现时内容隐藏，避免脚本/样式源码混入正文）
from ._html_attrs import RAW_TEXT_TAGS as _HTML_RAW_TEXT_TAGS
from ._html_attrs import parse_attrs as _parse_attrs
from ._inline_scan import find_first_of

#: HTML 标签名长度上限（扫描保护）：合法 HTML 标签名远短于此（本项目标签表
#: 最长 ``figcaption`` 为 10）——超长同字母 run（如 ``<aaaa…``）在标签名扫描
#: 阶段即安全放弃，避免逐字符扫描整段 run 的 O(n²) 退化。
_MAX_TAG_NAME_LEN = 32


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

    # ★ 宿主契约（仅静态检查用；运行期由 ``_InlineParser`` 提供）。
    if TYPE_CHECKING:  # pragma: no cover - 仅静态检查
        _text: str
        _n: int
        _pos: int
        _MAX_DEPTH: int
        _FORMAT_CHARS: frozenset

        def _has_seq(self, *args: Any, **kwargs: Any) -> Any: ...

        def _has_char(self, *args: Any, **kwargs: Any) -> Any: ...

        def _try_match_str(self, *args: Any, **kwargs: Any) -> Any: ...

        def _try_format(self, *args: Any, **kwargs: Any) -> Any: ...

        def _make_nestable(self, *args: Any, **kwargs: Any) -> Any: ...

        def _is_interest(self, *args: Any, **kwargs: Any) -> Any: ...

    # ── HTML 实体 ─────────────────────────────────────────

    def _try_html_entity(self) -> InlineNode | None:
        if self._text[self._pos] != '&':
            return None
        # ★ 性能（超长单行）：实体需要 ``;`` 终止——文本不含 ``;`` 时直接判否
        #   （超长 ``&`` run 下避免每个 ``&`` 都扫描到文本末尾的 O(n²)，4000
        #   字符实测 1.56s）。
        if not self._has_char(';'):
            return None
        saved = self._pos
        # ★ 性能：终止字符（``;`` 或空白）用 C 级 ``find`` 一次定位（原逐字符
        #   Python 扫描）。
        end = find_first_of(self._text, '; \n\t', saved + 1)
        if end < 0 or self._text[end] != ';':
            return None  # 位置未推进（等价于原 ``self._pos = saved`` 回退）
        entity = self._text[saved:end + 1]
        self._pos = end + 1
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
        # ★ 性能（超长单行）：HTML 标签成功解析必然含 ``>``——文本不含 ``>``
        #   时直接判否（超长 ``<a`` run 下避免逐字符扫描到末尾的 O(n²)）。
        if not self._has_char('>'):
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
        # ★ 性能（超长单行）：标签名长度上限——同字母 run（``<aaaa…``）在扫描
        #   上限处停止（标签名必然不在标签表中 → 调用方回退），避免逐字符扫完
        #   整段 run。合法 HTML 标签名远短于上限（最长 ``figcaption``）。
        while self._pos < self._n and (self._text[self._pos].isalnum()
                                        or self._text[self._pos] in '-:'):
            self._pos += 1
            if self._pos - tag_start > _MAX_TAG_NAME_LEN:
                break
        tag_name = self._text[tag_start:self._pos].lower()

        if not tag_name:
            self._pos = saved
            return None

        # ★ 关闭 void 标签（如 </br>、</hr>、</img>、</input>）：HTML 中
        #   void 元素没有闭合标签，浏览器忽略之。渲染层同样**静默吞掉**——
        #   修复前返回 None 让文本原样上屏（``a</br>b`` 显示 ``a</br>b``）。
        if is_close and tag_name in _VOID_HTML_TAGS:
            self._skip_to_tag_end()
            return TextNode(content='')

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
            if tag_name == 'input':
                end_before_gt = self._pos - 1
                attr_text = self._text[attr_start:end_before_gt].strip()
                return self._make_input_node(attr_text)
            if tag_name in ('source', 'track', 'embed'):
                end_before_gt = self._pos - 1
                attr_text = self._text[attr_start:end_before_gt].strip()
                return self._make_media_node(tag_name, attr_text)
            return TextNode(content='')

        # ── 特殊标签：<a> 行内链接 / <ruby> 注音 ──
        if not is_close and tag_name in ('a', 'ruby'):
            attr_start = self._pos
            self._skip_html_attrs()
            if self._pos >= self._n or self._text[self._pos] != '>':
                self._pos = saved
                return None
            attr_text = self._text[attr_start:self._pos]
            self._pos += 1
            if tag_name == 'a':
                href = self._extract_attr(attr_text.strip(), 'href')
                children = self._parse_html_content_nested('a', depth)
                if children is None:
                    self._pos = saved
                    return None
                text = render_inline_to_text(children)
                if href:
                    return LinkNode(url=href, content=text or href,
                                    title=self._extract_attr(attr_text, 'title'),
                                    children=children)
                return TextNode(content=text)
            content = self._parse_html_content('ruby', depth)
            if content is None:
                self._pos = saved
                return None
            return TextNode(content=render_ruby_text(content))

        # ── 行内 MathML：``<math>…</math>`` → MathMLNode（渲染层转公式排版）──
        if not is_close and tag_name == 'math':
            self._skip_html_attrs()
            if self._pos < self._n and self._text[self._pos] == '>':
                self._pos += 1
                content = self._parse_html_content('math', depth)
                if content is not None:
                    return MathMLNode(content=content)
            self._pos = saved
            return None

        # ── 原始内容标签：内容隐藏（``<script>``/``<style>`` 等不应显示）──
        if not is_close and tag_name in _HTML_RAW_TEXT_TAGS:
            self._skip_html_attrs()
            if self._pos < self._n and self._text[self._pos] == '>':
                self._pos += 1
            content = self._parse_html_content(tag_name, depth)
            if content is None:
                self._pos = saved
                return None
            return TextNode(content='')

        # ── 行内媒体/嵌入容器：保留地址信息（修复前 src 随标签一起消失）──
        if not is_close and tag_name in ('video', 'audio', 'iframe', 'object'):
            attr_start = self._pos
            self._skip_html_attrs()
            if self._pos < self._n and self._text[self._pos] == '>':
                attr_text = self._text[attr_start:self._pos].strip()
                self._pos += 1
                children = self._parse_html_content_nested(tag_name, depth)
                if children is not None:
                    fallback = render_inline_to_text(children)
                    return self._make_media_node(tag_name, attr_text, fallback)
            self._pos = saved
            return None

        # 非 void 标签：必须在 _HTML_TAG_MAP 中注册
        if tag_name not in _HTML_TAG_MAP:
            self._pos = saved
            return None

        if is_close:
            self._skip_to_tag_end()
            return TextNode(content='')

        attr_start = self._pos
        self._skip_html_attrs()
        # 属性区文本（``title`` / ``style`` / ``color`` 等属性提取共用）
        attr_text = self._text[attr_start:self._pos].strip()
        # 提取 title 属性（用于 <abbr title="...">）
        title = self._extract_attr(attr_text, 'title')

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
            # 可嵌套但无节点类的标签（如 span/font/center）：递归解析内部格式标记
            children = self._parse_html_content_nested(tag_name, depth)
            if children is not None:
                text = render_inline_to_text(children)
                if tag_name in ('span', 'font'):
                    # ``<span style="color:red">`` / ``<font color="red">``
                    # → 彩色文本节点（颜色名 / #rrggbb；无颜色则透明）
                    color = _extract_style_color(attr_text)
                    bg = _extract_style_background(attr_text)
                    if tag_name == 'font':
                        color = self._extract_attr(attr_text, 'color') or color
                        bg = (self._extract_attr(attr_text, 'bgcolor')
                              or bg)
                    if color or bg:
                        return ColorTextNode(content=text, children=children,
                                             color=color, bg=bg)
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
                # ── 非可嵌套标签的**内容 Markdown 解析**（独立 C 级 █ 定界）──
                # ``<code>`` / ``<sup>`` / ``<kbd>`` / ``<abbr>`` 等内容按 HTML
                # 语义是短语容器，但本项目「行内 HTML 透明容器」的既有契约
                # （``<i>`` / ``<span>`` / ``<mark>``…）是内容仍按行内 Markdown
                # 解析。修复前这些标签返回原始文本 ——
                # ``<code>**x**</code>`` / ``<sup>**2**</sup>`` 把 ``**`` 字面
                # 上屏（``<b>`` 中不存在该缺陷）。
                #
                # ★ 内容跨软换行时**不用 C 级 find 取内容**：同一段落内后续还能
                #   出现同名标签（``<b>a\nb</b>``），C 级 find 会跨段取到最后一个
                #   闭合，吞掉中间文本。此时退回逐字符 ``_parse_html_content_nested``
                #   （渲染结果已正确配对，两者仅取边界方式不同）。
                # ``<progress>`` / ``<meter>`` / 原始内容标签（脚本/样式）等不作
                # 为短语容器，保持原始文本语义（见 ``_html_content_parses_inline``）。
                if self._html_content_parses_inline(tag_name):
                    if '\n' in content:
                        # 逐字符配对路径：内容含软换行时 C 级 find 会跨段取到
                        # 最后一个同名闭合标签（``<b>a\nb</b>`` 吞掉中间文本）。
                        # 先把位置回退到**内容起点**：C 级 ``find`` 已把
                        # ``self._pos`` 推到闭合标签之后，返回内容起点
                        # ``self._pos - len(content) - len(</tag>)`` 后交逐字符
                        # 扫描重新消费内容与闭合标签（父层无副作用）。
                        self._pos -= len(content) + len(f'</{tag_name}>')
                        children = self._parse_html_content_nested(tag_name, depth)
                    else:
                        children = self._parse_tag_content_nested(
                            tag_name, content, depth)
                    if children is not None:
                        if tag_name in ('abbr', 'acronym'):
                            return AbbrNode(content=render_inline_to_text(
                                children), children=children, title=title)
                        node = self._make_nestable(node_cls, children)
                        if tag_name in ('progress', 'meter'):
                            # 进度/度量控件：属性（value/max/min）供渲染层绘制
                            # 进度条（``_make_nestable`` 重建 meta → 属性补回）
                            node.meta = dict(node.meta or {})
                            node.meta.update(_parse_attrs(attr_text))
                        return node
                    # 子解析失败（病态嵌套 / 超深）→ 回退原始文本（不丢内容）
                if tag_name in ('abbr', 'acronym'):
                    return AbbrNode(content=content, title=title)
                node = node_cls(content=content)
                if tag_name in ('progress', 'meter'):
                    # 进度/度量控件：属性（value/max/min）供渲染层绘制进度条
                    node.meta = _parse_attrs(attr_text)
                return node

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
        # ★ 性能（超长单行）：闭合标签的**必要条件判否 + C 级定位**——修复前
        #   逐字符扫描到文本末尾，未闭合的超长 HTML 块（``<a>…`` 无 ``</a>``）
        #   在每个 ``<`` 触发位置都重扫一遍（O(n²)）。返回 ``None`` 时位置不变
        #   （调用方统一 ``self._pos = saved`` 回退）。
        if not self._has_seq(close_tag):
            return None
        start = self._pos
        idx = self._text.find(close_tag, self._pos)
        if idx < 0:
            return None
        self._pos = idx + len(close_tag)
        return self._text[start:idx]

    def _parse_html_content_nested(self, tag: str, depth: int
                                   ) -> list[InlineNode] | None:
        if depth > self._MAX_DEPTH:
            return None
        close_tag = f'</{tag}>'
        # ★ 性能（超长单行）：闭合标签的必要条件判否——未闭合的嵌套 HTML 标签
        #   （``<span>…`` 无 ``</span>``）原先每个 ``<`` 触发位置都扫描到文本
        #   末尾（O(n²)）。返回 ``None`` 时位置不变（调用方统一回退）。
        if not self._has_seq(close_tag):
            return None
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
            node = (self._try_format(depth + 1)
                    if self._is_interest(self._pos) else None)
            if node is not None:
                _emit_plain()
                nodes.append(node)
            else:
                plain_buf.append(self._text[self._pos])
                self._pos += 1
        return None

    #: 内容按**原始文本**保留的非可嵌套标签（HTML/本项目语义：内容不是
    #: Markdown，解析会破坏内容本身）。其余非可嵌套标签（``code`` / ``sup`` /
    #: ``sub`` / ``kbd`` / ``abbr`` / ``tt`` / ``samp`` / ``pre`` / ``h1``…）
    #: 的内容按行内 Markdown 解析（与可嵌套标签的「透明容器」契约一致）。
    _HTML_RAW_CONTENT_TAGS: frozenset = frozenset({
        'progress', 'meter',   # 数值控件：内容为回退文本，非标记
        'time', 'data', 'bdo',  # 值/方向标注文本
    })

    def _html_content_parses_inline(self, tag: str) -> bool:
        """该标签的内容是否按行内 Markdown 解析（而非保留原始文本）。

        原始内容标签（``<script>`` / ``<style>`` 等，见 ``_HTML_RAW_TEXT_TAGS``）
        与数值/标注控件（``_HTML_RAW_CONTENT_TAGS``）保留原始文本，其余短语
        容器（``<code>`` / ``<sup>`` / ``<kbd>`` / ``<abbr>`` …）内容解析。

        ★ 架构约束：本模块不得 import ``inline_parser``（``inline_parser``
        顶层导入本 mixin，反向导入构成导入环，架构测试强制校验）。
        """
        if tag in _HTML_RAW_TEXT_TAGS:
            return False
        return tag not in self._HTML_RAW_CONTENT_TAGS

    def _parse_tag_content_nested(self, tag: str, content: str,
                                  depth: int) -> list[InlineNode] | None:
        """解析标签内部文本（行内 Markdown）→ 子节点列表。

        与 ``_parse_html_content_nested``（逐字符、遇闭合标签即停）不同：
        内部文本已在 ``_parse_html_content`` 中用 C 级 ``find`` 定位并切出，
        此处只做一次**独立子解析**（同样的 ``_InlineParser``，深度 +1）。
        内容无行内标记（``_FORMAT_CHARS`` 全不出现）时直接返回单个文本节点，
        避免为 ``<code>x</code>`` 这类常见写法构造解析器。

        返回 ``None`` 表示无法解析（超深 / 解析异常）——调用方回退原始文本。

        ★ 子解析器**不 import** ``inline_parser``：宿主解析器（``_InlineParser``）
          必然提供 ``_parse_inline_subtree`` 钩子（它就是我方子类），该钩子内部
          使用同一解析器构造新实例——既复用同一实现，又避免 ``_inline_html``
          → ``inline_parser`` 的导入环（架构测试强制校验）。
        """
        if depth > self._MAX_DEPTH:
            return None
        # 独立子解析：深度 + 1（与 ``_parse_html_content_nested`` 的递归计数
        # 口径一致——每进入一层标签内容深度加 1，防止病态嵌套递归爆栈）
        if depth + 1 > self._MAX_DEPTH:
            return None
        if not content:
            return []
        if self._FORMAT_CHARS.isdisjoint(content):
            return [TextNode(content=content)]
        parser_factory = getattr(self, "_parse_inline_subtree", None)
        if parser_factory is None:  # pragma: no cover - 宿主未提供钩子
            return None
        try:
            nodes = parser_factory(content)
        except Exception:  # pragma: no cover - 子解析异常时回退原始文本
            return None
        return nodes

    def _make_img_node(self, attr_text: str) -> InlineNode | None:
        src = self._extract_attr(attr_text, 'src')
        alt = self._extract_attr(attr_text, 'alt')
        if src:
            title = self._extract_attr(attr_text, 'title')
            node = ImageNode(content=alt or '', url=src, title=title)
            width = self._extract_attr(attr_text, 'width')
            height = self._extract_attr(attr_text, 'height')
            if width or height:
                node.meta = {"width": width or "", "height": height or ""}
            return node
        return TextNode(content=f'<img {attr_text}>')

    def _make_input_node(self, attr_text: str) -> InlineNode:
        """行内 ``<input>`` → 控件符号（修复前整标签消失、行内控件不可见）。

        - ``checkbox`` → ☑ / ☐；``radio`` → ◉ / ○；
        - ``submit`` / ``button`` / ``reset`` → ``[ 标签 ]``；
        - 其它类型 → ``▸ <input 类型>``（不丢类型信息）。
        """
        attrs = _parse_attrs("<input " + (attr_text or "") + ">")
        itype = str(attrs.get("type", "") or "").strip().lower()
        if itype == "checkbox":
            return TextNode(content="\u2611" if "checked" in attrs else "\u2610")
        if itype == "radio":
            return TextNode(content="\u25c9" if "checked" in attrs else "\u25cb")
        if itype in ("submit", "button", "reset"):
            label = str(attrs.get("value", "") or itype.title())
            return TextNode(content=f"[ {label} ]")
        if itype:
            return TextNode(content=f"\u25b8 <input {itype}>")
        return TextNode(content="\u25b8 <input>")

    #: 行内媒体标签 → 图标（终端单行呈现）
    _MEDIA_GLYPHS = {
        "video": "\u25b6", "audio": "\u266a", "iframe": "\u29c9",
        "object": "\u29c9", "source": "\u25c6", "track": "\u25c6",
        "embed": "\u25c6",
    }

    def _make_media_node(self, tag: str, attr_text: str,
                         fallback: str = "") -> InlineNode:
        """行内 ``<video>`` / ``<audio>`` / ``<iframe>`` / ``<object>`` /
        ``<source>`` / ``<track>`` → 带地址的媒体摘要（内容不丢）。

        修复前这些标签在行内被当作透明容器或直接丢弃：``<audio src>`` /
        ``<iframe src>`` 的地址完全消失，``<source>`` / ``<track>`` 连标签都
        不显示。终端无媒体播放能力，但应保留来源信息。
        """
        attrs = _parse_attrs("<" + (tag or "media") + " "
                             + (attr_text or "") + ">")
        src = (str(attrs.get("src", "") or "")
               or str(attrs.get("data", "") or "")
               or str(attrs.get("data-src", "") or ""))
        kind = str(attrs.get("type", "") or attrs.get("kind", "") or "")
        glyph = self._MEDIA_GLYPHS.get(tag, "\u25c6")
        parts = [glyph]
        if kind:
            parts.append(f"[{kind}]")
        if src:
            parts.append(src)
        fallback = (fallback or "").strip()
        if fallback and fallback != src:
            parts.append(f"({fallback})")
        text = " ".join(parts).strip()
        return TextNode(content=text or fallback)

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


def render_ruby_text(text: str) -> str:
    """``<ruby>`` 内容 → 终端注音文本（``漢<rt>かん</rt>`` → ``漢(かん)``）。

    ``<rt>`` 内容以半角括号跟随基字；``<rp>``（注音括号回退）整段隐藏。
    """
    out: list[str] = []
    lower = text.lower()
    i = 0
    n = len(text)
    while i < n:
        if lower.startswith('<rt', i):
            end = text.find('>', i)
            if end < 0:
                break
            close = lower.find('</rt>', end)
            rt = text[end + 1:close] if close >= 0 else text[end + 1:]
            if rt:
                out.append(f'({rt.strip()})')
            i = close + 5 if close >= 0 else n
            continue
        if lower.startswith('<rp', i):
            end = text.find('>', i)
            close = lower.find('</rp>', end) if end >= 0 else -1
            i = close + 5 if close >= 0 else (end + 1 if end >= 0 else i + 1)
            continue
        out.append(text[i])
        i += 1
    return ''.join(out)