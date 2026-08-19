"""Mermaid 渲染测试 — 覆盖 src/renderer/_mermaid_helpers.py 与 mermaid_renderer.py。

验证字符级辅助函数、节点形状解析、subgraph 识别，以及 MermaidRenderer 的类型分派与降级。
"""

import pytest
from rich.text import Text

from src.renderer._mermaid_helpers import (
    _extract_subgraph_title,
    _extract_word_ids,
    _is_comment_line,
    _is_subgraph_end,
    _is_subgraph_start,
    _is_word_char,
    _parse_node_shape,
    _starts_with_ignore_case,
)
from src.renderer.mermaid_renderer import MermaidRenderer


# ── 字符级辅助函数 ────────────────────────────────────────

def test_is_word_char():
    assert _is_word_char("a") is True
    assert _is_word_char("1") is True
    assert _is_word_char("_") is True
    assert _is_word_char("-") is False
    assert _is_word_char(" ") is False


def test_extract_word_ids():
    assert _extract_word_ids("A --> B") == ["A", "B"]
    assert _extract_word_ids("_id1 and id_2") == ["_id1", "and", "id_2"]


def test_starts_with_ignore_case():
    assert _starts_with_ignore_case("Graph TD", "graph") is True
    assert _starts_with_ignore_case("GRAPH", "graph") is True
    assert _starts_with_ignore_case("grap", "graph") is False


def test_is_comment_line():
    assert _is_comment_line("%% comment") is True
    assert _is_comment_line("A-->B") is False


# ── _parse_node_shape ─────────────────────────────────────

def test_parse_node_shape_square():
    assert _parse_node_shape("A[text]") == [("A", "text", "square")]


def test_parse_node_shape_round():
    assert _parse_node_shape("A(text)") == [("A", "text", "round")]


def test_parse_node_shape_diamond():
    assert _parse_node_shape("A{text}") == [("A", "text", "diamond")]


def test_parse_node_shape_cylinder():
    assert _parse_node_shape("A[(text)]") == [("A", "text", "cylinder")]


def test_parse_node_shape_no_shape():
    assert _parse_node_shape("A") == []


def test_parse_node_shape_multiple():
    result = _parse_node_shape("A[x] B[y]")
    assert result == [("A", "x", "square"), ("B", "y", "square")]


# ── subgraph 识别 ─────────────────────────────────────────

def test_is_subgraph_start():
    assert _is_subgraph_start("subgraph title") is True
    assert _is_subgraph_start("SUBGRAPH") is True
    assert _is_subgraph_start("graph TD") is False


def test_is_subgraph_end():
    assert _is_subgraph_end("end") is True
    assert _is_subgraph_end("END") is True
    assert _is_subgraph_end("end note") is False


def test_extract_subgraph_title():
    assert _extract_subgraph_title("subgraph My Title") == "My Title"
    assert _extract_subgraph_title("subgraph") == "subgraph"
    assert _extract_subgraph_title("graph TD") is None


# ── MermaidRenderer ───────────────────────────────────────

@pytest.fixture
def renderer():
    return MermaidRenderer()


def test_render_empty_returns_placeholder(renderer):
    t = renderer.render("")
    assert isinstance(t, Text)
    assert "empty diagram" in t.plain


def test_render_whitespace_returns_placeholder(renderer):
    t = renderer.render("   \n  ")
    assert "empty diagram" in t.plain


def test_render_flowchart(renderer):
    t = renderer.render("graph TD\n    A --> B")
    assert isinstance(t, Text)
    assert t.plain  # 非空


def test_render_sequence(renderer):
    t = renderer.render("sequenceDiagram\n    Alice->>Bob: Hello")
    assert isinstance(t, Text)


def test_render_class(renderer):
    t = renderer.render("classDiagram\n    class A")
    assert isinstance(t, Text)


def test_render_unknown_type_fallback(renderer):
    t = renderer.render("someunknown A-->B")
    assert isinstance(t, Text)
    assert "someunknown" in t.plain


def test_render_fallback(renderer):
    t = renderer._render_fallback(["customDiagram", "A-->B"])
    assert "customDiagram" in t.plain
    assert "A-->B" in t.plain


def test_render_pie_gantt_state(renderer):
    for src in ("pie title X\n\"a\": 1", "gantt\ntitle T", "stateDiagram-v2\n[*]-->A"):
        assert isinstance(renderer.render(src), Text)
