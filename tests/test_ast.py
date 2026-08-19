"""AST 层测试 — 覆盖 src/renderer/ast/ 的节点类型、展平器、优化器与构建器。

验证 NodeType/ASTNode/SourceRange、ASTFlattener.flatten、ASTOptimizer.optimize、
ASTBuilder.feed/flush 的核心行为。
"""

import pytest

from src.renderer.ast import (
    ASTBuilder,
    ASTFlattener,
    ASTNode,
    ASTOptimizer,
    NodeType,
    SourceRange,
)
from src.renderer.types import RenderContext, Token, TokenType


# ── NodeType / SourceRange ─────────────────────────────────

def test_node_type_has_document_and_paragraph():
    assert NodeType.DOCUMENT is not None
    assert NodeType.PARAGRAPH is not None


def test_source_range_defaults():
    r = SourceRange()
    assert r.start == 0 and r.end == 0
    assert r.line_start == 0 and r.line_end == 0


def test_source_range_repr():
    r = SourceRange(start=1, end=5, line_start=1, line_end=3)
    assert "L1" in repr(r)


# ── ASTNode 树操作 ─────────────────────────────────────────

def test_astnode_add_child():
    node = ASTNode(NodeType.DOCUMENT)
    child = ASTNode(NodeType.PARAGRAPH, content="hi")
    node.add_child(child)
    assert node.children == [child]


def test_astnode_find():
    root = ASTNode(NodeType.DOCUMENT)
    p = ASTNode(NodeType.PARAGRAPH, content="a")
    root.add_child(p)
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="b"))
    assert root.find(NodeType.PARAGRAPH) == [p, root.children[1]]


def test_astnode_find_first():
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.HR))
    root.add_child(ASTNode(NodeType.PARAGRAPH))
    found = root.find_first(NodeType.PARAGRAPH)
    assert found is not None and found.type is NodeType.PARAGRAPH


def test_astnode_find_first_missing_returns_none():
    root = ASTNode(NodeType.DOCUMENT)
    assert root.find_first(NodeType.HEADING) is None


def test_astnode_to_dict_from_dict_roundtrip():
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.HEADING, content="Title", meta={"level": 1}))
    root.range = SourceRange(start=0, end=10, line_start=1, line_end=1)

    restored = ASTNode.from_dict(root.to_dict())
    assert restored.type is NodeType.DOCUMENT
    assert restored.children[0].content == "Title"
    assert restored.children[0].meta["level"] == 1
    assert restored.range.end == 10


def test_astnode_dump_contains_type_name():
    node = ASTNode(NodeType.PARAGRAPH, content="hello")
    out = node.dump()
    assert "PARAGRAPH" in out
    assert "hello" in out


def test_astnode_repr():
    node = ASTNode(NodeType.PARAGRAPH, content="hi")
    assert "PARAGRAPH" in repr(node)


# ── ASTFlattener ───────────────────────────────────────────

@pytest.fixture
def flattener():
    return ASTFlattener()


def test_flatten_paragraph(flattener):
    node = ASTNode(NodeType.PARAGRAPH, content="hello")
    tokens = flattener.flatten(node)
    assert len(tokens) == 1
    assert tokens[0].type is TokenType.PARAGRAPH
    assert tokens[0].content == "hello"


def test_flatten_heading(flattener):
    node = ASTNode(NodeType.HEADING, content="Title", meta={"level": 2})
    tokens = flattener.flatten(node)
    assert tokens[0].type is TokenType.HEADING
    assert tokens[0].meta["level"] == 2


def test_flatten_code_block(flattener):
    node = ASTNode(NodeType.CODE_BLOCK, content="a\nb", meta={"lang": "python"})
    tokens = flattener.flatten(node)
    types = [t.type for t in tokens]
    assert types[0] is TokenType.CODE_FENCE_OPEN
    assert types[-1] is TokenType.CODE_FENCE_CLOSE
    assert TokenType.CODE_LINE in types
    assert tokens[0].meta.get("lang") == "python"


def test_flatten_document_recurses(flattener):
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="a"))
    root.add_child(ASTNode(NodeType.HR))
    tokens = flattener.flatten(root)
    types = [t.type for t in tokens]
    assert types == [TokenType.PARAGRAPH, TokenType.HR]


def test_flatten_ordered_list_injects_bullet_false(flattener):
    item = ASTNode(NodeType.LIST_ITEM, content="x", meta={"bullet": True})
    ordered = ASTNode(NodeType.ORDERED_LIST, children=[item])
    tokens = flattener.flatten(ordered)
    assert tokens[0].type is TokenType.LIST_ITEM
    assert tokens[0].meta["bullet"] is False


def test_flatten_unknown_node_returns_empty(flattener):
    # 无 handler 的节点类型（如 HTML_LINE 直接展平）→ 安全降级为空
    node = ASTNode(NodeType.TEXT, content="x")
    assert flattener.flatten(node) == []


# ── ASTOptimizer ───────────────────────────────────────────

@pytest.fixture
def optimizer():
    return ASTOptimizer()


def test_optimize_does_not_mutate_original(optimizer):
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.EMPTY_LINE))
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="x"))

    optimized = optimizer.optimize(root)
    # 原始树保留 EMPTY_LINE
    assert root.children[0].type is NodeType.EMPTY_LINE
    # 优化后树移除 EMPTY_LINE
    assert all(c.type is not NodeType.EMPTY_LINE for c in optimized.children)


def test_optimize_strip_empty(optimizer):
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.EMPTY_LINE))
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="keep"))
    optimized = optimizer.optimize(root)
    assert [c.type for c in optimized.children] == [NodeType.PARAGRAPH]


def test_optimize_merge_paragraphs(optimizer):
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="a"))
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="b"))
    optimized = optimizer.optimize(root)
    assert len(optimized.children) == 1
    assert optimized.children[0].content == "a\n\nb"


def test_optimize_wrap_sections(optimizer):
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.HEADING, content="H"))
    root.add_child(ASTNode(NodeType.PARAGRAPH, content="p"))
    optimized = optimizer.optimize(root)
    assert optimized.children[0].type is NodeType.SECTION
    assert optimized.children[0].children[0].type is NodeType.HEADING


def test_optimize_normalize_lists(optimizer):
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.LIST_ITEM, content="a", meta={"bullet": True}))
    root.add_child(ASTNode(NodeType.LIST_ITEM, content="b", meta={"bullet": True}))
    optimized = optimizer.optimize(root)
    assert optimized.children[0].type is NodeType.LIST
    assert len(optimized.children[0].children) == 2


def test_optimize_merge_code_blocks_enabled():
    opt = ASTOptimizer(merge_code_blocks=True)
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.CODE_BLOCK, content="a", meta={"lang": "py"}))
    root.add_child(ASTNode(NodeType.CODE_BLOCK, content="b", meta={"lang": "py"}))
    optimized = opt.optimize(root)
    assert len(optimized.children) == 1
    assert optimized.children[0].content == "a\nb"


def test_optimize_merge_code_blocks_different_lang_not_merged():
    opt = ASTOptimizer(merge_code_blocks=True)
    root = ASTNode(NodeType.DOCUMENT)
    root.add_child(ASTNode(NodeType.CODE_BLOCK, content="a", meta={"lang": "py"}))
    root.add_child(ASTNode(NodeType.CODE_BLOCK, content="b", meta={"lang": "js"}))
    optimized = opt.optimize(root)
    assert len(optimized.children) == 2


def test_optimizer_deep_copy_isolated():
    node = ASTNode(NodeType.DOCUMENT)
    node.add_child(ASTNode(NodeType.PARAGRAPH, content="x", meta={"k": "v"}))
    copied = ASTOptimizer._deep_copy(node)
    copied.children[0].meta["k"] = "changed"
    assert node.children[0].meta["k"] == "v"


# ── ASTBuilder ─────────────────────────────────────────────

def test_builder_paragraph_buffered_then_flushed():
    b = ASTBuilder()
    assert b.feed(Token(TokenType.PARAGRAPH, "hello")) == []
    nodes = b.flush()
    assert len(nodes) == 1
    assert nodes[0].type is NodeType.PARAGRAPH
    assert nodes[0].content == "hello"


def test_builder_heading_emits_immediately():
    b = ASTBuilder()
    nodes = b.feed(Token(TokenType.HEADING, "Title", {"level": 1}))
    assert nodes[0].type is NodeType.HEADING
    assert nodes[0].meta["level"] == 1


def test_builder_code_block():
    b = ASTBuilder()
    b.feed(Token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}))
    b.feed(Token(TokenType.CODE_LINE, "print(1)"))
    nodes = b.feed(Token(TokenType.CODE_FENCE_CLOSE))
    assert nodes[0].type is NodeType.CODE_BLOCK
    assert nodes[0].content == "print(1)"
    assert nodes[0].meta["lang"] == "python"


def test_builder_list_items_merged():
    b = ASTBuilder()
    b.feed(Token(TokenType.LIST_ITEM, "a", {"bullet": True}))
    b.feed(Token(TokenType.LIST_ITEM, "b", {"bullet": True}))
    nodes = b.flush()
    assert nodes[0].type is NodeType.LIST
    assert len(nodes[0].children) == 2


def test_builder_get_root():
    b = ASTBuilder()
    b.feed(Token(TokenType.HEADING, "T", {"level": 1}))
    root = b.get_root()
    assert root.type is NodeType.DOCUMENT
    assert len(root.children) == 1


def test_builder_paragraph_join_multiple_lines():
    b = ASTBuilder()
    b.feed(Token(TokenType.PARAGRAPH, "line1"))
    b.feed(Token(TokenType.PARAGRAPH, "line2"))
    nodes = b.flush()
    assert nodes[0].content == "line1\nline2"


# ── RenderContext ──────────────────────────────────────────

def test_render_context_fn_next_number():
    ctx = RenderContext()
    assert ctx.fn_next_number() == 1
    assert ctx.fn_next_number() == 2


def test_render_context_defaults():
    ctx = RenderContext()
    assert ctx.fn_map == {}
    assert ctx.fn_counter == 0
    assert ctx.heading_counters == {}
