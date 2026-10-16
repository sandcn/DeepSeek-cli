"""MathML → LaTeX → 终端排版测试（TUI 流式路径）。

覆盖 ``src/renderer/ansi/_mathml.py``：MathML 宽容解析、元素转换、失败回退，
以及 ``<math>`` 块（``_html_block``）与行内 ``<math>``（``inline`` 节点）渲染。
"""

from __future__ import annotations

from src.renderer.ansi._mathml import (
    mathml_to_latex, parse_mathml, render_mathml, register_mathml_element,
)
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


def _render(md: str, width: int = 90) -> str:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return "\n".join(ln.plain for ln in r.take_lines())


def _inline(text: str) -> str:
    return "".join(r.text for r in render_inline(text))


# ── 基础元素转换 ─────────────────────────────────────────


def test_simple_identifier_and_number():
    assert mathml_to_latex("<mi>x</mi>") == "x"
    assert mathml_to_latex("<mn>42</mn>") == "42"


def test_operator_mapping_adds_spacing():
    latex = mathml_to_latex("<mi>x</mi><mo>+</mo><mi>y</mi>")
    assert latex == "x + y"


def test_operator_unicode_maps_to_command():
    assert mathml_to_latex("<mo>∑</mo>").strip() == "\\sum"
    assert mathml_to_latex("<mo>≤</mo>").strip() == "\\le"


def test_mrow_and_math_root():
    latex = mathml_to_latex("<math><mrow><mn>1</mn><mo>+</mo><mn>2</mn></mrow></math>")
    assert latex == "1 + 2"


def test_frac():
    latex = mathml_to_latex("<mfrac><mi>a</mi><mi>b</mi></mfrac>")
    assert latex == "\\frac{a}{b}"


def test_sqrt_and_root():
    assert mathml_to_latex("<msqrt><mn>2</mn></msqrt>") == "\\sqrt{2}"
    assert mathml_to_latex("<mroot><mi>x</mi><mn>3</mn></mroot>") == "\\sqrt[3]{x}"


def test_sup_sub_subsup():
    assert mathml_to_latex("<msup><mi>x</mi><mn>2</mn></msup>") == "x^{2}"
    assert mathml_to_latex("<msub><mi>x</mi><mi>i</mi></msub>") == "x_{i}"
    latex = mathml_to_latex("<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>")
    assert latex == "x_{i}^{2}"


def test_mover_under_munderover():
    assert mathml_to_latex("<mover><mi>x</mi><mo>‾</mo></mover>") == "\\overline{x}"
    assert mathml_to_latex("<mover><mi>x</mi><mo>→</mo></mover>") == "\\vec{x}"
    assert mathml_to_latex("<munder><mi>x</mi><mo>_</mo></munder>") == "\\underset{_}{x}"


def test_munderover_big_operator():
    latex = mathml_to_latex(
        "<munderover><mo>∑</mo><mrow><mn>1</mn></mrow><mi>n</mi></munderover>")
    assert latex.strip() == "\\sum_{1}^{n}"


def test_mfenced():
    latex = mathml_to_latex('<mfenced open="[" close="]"><mi>a</mi><mi>b</mi></mfenced>')
    assert latex == "\\left[ a , b \\right]"


def test_mfenced_with_explicit_separator():
    latex = mathml_to_latex(
        '<mfenced open="[" close="]"><mi>a</mi><mo>,</mo><mi>b</mi></mfenced>')
    assert latex == "\\left[ a , b \\right]"


def test_mtable():
    latex = mathml_to_latex(
        "<mtable><mtr><mtd><mn>1</mn></mtd><mtd><mn>2</mn></mtd></mtr>"
        "<mtr><mtd><mn>3</mn></mtd><mtd><mn>4</mn></mtd></mtr></mtable>")
    assert latex == "\\begin{matrix}1 & 2 \\\\ 3 & 4\\end{matrix}"


def test_mathvariant_mi():
    assert mathml_to_latex('<mi mathvariant="double-struck">R</mi>') == "\\mathbb{R}"
    assert mathml_to_latex('<mi mathvariant="script">F</mi>') == "\\mathcal{F}"
    assert mathml_to_latex('<mi mathvariant="bold">v</mi>') == "\\mathbf{v}"


def test_mtext_and_ms():
    assert mathml_to_latex("<mtext>if x</mtext>") == "\\text{if x}"
    assert mathml_to_latex('<ms>hi</ms>') == '\\text{"hi"}'


def test_menclose():
    assert mathml_to_latex('<menclose notation="box"><mi>E</mi></menclose>') == "\\boxed{E}"
    assert mathml_to_latex('<menclose notation="updiagonalstrike"><mi>x</mi></menclose>') == "\\cancel{x}"


def test_semantics_and_annotation_skipped():
    latex = mathml_to_latex(
        "<semantics><mi>x</mi><annotation>ignored</annotation></semantics>")
    assert latex == "x"


def test_mphantom():
    assert mathml_to_latex("<mphantom><mi>a</mi></mphantom>") == "\\phantom{a}"


# ── 宽容解析 ─────────────────────────────────────────────


def test_parse_tolerates_unclosed_tags():
    node = parse_mathml("<mi>x</mi><mn>1")
    assert node is not None
    assert mathml_to_latex("<mi>x</mi><mn>1") == "x1"


def test_parse_namespace_prefix():
    assert mathml_to_latex('<m:mi xmlns:m="x">y</m:mi>') == "y"


def test_parse_html_entities():
    assert mathml_to_latex("<mi>&#x2211;</mi>") == "∑"


def test_invalid_source_returns_none():
    assert mathml_to_latex("") is None
    assert mathml_to_latex("plain text without tags") is None


def test_render_mathml_returns_box():
    box = render_mathml("<mfrac><mn>1</mn><mn>2</mn></mfrac>")
    assert box is not None
    assert "─" in box.plain
    assert render_mathml("no tags here") is None


def test_register_mathml_element_extension_point():
    def _handler(node, depth):
        return "\\alpha"

    register_mathml_element("mysymbol", _handler)
    try:
        assert mathml_to_latex("<mysymbol/>") == "\\alpha"
    finally:
        from src.renderer.ansi import _mathml
        _mathml._HANDLERS.pop("mysymbol", None)


# ── 块级 / 行内渲染 ──────────────────────────────────────


def test_math_block_rendered_with_frame():
    out = _render("<math><mfrac><mi>a</mi><mi>b</mi></mfrac></math>\n")
    assert "╭─ 公式" in out
    assert "─" in out
    assert "a" in out and "b" in out


def test_math_block_multiline_source():
    out = _render(
        "<math>\n"
        "  <msup><mi>x</mi><mn>2</mn></msup><mo>+</mo>\n"
        "  <mfrac><mn>1</mn><mn>2</mn></mfrac>\n"
        "</math>\n")
    assert "x²" in out
    assert "1" in out


def test_math_block_plain_latex_content():
    # 非 MathML 标签片段（模型直接写公式源码）→ 按 LaTeX 渲染
    out = _render("<math>x^2 + \\frac{1}{2}</math>\n")
    assert "x²" in out


def test_math_block_invalid_falls_back_with_content():
    out = _render("<math><broken</math>\n")
    # 内容不丢（回退为标签行 + 原文），且不显示「原始内容不显示」
    assert "原始内容不显示" not in out


def test_inline_math_renders_compact():
    assert _inline("值 <math><msup><mi>x</mi><mn>2</mn></msup></math> 结束") == "值 x² 结束"


def test_inline_mathml_fraction_expands_to_multiline():
    from src.renderer.ansi.inline import inline_lines

    rows = inline_lines("值 <math><mfrac><mi>a</mi><mi>b</mi></mfrac></math> 结束")
    assert len(rows) == 3
    assert "值" in rows[1].plain and "结束" in rows[1].plain
    assert rows[0].plain.strip() == "a"
    assert rows[2].plain.strip() == "b"


def test_inline_math_invalid_keeps_content():
    out = _inline("<math>raw</math>")
    assert "raw" in out


def test_mathml_fraction_block_layout():
    out = _render("<math><mfrac><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow><mn>2</mn></mfrac></math>\n")
    assert "a + b" in out
    assert "2" in out


# ── Rich 路径同源 ────────────────────────────────────────


def test_rich_inline_renderer_handles_mathml():
    from src.renderer.inline_renderer import render_inline as rich_inline

    text = rich_inline("<math><msup><mi>x</mi><mn>2</mn></msup></math>")
    assert "x" in text.plain
    assert "²" in text.plain
    assert "<msup>" not in text.plain


def test_rich_inline_renderer_handles_progress():
    from src.renderer.inline_renderer import render_inline as rich_inline

    text = rich_inline('<progress value="50" max="100"></progress>')
    assert "50%" in text.plain
    assert "█" in text.plain
