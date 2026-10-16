"""src/renderer/_mermaid_er_gitgraph_mindmap_timeline_journey — Mermaid 额外图表单元测试。

通过 MermaidRenderer.render() 端到端覆盖 ER / Gitgraph / Mindmap / Timeline / Journey 分支。
"""

from __future__ import annotations

from src.renderer.mermaid_renderer import MermaidRenderer


def _render(source: str) -> str:
    return MermaidRenderer().render(source).plain


# ── ER 图 ────────────────────────────────────────────────

def test_render_er_basic():
    out = _render("erDiagram\nCUSTOMER ||--o{ ORDER : places\n")
    assert "CUSTOMER" in out
    assert "ORDER" in out
    assert "places" in out


def test_render_er_relation_symbol_visible():
    """关系符号不再被丢弃（回归护栏）。"""
    out = _render("erDiagram\nA ||--|| B : has\n")
    assert "||--||" in out


def test_render_er_no_relationships():
    out = _render("erDiagram\n%% comment only\n")
    assert "no relationships" in out


def test_render_er_multiple_relationships():
    out = _render("erDiagram\nA ||--o{ B : one\nC }o--|| D : two\n")
    assert out.count("┌") == 4  # 两组关系 × 每对两个框
    assert "one" in out
    assert "two" in out


def test_parse_er_rel():
    from src.renderer._mermaid_er_gitgraph_mindmap_timeline_journey import MermaidExtraMixin

    r = MermaidExtraMixin._parse_er_rel("CUSTOMER ||--o{ ORDER : places")
    assert r == ("CUSTOMER", "||--o{", "ORDER", "places")
    assert MermaidExtraMixin._parse_er_rel("no colon here") is None


# ── Gitgraph ─────────────────────────────────────────────

def test_render_gitgraph_commit():
    out = _render("gitGraph\ncommit\ncommit\n")
    assert "main" in out
    assert "●" in out


def test_render_gitgraph_branch_and_merge():
    out = _render("gitGraph\nbranch feature\ncheckout feature\ncommit\ncheckout main\nmerge feature\n")
    assert "feature" in out
    assert "●" in out


def test_render_gitgraph_no_commits():
    out = _render("gitGraph\n%% nothing\n")
    assert "no commits" in out


# ── Mindmap ──────────────────────────────────────────────

def test_render_mindmap_basic():
    out = _render("mindmap\n  root\n    child1\n      grand\n    child2\n")
    assert "root" in out
    assert "child1" in out
    assert "grand" in out
    assert "child2" in out
    assert "├─" in out
    assert "└─" in out


def test_render_mindmap_shapes():
    out = _render("mindmap\n  root\n    ((double))\n    [square]\n    (round)\n")
    assert "◎ double" in out
    assert "▢ square" in out
    assert "◯ round" in out


def test_render_mindmap_empty():
    out = _render("mindmap\n%% comment\n")
    assert "empty" in out


# ── Timeline ─────────────────────────────────────────────

def test_render_timeline_basic():
    out = _render("timeline\ntitle 项目历史\n2024: 启动\n2025: 发展 : 成熟\n")
    assert "项目历史" in out
    assert "2024" in out
    assert "启动" in out
    assert "发展" in out
    assert "成熟" in out


def test_render_timeline_empty():
    out = _render("timeline\n%% nothing\n")
    assert "empty" in out


# ── Journey ──────────────────────────────────────────────

def test_render_journey_basic():
    out = _render("journey\ntitle 用户旅程\nsection 注册\n  填写表单: 4: 用户\n  提交: 2: 系统\n")
    assert "用户旅程" in out
    assert "注册" in out
    assert "填写表单" in out
    assert "提交" in out
    assert "👤" in out
    assert "🤖" in out


def test_render_journey_score_clamped():
    out = _render("journey\nsection 测试\n  任务: 99: 用户\n  任务2: -3: 用户\n")
    assert "99" not in out  # 钳制到 5
    assert "█" in out


def test_render_journey_empty():
    out = _render("journey\n%% nothing\n")
    assert "empty" in out


# ── 空图 / fallback ──────────────────────────────────────

def test_render_empty_source():
    out = _render("")
    assert "empty diagram" in out


def test_render_fallback():
    out = _render("customdiagram\nline1\nline2\n")
    assert "customdiagram" in out
    assert "line1" in out
    assert "line2" in out
