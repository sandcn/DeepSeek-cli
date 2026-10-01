"""CodeBlock 折叠/展开 + 语法高亮增强测试。"""

from __future__ import annotations

from src.tui.ink import h, CodeBlock, CollapsibleCodeBlock
from src.tui._input_parser import KeyEvent
from src.tui.ink.widgets import _syntax
from tests.test_tui.ink._harness import Harness


CODE = "\n".join(f"line{i}" for i in range(10))


def test_codeblock_no_fold_by_default():
    text = Harness(40).render(h(CodeBlock, {"code": CODE, "width": 20}))
    assert text.count("line") == 10


def test_codeblock_folded_shows_hint_and_subset():
    text = Harness(40).render(
        h(CodeBlock, {"code": CODE, "width": 24, "maxLines": 3, "expandable": True})
    )
    assert "line0" in text and "line2" in text
    assert "line5" not in text
    assert "还有 7 行" in text


def test_codeblock_expanded_prop_shows_all():
    text = Harness(40).render(
        h(CodeBlock, {"code": CODE, "width": 24, "maxLines": 3, "expandable": True, "expanded": True})
    )
    assert "line9" in text


def test_collapsible_codeblock_toggles_via_enter():
    el = h(CollapsibleCodeBlock, {"code": CODE, "width": 24, "maxLines": 3})
    harness = Harness(40)
    before = harness.render(el)
    assert "line9" not in before
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="enter")) is True
    after = harness.render(el)
    assert "line9" in after


def test_collapsible_codeblock_space_toggles():
    el = h(CollapsibleCodeBlock, {"code": CODE, "width": 24, "maxLines": 3})
    harness = Harness(40)
    harness.render(el)
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="char", char=" ")) is True
    assert "line9" in harness.render(el)


def test_collapsible_no_consume_when_not_foldable():
    el = h(CollapsibleCodeBlock, {"code": "a\nb", "width": 20, "maxLines": 5})
    harness = Harness(40)
    harness.render(el)
    router = harness.reconciler._input_router_cache[1] if harness.reconciler._input_router_cache else None
    if router is not None:
        assert router(KeyEvent(kind="enter")) is False


def test_collapsible_on_toggle_callback():
    seen = []
    el = h(
        CollapsibleCodeBlock,
        {"code": CODE, "width": 24, "maxLines": 3, "onToggle": seen.append},
    )
    harness = Harness(40)
    harness.render(el)
    harness.reconciler._input_router_cache[1](KeyEvent(kind="enter"))
    assert seen == [True]


# ── 语法高亮 ────────────────────────────────────────────

def test_syntax_language_coverage():
    for lang in ("python", "javascript", "typescript", "go", "rust", "java", "c", "cpp",
                 "ruby", "shell", "sql", "yaml", "json", "css", "html"):
        assert _syntax.is_supported(lang)


def test_syntax_alias_normalization():
    assert _syntax.normalize_language("py") == "python"
    assert _syntax.normalize_language("JS") == "javascript"
    assert _syntax.normalize_language("bash") == "shell"


def test_syntax_tokenize_keywords_and_strings():
    runs = _syntax.tokenize_line('def f(): return "x"', "python")
    styles = {r.text: r.style for r in runs}
    assert styles.get("def") is not None
    assert styles.get('"x"') is not None


def test_codeblock_syntax_highlight_produces_ansi():
    el = h(CodeBlock, {"code": "def f(): pass", "language": "python", "syntaxHighlight": True, "width": 24})
    frame = Harness(40).frame(el)
    ansi = frame.to_ansi()
    assert "\x1b[" in ansi
    assert "def" in frame.to_ansi().replace("\x1b", "")


def test_codeblock_direct_call_still_works_without_hooks():
    """CodeBlock 保持无 hook（可直接调用构造元素）。"""
    el = CodeBlock({"code": "x = 1", "width": 10})
    assert el is not None


def test_codeblock_border_variants_full_set():
    """边框变体收敛到 _BORDER_CHARS（含 dashed/singleDouble 等）。"""
    for style in ("single", "double", "round", "bold", "classic", "dashed",
                  "singleDouble", "doubleSingle"):
        text = Harness(40).render(
            h(CodeBlock, {"code": "a = 1", "width": 16, "borderStyle": style})
        )
        first = text.split("\n")[0]
        assert len(first) == 16, (style, first)


def test_codeblock_dashed_uses_dashed_horizontal():
    text = Harness(40).render(
        h(CodeBlock, {"code": "a = 1", "width": 16, "borderStyle": "dashed"})
    )
    assert "┄" in text.split("\n")[0]
