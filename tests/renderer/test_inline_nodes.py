"""内联节点与递归解析器测试 — 覆盖 inline_nodes.py 与 recursive_parser.py。

验证 InlineNode 类型体系、render_inline_to_text 纯文本渲染、
MarkdownRecursiveParser 一次性解析、以及 AST 后处理函数。
"""

import pytest

from src.renderer.ast.types import ASTNode, NodeType
from src.renderer.inline_nodes import (
    AutoLinkEmailNode,
    AutoLinkNode,
    BoldNode,
    FootnoteRefNode,
    ImageNode,
    InlineCommentNode,
    LinkNode,
    TextNode,
    WikiLinkNode,
    render_inline_to_text,
)
from src.renderer.recursive_parser import (
    MarkdownRecursiveParser,
    _merge_blockquotes,
    _merge_list_continuations,
    _nest_blockquotes,
)


# ── InlineNode 节点 ────────────────────────────────────────

def test_text_node_default():
    node = TextNode(content="hello")
    assert node.content == "hello"
    assert node.children is None  # 叶子节点


def test_link_node_fields():
    node = LinkNode(content="text", url="http://x", title="t")
    assert node.url == "http://x"
    assert node.title == "t"


def test_wikilink_node_display():
    node = WikiLinkNode(target="target", display="display")
    assert node.display == "display"


# ── render_inline_to_text ──────────────────────────────────

def test_render_text_node():
    assert render_inline_to_text([TextNode(content="hello")]) == "hello"


def test_render_bold_recurses_children():
    node = BoldNode(children=[TextNode(content="x")])
    assert render_inline_to_text([node]) == "x"


def test_render_link_node():
    node = LinkNode(content="text", url="http://example.com")
    assert render_inline_to_text([node]) == "text (http://example.com)"


def test_render_link_node_ref():
    node = LinkNode(content="text", url="[ref:abc]")
    assert render_inline_to_text([node]) == "text (ref:abc)"


def test_render_image_node():
    node = ImageNode(content="alt", url="img.png")
    assert render_inline_to_text([node]) == "[Image: alt]"


def test_render_autolink_node():
    assert render_inline_to_text([AutoLinkNode(content="http://x", url="http://x")]) == "http://x"
    assert render_inline_to_text([AutoLinkEmailNode(content="a@b", email="a@b")]) == "a@b"


def test_render_footnote_ref():
    node = FootnoteRefNode(content="x", ref_id="1")
    assert render_inline_to_text([node]) == "[^1]"


def test_render_wikilink_uses_display():
    node = WikiLinkNode(target="t", display="d")
    assert render_inline_to_text([node]) == "d"


def test_render_wikilink_fallback_target():
    node = WikiLinkNode(target="t", display=None)
    assert render_inline_to_text([node]) == "t"


def test_render_inline_comment_empty():
    node = InlineCommentNode(content="hidden")
    assert render_inline_to_text([node]) == ""


def test_render_multiple_nodes_concatenated():
    nodes = [TextNode(content="a"), TextNode(content="b")]
    assert render_inline_to_text(nodes) == "ab"


# ── 后处理：_merge_blockquotes ─────────────────────────────

def test_merge_blockquotes_same_depth():
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.BLOCKQUOTE, content="a", meta={"depth": 1}))
    root.add_child(ASTNode(NodeType.BLOCKQUOTE, content="b", meta={"depth": 1}))

    _merge_blockquotes(root)
    assert len(root.children) == 1
    merged = root.children[0]
    assert merged.type is NodeType.BLOCKQUOTE
    assert len(merged.children) == 2


def test_merge_blockquotes_different_depth_not_merged():
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.BLOCKQUOTE, content="a", meta={"depth": 1}))
    root.add_child(ASTNode(NodeType.BLOCKQUOTE, content="b", meta={"depth": 2}))

    _merge_blockquotes(root)
    assert len(root.children) == 2


# ── 后处理：_nest_blockquotes ──────────────────────────────

def test_nest_blockquotes_nests_deeper():
    root = ASTNode(NodeType.DOCUMENT)
    outer = ASTNode(NodeType.BLOCKQUOTE, content="a", meta={"depth": 1})
    inner = ASTNode(NodeType.BLOCKQUOTE, content="b", meta={"depth": 2})
    root.add_child(outer)
    root.add_child(inner)

    _nest_blockquotes(root)
    assert len(root.children) == 1
    assert len(root.children[0].children) == 1
    assert root.children[0].children[0].content == "b"


# ── 后处理：_merge_list_continuations ──────────────────────

def test_merge_list_continuations():
    root = ASTNode(NodeType.DOCUMENT)
    item = ASTNode(NodeType.LIST_ITEM, content="a")
    lst = ASTNode(NodeType.LIST, children=[item])
    root.add_child(lst)
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="continued"))

    _merge_list_continuations(root)
    assert len(root.children) == 1
    assert root.children[0].children[0].content == "a continued"


# ── MarkdownRecursiveParser.parse ──────────────────────────

@pytest.fixture
def parser():
    return MarkdownRecursiveParser()


def test_parse_heading(parser):
    root = parser.parse("# Title")
    assert root.type is NodeType.DOCUMENT
    assert root.children[0].type is NodeType.HEADING
    assert root.children[0].content == "Title"


def test_parse_paragraph(parser):
    root = parser.parse("hello world")
    assert root.children[0].type is NodeType.PARAGRAPH
    assert "hello" in root.children[0].content


def test_parse_horizontal_rule(parser):
    root = parser.parse("---")
    assert any(c.type is NodeType.HR for c in root.children)


def test_parse_code_block(parser):
    root = parser.parse("```python\nprint(1)\n```")
    assert any(c.type is NodeType.CODE_BLOCK for c in root.children)


def test_parse_incremental_feed_flush(parser):
    nodes = []
    nodes.extend(parser.feed("# T"))
    nodes.extend(parser.feed("itle\n"))
    nodes.extend(parser.flush())
    assert any(n.type is NodeType.HEADING for n in nodes)


def test_parse_empty(parser):
    root = parser.parse("")
    assert root.type is NodeType.DOCUMENT
