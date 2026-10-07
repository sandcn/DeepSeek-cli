"""ANSI 数学渲染增强测试（TUI 流式路径，2026-10 扩展）。

覆盖 ``src/renderer/ansi/_math_*.py`` 的新增能力：

  - Unicode 数学字母族（``\\mathbb`` / ``\\mathcal`` / ``\\mathfrak`` /
    ``\\mathsf`` / ``\\mathtt`` / ``\\mathbf`` / ``\\boldsymbol``）；
  - 上下叠加标注（``\\overset`` / ``\\underset`` / ``\\stackrel``）；
  - 上下花括号（``\\overbrace`` / ``\\underbrace`` + 标注）；
  - 大算符上下限堆叠与极限函数下标（块级二维、行内紧凑）；
  - 矩阵/对齐环境（多行单元格、``array`` 列格式与竖线、``\\hline``、
    aligned / gather / multline / cases 族）；
  - 自动伸缩定界符（``\\left...\\right``）；
  - 着色盒 / 占位 / 尺寸线 / genfrac / 字体样式命令；
  - 流式分段写入与一次性写入结果一致、缓存复用与异常安全。
"""

from __future__ import annotations

from src.renderer.ansi.math import (
    render_math_block, render_math_inline, clear_math_cache,
)
from src.renderer.ansi import AnsiStreamRenderer


def _text(lines) -> str:
    return "\n".join(ln.plain for ln in lines)


def _block(src: str) -> str:
    return _text(render_math_block(src))


# ── Unicode 数学字母族 ────────────────────────────────────


def test_mathbb_letters():
    assert render_math_inline(r"\mathbb{R}").plain == "\u211d"
    assert render_math_inline(r"\mathbb{N} \subset \mathbb{Z}").plain == "\u2115 \u2282 \u2124"


def test_mathcal_and_mathfrak():
    assert render_math_inline(r"\mathcal{F}").plain == "\u2131"
    assert render_math_inline(r"\mathscr{L}").plain == "\u2112"
    assert render_math_inline(r"\mathfrak{g}").plain == "\U0001D524"


def test_mathbf_and_boldsymbol():
    assert render_math_inline(r"\mathbf{v}").plain == "\U0001D42F"
    assert render_math_inline(r"\boldsymbol{\alpha}").plain == "\u03b1"  # 非 ASCII → 回退内容


def test_mathsf_and_mathtt():
    assert render_math_inline(r"\mathsf{A}").plain == "\U0001D5A0"
    assert render_math_inline(r"\mathtt{x}").plain == "\U0001D6A1"


def test_unicode_alphabet_fallback_keeps_content():
    # 中文 / 希腊字母无法映射 → 保留原内容（不产生错误字形）
    assert "中文" in render_math_inline(r"\mathbb{中文}").plain


# ── 上下叠加 / 上下花括号 ─────────────────────────────────


def test_overset_block_stacks():
    rows = _block(r"\overset{def}{=} x").split("\n")
    assert any("def" in r for r in rows)
    assert any("=" in r for r in rows)
    assert rows.index(next(r for r in rows if "def" in r)) < \
        rows.index(next(r for r in rows if "=" in r))


def test_underset_block_stacks_below():
    rows = _block(r"\underset{n \to \infty}{a}").split("\n")
    assert any("a" in r for r in rows)
    assert any("\u2192" in r for r in rows)


def test_overset_block_stacks_before():
    rows = _block(r"\overset{a}{b}").split("\n")
    a_row = next(i for i, r in enumerate(rows) if "a" in r)
    b_row = next(i for i, r in enumerate(rows) if "b" in r)
    assert a_row < b_row


def test_stackrel_inline_compact():
    assert "=" in render_math_inline(r"\stackrel{def}{=}").plain


def test_overbrace_with_label():
    rows = _block(r"\overbrace{x + y}^{n}").split("\n")
    assert any("\u23de" in r for r in rows)     # ⏞
    assert any("n" in r for r in rows)


def test_underbrace_with_label():
    rows = _block(r"\underbrace{a + b}_{2c}").split("\n")
    assert any("\u23df" in r for r in rows)     # ⏟
    assert any("2c" in r for r in rows)


# ── 大算符 / 极限函数 ─────────────────────────────────────


def test_bigop_block_limits_stacked():
    rows = _block(r"\sum_{i=1}^{n} i").split("\n")
    assert any("\u2211" in r for r in rows)     # ∑
    assert any("n" in r for r in rows)
    assert any("i=1" in r for r in rows)


def test_bigop_inline_stays_compact():
    assert render_math_inline(r"\sum_{i=1}^{n} i").plain == "\u2211_{i=1}^{n} i"


def test_lim_block_subscript_below():
    rows = _block(r"\lim_{x \to 0} f(x)").split("\n")
    assert any("lim" in r for r in rows)
    assert any("x \u2192 0" in r for r in rows)


def test_lim_inline_stays_compact():
    assert render_math_inline(r"\lim_{x \to 0} f(x)").plain == "lim(x \u2192 0) f(x)"


# ── 矩阵 / 环境 ───────────────────────────────────────────


def test_matrix_cell_with_fraction_keeps_all_lines():
    text = _block(r"\begin{pmatrix} \frac{a}{b} & c \\ d & e \end{pmatrix}")
    assert "a" in text and "b" in text       # 分子/分母都在（不再只取首行）
    assert "c" in text and "d" in text and "e" in text
    assert "\u239b" in text                  # ⎛ 高左括号


def test_array_colspec_and_vline():
    text = _block(r"\begin{array}{c|c} a & b \end{array}")
    assert "\u2502" in text                  # │ 竖线


def test_array_hline():
    text = _block(r"\begin{array}{c|c} a & b \\ \hline c & d \end{array}")
    assert "\u2500" in text                  # ─ 水平线
    assert "\u253c" in text                  # ┼ 交叉
    assert "c" in text and "d" in text


def test_array_toprule_bottomrule():
    text = _block(r"\begin{array}{c} \toprule a \\ \bottomrule \end{array}")
    assert "\u2501" in text                  # ━ 粗线


def test_aligned_columns():
    text = _block(r"\begin{aligned} x &= 1 \\ y &= 2 \end{aligned}")
    assert "x" in text and "= 1" in text
    assert "y" in text and "= 2" in text


def test_gather_centered_rows():
    text = _block(r"\begin{gather} a = b \\ c = d \end{gather}")
    assert "a = b" in text and "c = d" in text


def test_multline_first_left_last_right():
    rows = _block(r"\begin{multline} loooooong \\ mid \\ s \end{multline}").split("\n")
    first = next(r for r in rows if "loooooong" in r)
    last = next(r for r in rows if r.strip().endswith("s │"))
    assert first.index("loooooong") < last.index("s")


def test_rcases_environment():
    text = _block(r"\begin{rcases} a & x > 0 \\ b & x \le 0 \end{rcases}")
    assert "\u23ab" in text or "}" in text   # ⎫ 或右花括号成分
    assert "\u2264" in text


def test_unknown_environment_keeps_content():
    assert "abc" in _block(r"\begin{fooenv} abc \end{fooenv}")


# ── 自动伸缩定界符 ────────────────────────────────────────


def test_left_right_tall_delimiters():
    rows = _block(r"\left(\frac{a}{b}\right)").split("\n")
    assert any("\u239b" in r for r in rows)   # ⎛
    assert any("\u239e" in r for r in rows)   # ⎞
    assert any("\u2500" in r for r in rows)   # 分数线


def test_left_right_single_line_unchanged():
    # 单行内容：括号保持普通字符（不换成高括号）
    plain = render_math_inline(r"\left( x \right)").plain
    assert plain.startswith("(") and plain.endswith(")")
    assert "x" in plain


def test_left_without_right_keeps_content():
    assert "x" in _block(r"\left( x").split("\n")[1]


# ── 着色 / 占位 / 尺寸 / genfrac ──────────────────────────


def test_colorbox_applies_background():
    inline = render_math_inline(r"\colorbox{yellow}{hi}")
    assert inline.plain == "hi"
    assert any(run.style is not None and run.style.bg is not None
               for run in inline.runs)


def test_fcolorbox_frames_content():
    inline = render_math_inline(r"\fcolorbox{red}{yellow}{hi}")
    assert "\u258c" in inline.plain          # ▌ 边框
    assert "hi" in inline.plain


def test_phantom_keeps_width():
    plain = render_math_inline(r"\phantom{abc}").plain
    assert plain.strip() == "" and len(plain) >= 3


def test_hphantom_and_vphantom():
    assert render_math_inline(r"\hphantom{ab}").plain == "  "
    block = render_math_block(r"\vphantom{\frac{a}{b}}")
    # 内容为空白占位行（去边框后为空）
    assert any(ln.plain.strip("│ ").strip() == "" for ln in block[1:-1])


def test_rule_renders_bar():
    assert "\u25ac" in render_math_inline(r"\rule{3em}{1pt}").plain


def test_genfrac_with_delimiters():
    text = render_math_inline(r"\genfrac{(}{)}{0pt}{}{a+b}{c}").plain
    assert "(a+b)" in text and "c" in text


def test_text_fonts_upright_vs_italic():
    upright = render_math_inline(r"\mathrm{d}x")
    assert upright.plain == "dx"
    runs = [r for r in upright.runs if r.text == "d"]
    assert runs and not runs[0].style.italic
    # ``\textit`` 为文本语义命令（保留斜体样式）
    italic = render_math_inline(r"\textit{d}x")
    runs = [r for r in italic.runs if r.text == "d"]
    assert runs and runs[0].style.italic


def test_mathit_uses_italic_glyphs():
    assert render_math_inline(r"\mathit{a}").plain == "\U0001D44E"


def test_monospace_font_style():
    assert render_math_inline(r"\mathtt{x}").plain == "\U0001D6A1"
    # 非 ASCII 内容回退等宽样式（内容不丢）
    fallback = render_math_inline(r"\mathtt{中文}")
    assert fallback.plain == "中文"
    assert any(run.style is not None and run.style.bold
               for run in fallback.runs)


# ── 流式一致性 / 缓存 / 异常 ─────────────────────────────


def test_stream_chunked_matches_single_write():
    md = (r"$$\n\sum_{i=1}^{n} \frac{1}{i} = \ln n + \gamma\n$$" "\n"
          r"$$\n\begin{array}{c|c} a & b \\ \hline c & d \end{array}" "\n$$\n")
    one = AnsiStreamRenderer(width=72)
    one.write(md)
    one.close()
    whole = [ln.plain for ln in one.take_lines()]

    chunked = AnsiStreamRenderer(width=72)
    for i in range(0, len(md), 3):
        chunked.write(md[i:i + 3])
    chunked.close()
    parts = [ln.plain for ln in chunked.take_lines()]
    assert parts == whole


def test_cache_reuses_enhanced_result():
    clear_math_cache()
    a = render_math_block(r"\overset{a}{=}")
    b = render_math_block(r"\overset{a}{=}")
    assert a is b


def test_preview_render_does_not_raise():
    r = AnsiStreamRenderer(width=72)
    r.write(r"$$\n\begin{bmatrix} \frac{1}{2} & x \\")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert preview
    r.write(r" y & z \end{bmatrix}\n$$\n")
    r.close()
