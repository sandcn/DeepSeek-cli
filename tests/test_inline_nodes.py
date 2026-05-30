#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/api/renderer/inline_nodes.py — 内联 Markdown 节点类型

覆盖内容：
  1. 所有 InlineNode 子类的默认构造和自定义构造
  2. _NESTABLE_TYPES / _HTML_TAG_MAP / 模块常量健康检查
  3. render_inline_to_text — 内联节点列表转纯文本（全覆盖）
  4. InlineRecursionError
"""

import pytest

from src.api.renderer.inline_nodes import (
    InlineNode,
    TextNode,
    BoldNode,
    ItalicNode,
    BoldItalicNode,
    UnderlineNode,
    InlineCodeNode,
    LinkNode,
    ImageNode,
    StrikethroughNode,
    HighlightNode,
    SubscriptNode,
    SuperscriptNode,
    SpoilerNode,
    InlineMathNode,
    FootnoteRefNode,
    AutoLinkNode,
    AutoLinkEmailNode,
    KbdNode,
    AbbrNode,
    LineBreakNode,
    _NESTABLE_TYPES,
    _HTML_TAG_MAP,
    InlineRecursionError,
    render_inline_to_text,
)


# ═══════════════════════════════════════════════════════════════
# 1. 所有 InlineNode 子类 — 默认构造
# ═══════════════════════════════════════════════════════════════

class TestInlineNodeDefaults:
    """所有 InlineNode 子类无参构造"""

    NODE_TYPES = [
        TextNode, BoldNode, ItalicNode, BoldItalicNode,
        UnderlineNode, InlineCodeNode, LinkNode, ImageNode,
        StrikethroughNode, HighlightNode, SubscriptNode,
        SuperscriptNode, SpoilerNode, InlineMathNode,
        FootnoteRefNode, AutoLinkNode, AutoLinkEmailNode,
        KbdNode, AbbrNode, LineBreakNode,
    ]

    @pytest.mark.parametrize("cls", NODE_TYPES)
    def test_default_construction(self, cls):
        """无参构造不应抛出异常，content 默认为空"""
        node = cls()
        assert isinstance(node, InlineNode)
        assert node.content == ''
        # 叶节点 children 为 None，非叶节点为 []
        if cls in (TextNode, InlineCodeNode, SubscriptNode, SuperscriptNode,
                   InlineMathNode, FootnoteRefNode, AutoLinkNode,
                   AutoLinkEmailNode, KbdNode, AbbrNode, LineBreakNode):
            assert node.children is None
        else:
            assert node.children == []

    @pytest.mark.parametrize("cls", NODE_TYPES)
    def test_is_inline_node_instance(self, cls):
        """所有节点类型都是 InlineNode 的实例"""
        assert isinstance(cls(), InlineNode)


# ═══════════════════════════════════════════════════════════════
# 2. 自定义构造
# ═══════════════════════════════════════════════════════════════

class TestInlineNodeCustom:
    """自定义参数构造"""

    def test_text_node_with_content(self):
        """TextNode 带 content"""
        node = TextNode(content='hello world')
        assert node.content == 'hello world'
        assert node.children is None

    def test_bold_node_with_children(self):
        """BoldNode 带 content 和 children"""
        child = TextNode(content='bold text')
        node = BoldNode(content='', children=[child])
        assert len(node.children) == 1
        assert node.children[0].content == 'bold text'

    def test_italic_node_with_children(self):
        """ItalicNode 带 children"""
        child = TextNode(content='italic')
        node = ItalicNode(children=[child])
        assert len(node.children) == 1
        assert node.children[0].content == 'italic'

    def test_bold_italic_node(self):
        """BoldItalicNode 嵌套"""
        child = TextNode(content='bold+italic')
        node = BoldItalicNode(children=[child])
        assert len(node.children) == 1

    def test_underline_node(self):
        """UnderlineNode"""
        node = UnderlineNode(content='underlined')
        assert node.content == 'underlined'

    def test_inline_code_node(self):
        """InlineCodeNode — 叶子节点"""
        node = InlineCodeNode(content='print("hello")')
        assert node.content == 'print("hello")'
        assert node.children is None

    def test_link_node_full(self):
        """LinkNode — 完整参数"""
        node = LinkNode(content='Click here', url='https://example.com', title='Example')
        assert node.content == 'Click here'
        assert node.url == 'https://example.com'
        assert node.title == 'Example'

    def test_link_node_default_title(self):
        """LinkNode — title 默认为空"""
        node = LinkNode(content='link', url='https://example.com')
        assert node.title == ''

    def test_image_node_full(self):
        """ImageNode — 完整参数"""
        node = ImageNode(content='alt text', url='https://example.com/img.png', title='Image')
        assert node.content == 'alt text'
        assert node.url == 'https://example.com/img.png'
        assert node.title == 'Image'

    def test_image_node_default_title(self):
        """ImageNode — title 默认为空"""
        node = ImageNode(content='alt', url='https://example.com/img.png')
        assert node.title == ''

    def test_spoiler_node(self):
        """SpoilerNode"""
        node = SpoilerNode(content='hidden text')
        assert node.content == 'hidden text'

    def test_inline_math_node(self):
        """InlineMathNode"""
        node = InlineMathNode(content='E=mc^2')
        assert node.content == 'E=mc^2'

    def test_footnote_ref_node(self):
        """FootnoteRefNode — 带 ref_id"""
        node = FootnoteRefNode(content='1', ref_id='fn1')
        assert node.ref_id == 'fn1'
        assert node.content == '1'

    def test_auto_link_node(self):
        """AutoLinkNode"""
        node = AutoLinkNode(content='https://example.com', url='https://example.com')
        assert node.url == 'https://example.com'

    def test_auto_link_email_node(self):
        """AutoLinkEmailNode"""
        node = AutoLinkEmailNode(content='user@example.com', email='user@example.com')
        assert node.email == 'user@example.com'

    def test_kbd_node(self):
        """KbdNode"""
        node = KbdNode(content='Ctrl+C')
        assert node.content == 'Ctrl+C'

    def test_abbr_node_full(self):
        """AbbrNode — 完整参数"""
        node = AbbrNode(content='AI', title='Artificial Intelligence')
        assert node.content == 'AI'
        assert node.title == 'Artificial Intelligence'

    def test_abbr_node_default_title(self):
        """AbbrNode — title 默认为空"""
        node = AbbrNode(content='AI')
        assert node.title == ''

    def test_line_break_node(self):
        """LineBreakNode"""
        node = LineBreakNode()
        assert node.content == ''
        assert node.children is None

    def test_strikethrough_node(self):
        """StrikethroughNode"""
        node = StrikethroughNode(content='deleted')
        assert node.content == 'deleted'

    def test_highlight_node(self):
        """HighlightNode"""
        node = HighlightNode(content='highlighted')
        assert node.content == 'highlighted'

    def test_subscript_node(self):
        """SubscriptNode"""
        node = SubscriptNode(content='sub')
        assert node.content == 'sub'

    def test_superscript_node(self):
        """SuperscriptNode"""
        node = SuperscriptNode(content='sup')
        assert node.content == 'sup'

    # ── 嵌套结构 ───────────────────────────────────────────

    def test_deeply_nested_structure(self):
        """深层嵌套节点树"""
        leaf = TextNode(content='deep')
        inner = BoldNode(children=[leaf])
        outer = ItalicNode(children=[inner])
        assert len(outer.children) == 1
        assert isinstance(outer.children[0], BoldNode)
        assert outer.children[0].children[0].content == 'deep'

    def test_mixed_children(self):
        """混合多种子节点类型"""
        children = [
            TextNode(content='text '),
            BoldNode(content='bold'),
            TextNode(content=' more'),
        ]
        parent = ItalicNode(children=children)
        assert len(parent.children) == 3


# ═══════════════════════════════════════════════════════════════
# 3. 模块常量健康检查
# ═══════════════════════════════════════════════════════════════

class TestModuleConstants:
    """_NESTABLE_TYPES / _HTML_TAG_MAP 健康检查"""

    def test_nestable_types_not_empty(self):
        """_NESTABLE_TYPES 不为空"""
        assert len(_NESTABLE_TYPES) > 0

    def test_nestable_types_all_node_subclasses(self):
        """_NESTABLE_TYPES 所有元素均为 InlineNode 子类"""
        for t in _NESTABLE_TYPES:
            assert issubclass(t, InlineNode), f"{t} 不是 InlineNode 子类"

    def test_nestable_types_allowed(self):
        """可嵌套节点类型应包含 BoldNode、ItalicNode 等"""
        expected = {BoldNode, ItalicNode, BoldItalicNode, UnderlineNode,
                    StrikethroughNode, HighlightNode, SpoilerNode}
        assert _NESTABLE_TYPES >= expected, f"缺少可嵌套类型: {expected - _NESTABLE_TYPES}"

    def test_html_tag_map_not_empty(self):
        """_HTML_TAG_MAP 不为空"""
        assert len(_HTML_TAG_MAP) > 0

    @pytest.mark.parametrize("tag,expected", [
        ('b', BoldNode), ('strong', BoldNode),
        ('i', ItalicNode), ('em', ItalicNode),
        ('u', UnderlineNode), ('s', StrikethroughNode),
        ('mark', HighlightNode), ('code', InlineCodeNode),
        ('sub', SubscriptNode), ('sup', SuperscriptNode),
        ('kbd', KbdNode), ('abbr', AbbrNode),
    ])
    def test_html_tag_map_node_types(self, tag, expected):
        """_HTML_TAG_MAP 中常见 HTML 标签映射到正确节点类型"""
        node_type, _ = _HTML_TAG_MAP[tag]
        assert node_type == expected, f"<{tag}> 应映射到 {expected}"

    @pytest.mark.parametrize("tag,expected_nestable", [
        ('b', True), ('strong', True),
        ('i', True), ('em', True),
        ('u', True), ('s', True),
        ('mark', True), ('del', True),
        ('code', False), ('kbd', False),
        ('sub', False), ('sup', False),
        ('abbr', False),
    ])
    def test_html_tag_map_nestable(self, tag, expected_nestable):
        """_HTML_TAG_MAP 中可嵌套标记正确"""
        _, nestable = _HTML_TAG_MAP[tag]
        assert nestable == expected_nestable, f"<{tag}> 的可嵌套标记应为 {expected_nestable}"


# ═══════════════════════════════════════════════════════════════
# 4. render_inline_to_text — 内联节点转纯文本
# ═══════════════════════════════════════════════════════════════

class TestRenderInlineToText:
    """render_inline_to_text() 边界全覆盖测试"""

    def test_empty_list(self):
        """空列表应返回空字符串"""
        assert render_inline_to_text([]) == ''

    def test_single_text_node(self):
        """单个 TextNode"""
        result = render_inline_to_text([TextNode(content='hello')])
        assert result == 'hello'

    def test_single_bold_node(self):
        """单个 BoldNode（无 children，用 content）"""
        result = render_inline_to_text([BoldNode(content='bold')])
        assert result == 'bold'

    def test_bold_with_text_child(self):
        """BoldNode 含 TextNode children"""
        result = render_inline_to_text([
            BoldNode(children=[TextNode(content='bold text')])
        ])
        assert result == 'bold text'

    def test_nested_bold_italic(self):
        """嵌套 BoldNode → ItalicNode → TextNode"""
        child = ItalicNode(children=[TextNode(content='nested')])
        result = render_inline_to_text([BoldNode(children=[child])])
        assert result == 'nested'

    def test_link_node(self):
        """LinkNode → 'content (url)'"""
        result = render_inline_to_text([
            LinkNode(content='click', url='https://example.com')
        ])
        assert result == 'click (https://example.com)'

    def test_link_node_ref(self):
        """LinkNode 带 ref:[ref_id] URL"""
        result = render_inline_to_text([
            LinkNode(content='ref link', url='[ref:fn1]')
        ])
        assert result == 'ref link (ref:fn1)'

    def test_image_node(self):
        """ImageNode → '[Image: content]'"""
        result = render_inline_to_text([
            ImageNode(content='alt text', url='https://example.com/img.png')
        ])
        assert result == '[Image: alt text]'

    def test_image_node_empty_content(self):
        """ImageNode content 为空"""
        result = render_inline_to_text([
            ImageNode(content='', url='https://example.com/img.png')
        ])
        assert result == '[Image: ]'

    def test_auto_link_node(self):
        """AutoLinkNode → content"""
        result = render_inline_to_text([
            AutoLinkNode(content='https://example.com', url='https://example.com')
        ])
        assert result == 'https://example.com'

    def test_auto_link_email_node(self):
        """AutoLinkEmailNode → content"""
        result = render_inline_to_text([
            AutoLinkEmailNode(content='user@example.com', email='user@example.com')
        ])
        assert result == 'user@example.com'

    def test_line_break_node(self):
        """LineBreakNode → 换行符"""
        result = render_inline_to_text([
            TextNode(content='line1'),
            LineBreakNode(),
            TextNode(content='line2'),
        ])
        assert result == 'line1\nline2'

    def test_footnote_ref_node(self):
        """FootnoteRefNode → '[^ref_id]'"""
        result = render_inline_to_text([
            FootnoteRefNode(content='1', ref_id='fn1')
        ])
        assert result == '[^fn1]'

    def test_spoiler_node(self):
        """SpoilerNode → content"""
        result = render_inline_to_text([
            SpoilerNode(content='hidden')
        ])
        assert result == 'hidden'

    def test_inline_math_node(self):
        """InlineMathNode → content"""
        result = render_inline_to_text([
            InlineMathNode(content='E=mc^2')
        ])
        assert result == 'E=mc^2'

    def test_subscript_node(self):
        """SubscriptNode → content"""
        result = render_inline_to_text([
            SubscriptNode(content='sub')
        ])
        assert result == 'sub'

    def test_superscript_node(self):
        """SuperscriptNode → content"""
        result = render_inline_to_text([
            SuperscriptNode(content='sup')
        ])
        assert result == 'sup'

    def test_text_node_only(self):
        """TextNode（基类，无特殊处理）→ content"""
        result = render_inline_to_text([
            TextNode(content='plain text')
        ])
        assert result == 'plain text'

    def test_inline_code_node(self):
        """InlineCodeNode (叶子节点, children=None) → content"""
        result = render_inline_to_text([
            InlineCodeNode(content='code')
        ])
        assert result == 'code'

    def test_kbd_node(self):
        """KbdNode (叶子节点) → content"""
        result = render_inline_to_text([
            KbdNode(content='Ctrl+C')
        ])
        assert result == 'Ctrl+C'

    def test_abbr_node(self):
        """AbbrNode (叶子节点) → content"""
        result = render_inline_to_text([
            AbbrNode(content='AI', title='Artificial Intelligence')
        ])
        assert result == 'AI'

    # ── 复杂组合 ───────────────────────────────────────────

    def test_mixed_inline_nodes(self):
        """混合多种节点类型"""
        nodes = [
            TextNode(content='Hello '),
            BoldNode(children=[TextNode(content='world')]),
            TextNode(content='! '),
            ImageNode(content='img', url='https://example.com/img.png'),
            LinkNode(content='link', url='https://example.com'),
        ]
        result = render_inline_to_text(nodes)
        assert result == 'Hello world! [Image: img]link (https://example.com)'

    def test_paragraph_with_multiple_children(self):
        """段落含多种格式"""
        nodes = [
            TextNode(content='This is '),
            BoldNode(children=[TextNode(content='bold')]),
            TextNode(content=' and '),
            ItalicNode(children=[TextNode(content='italic')]),
            TextNode(content=' text.'),
        ]
        result = render_inline_to_text(nodes)
        assert result == 'This is bold and italic text.'

    def test_multiple_line_breaks(self):
        """多个 LineBreakNode"""
        nodes = [
            TextNode(content='a'),
            LineBreakNode(),
            TextNode(content='b'),
            LineBreakNode(),
            TextNode(content='c'),
        ]
        result = render_inline_to_text(nodes)
        assert result == 'a\nb\nc'

    # ── 边界条件 ───────────────────────────────────────────

    def test_text_node_with_empty_content(self):
        """TextNode content 为空"""
        result = render_inline_to_text([TextNode(content='')])
        assert result == ''

    def test_node_with_empty_children(self):
        """节点 children 为空列表"""
        result = render_inline_to_text([BoldNode(children=[])])
        assert result == ''

    def test_link_node_empty_content(self):
        """LinkNode content 和 url 均为空"""
        result = render_inline_to_text([LinkNode(content='', url='')])
        assert result == ' ()'

    def test_image_node_empty_url(self):
        """ImageNode url 为空"""
        result = render_inline_to_text([ImageNode(content='img', url='')])
        assert result == '[Image: img]'

    def test_footnote_ref_empty_ref_id(self):
        """FootnoteRefNode ref_id 为空"""
        result = render_inline_to_text([FootnoteRefNode(ref_id='')])
        assert result == '[^]'

    def test_large_list_of_nodes(self):
        """大量节点列表"""
        nodes = [TextNode(content=str(i)) for i in range(100)]
        result = render_inline_to_text(nodes)
        assert result == ''.join(str(i) for i in range(100))


# ═══════════════════════════════════════════════════════════════
# 5. InlineRecursionError
# ═══════════════════════════════════════════════════════════════

class TestInlineRecursionError:
    """InlineRecursionError"""

    def test_is_runtime_error(self):
        """InlineRecursionError 是 RuntimeError 的子类"""
        assert issubclass(InlineRecursionError, RuntimeError)

    def test_can_be_raised(self):
        """可正常抛出"""
        with pytest.raises(InlineRecursionError):
            raise InlineRecursionError('nesting too deep')

    def test_default_message(self):
        """无参构造可工作"""
        with pytest.raises(InlineRecursionError):
            raise InlineRecursionError()
