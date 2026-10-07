"""ANSI（TUI 流式）公式渲染全量特性测试。

覆盖第三批扩展：
  - 符号表补齐（KaTeX 别名 / 变体 / 函数 / 定界符）；
  - TeX 原语（``\\over`` / ``\\atop`` / ``\\choose`` / ``\\above`` /
    ``\\brace`` / ``\\brack`` 及其 withdelims 变体）；
  - 旧式结构（``\\matrix`` / ``\\cases`` / ``\\array`` / ``\\eqalign`` …）；
  - 宏系统（``\\def`` / ``\\gdef`` / ``\\newcommand`` / ``\\let`` /
    ``\\DeclareMathOperator`` / ``\\newenvironment`` / 条件与工具命令）；
  - HTML / 链接 / 盒子类命令；
  - 文本模式重音、重音扩展、字体变体、``\\char`` / ``\\unicode``；
  - 交换图 ``CD`` 环境、``\\cr`` / ``\\and`` / ``\\multicolumn``；
  - 流式（预览 + 提交）一致性。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi._math_latex import render_math_box
from src.renderer.ansi.math import (
    render_math_block, render_math_inline, clear_math_cache,
)
from src.renderer.ansi import AnsiStreamRenderer


@pytest.fixture(autouse=True)
def _isolate_macros():
    """每个用例前后重置全局宏状态（``\\gdef`` 会跨公式持久）。"""
    clear_math_cache()
    yield
    clear_math_cache()


def _inline(src: str) -> str:
    return render_math_inline(src).plain


def _block(src: str) -> str:
    return render_math_block(src).__class__ and "\n".join(
        ln.plain for ln in render_math_box(src, inline=False).lines)


# ── 符号表补齐 ──────────────────────────────────────────


def test_greek_and_symbol_aliases():
    assert _inline(r"\varUpsilon") == "\u03d2"
    assert _inline(r"\thetasym") == "\u03d1"
    assert _inline(r"\Coppa") == "\u03d8"
    assert _inline(r"\R") == "\u211d"
    assert _inline(r"\natnums") == "\u2115"
    assert _inline(r"\Complex") == "\u2102"


def test_relation_aliases():
    assert _inline(r"\asymp") == "\u224d"
    assert _inline(r"\vDash") == "\u22a8"
    assert _inline(r"\precapprox") == "\u2ab7"
    assert _inline(r"\succcurlyeq") == "\u227d"
    assert _inline(r"\coloneqq") == "\u2254"
    assert _inline(r"\eqcolon") == "\u2239"


def test_operator_and_arrow_aliases():
    assert _inline(r"\plusmn") == "\u00b1"
    assert _inline(r"\And") == "&"
    assert _inline(r"\rArr") == "\u21d2"
    assert _inline(r"\larr") == "\u2190"
    assert _inline(r"\dArr") == "\u21d3"
    assert _inline(r"\uArr") == "\u21d1"


def test_function_aliases():
    assert _inline(r"\arctg x") == "arctg x"
    assert _inline(r"\cosec y") == "cosec y"
    assert "arg max" in _inline(r"\argmax_x f")


def test_big_operator_additions():
    assert _inline(r"\iiiint") == "\u2a0c"
    assert _inline(r"\intop") == "\u222b"
    assert _inline(r"\smallint") == "\u222b"


def test_delimiter_aliases():
    assert _inline(r"\vert") == "|"
    assert _inline(r"\Vert") == "\u2016"
    assert _inline(r"\lparen x\rparen") == "( x)"
    assert _inline(r"\lBrace") == "\u2983"
    assert _inline(r"\lang") == "\u27e8"
    assert _inline(r"\llbracket") == "\u27e6"


# ── TeX 原语 ────────────────────────────────────────────


def test_tex_over_primitive_in_group():
    text = _block(r"{a \over b}")
    assert "/" not in text
    assert "a" in text and "b" in text
    assert "\u2500" in text  # 分数线


def test_tex_atop_choose_brace_brack():
    assert "\u2500" not in _block(r"{a \atop b}")
    assert "\u239b" in _block(r"{n \choose k}")  # ⎛ 高括号
    assert "\u23a7" in _block(r"{n \brace k}")   # ⎧ 高花括号
    assert "\u23a1" in _block(r"{n \brack k}")   # ⎡ 高方括号


def test_tex_over_withdelims():
    text = _block(r"{a \overwithdelims() b}")
    assert "\u239b" in text and "\u239e" in text and "\u2500" in text


def test_tex_above_primitive():
    text = _block(r"{a \above 1pt b}")
    assert "\u2500" in text


def test_tex_over_primitive_at_top_level():
    text = _block(r"a \over b")
    assert "\u2500" in text


# ── 旧式结构 ────────────────────────────────────────────


def test_legacy_matrix_and_cases():
    text = _block(r"\matrix{a & b \\ c & d}")
    assert "a" in text and "d" in text and text.count("\n") == 1
    assert "\u23a7" in _block(r"\cases{a & b \\ c & d}")


def test_legacy_displaylines_and_eqalign():
    assert _block(r"\displaylines{a \\ b}").count("\n") == 1
    assert "=" in _block(r"\eqalign{a &= b \\ c &= d}")


def test_legacy_eqalignno_tags():
    text = _block(r"\eqalignno{a &= b & (1) \\ c &= d & (2)}")
    assert "(1)" in text and "(2)" in text


# ── 位置与盒子命令 ──────────────────────────────────────


def test_position_commands_keep_content():
    assert "x" in _inline(r"\raise 1em x")
    assert "x" in _inline(r"\lower 1em x")
    assert "x" in _inline(r"\moveleft 1em x")
    assert "x" in _inline(r"\shoveleft x")


def test_skew_and_vcenter_and_rule():
    assert "\u0302" in _inline(r"\skew 6\hat{a}")
    assert "x" in _inline(r"\vcenter{x}")
    assert "\u2502" in _inline(r"\vrule")
    assert "\u25ac" in _inline(r"\Rule{3}{1}")


# ── 宏系统 ──────────────────────────────────────────────


def test_def_and_gdef():
    assert _inline(r"\def\foo{x^2}\foo+\foo") == "x\u00b2+x\u00b2"
    assert _inline(r"\gdef\foo{y}y=\foo") == "y=y"


def test_newcommand_with_args_and_optional():
    assert _inline(r"\newcommand{\vv}[1]{\vec{#1}}\vv{F}") == "F\u20d7"
    assert _inline(r"\newcommand{\sq}[2][2]{#1^{#2}}\sq{3}") == "2\u00b3"


def test_renewcommand_and_providecommand():
    assert _inline(r"\newcommand{\qone}{1}\renewcommand{\qone}{2}\qone") == "2"
    assert _inline(
        r"\providecommand{\qtwo}{1}\providecommand{\qtwo}{2}\qtwo") == "1"


def test_let_and_declare_math_operator():
    assert _inline(r"\let\a=\alpha\a") == "\u03b1"
    text = _inline(r"\DeclareMathOperator{\rank}{rank}\rank A")
    assert "rank" in text and "A" in text


def test_newenvironment_custom():
    text = _block(r"\newenvironment{boxy}{\left[}{\right]}"
                  r"\begin{boxy} x \end{boxy}")
    assert "[" in text and "x" in text and "]" in text


def test_conditionals_keep_true_branch():
    out = _inline(r"\iftrue a\else b\fi")
    assert "a" in out and "b" not in out
    assert _inline(r"\ifx\a\a yes\else no\fi").strip() == "yes"


def test_utility_commands():
    assert "1" in _inline(r"\mathchoice{1}{2}{3}{4}")
    assert "b" in _inline(r"\TextOrMath{a}{b}")
    assert "x" in _inline(r"\@firstoftwo{x}{y}")


def test_builtin_macros_bra_ket_set():
    assert _inline(r"\bra{\phi}") == "\u27e8\u03c6|"
    assert _inline(r"\ket{\psi}") == "|\u03c8\u27e9"
    assert _inline(r"\braket{a}") == "\u27e8a\u27e9"
    assert _inline(r"\set{x}") == "{x}"
    assert _inline(r"\TeX") == "TeX"
    assert _inline(r"\LaTeX") == "LaTeX"
    assert _inline(r"\KaTeX") == "KaTeX"


def test_builtin_macros_persist_gdef_across_calls():
    clear_math_cache()
    render_math_block(r"\gdef\RR{\mathbb{R}}")
    assert _inline(r"\RR^2") == "\u211d\u00b2"


# ── HTML / 链接 / 盒子 ──────────────────────────────────


def test_href_and_url():
    clear_math_cache()
    assert _inline(r"\href{https://x}{\alpha}") == "\u03b1"
    assert _inline(r"\url{https://x.y}") == "https://x.y"


def test_includegraphics_alt():
    assert _inline(r"\includegraphics[alt=logo]{pic.png}") == "[图片: logo]"
    assert _inline(r"\includegraphics{a/b/c.png}") == "[图片: c.png]"


def test_html_wrappers_render_content():
    assert _inline(r"\htmlId{id}{x}") == "x"
    assert _inline(r"\htmlClass{cls}{y}") == "y"
    assert _inline(r"\htmlStyle{color:red}{z}") == "z"
    assert _inline(r"\htmlData{a=b}{w}") == "w"
    assert _inline(r"\mmlToken{tok}{m}") == "m"


def test_tip_wrappers():
    assert _inline(r"\texttip{shown}{tip}") == "shown"
    assert _inline(r"\mathtip{math}{tip}") == "math"


def test_bbox_and_enclose_and_phase():
    assert _inline(r"\bbox[5px]{x}") == "x"
    assert "q" in _inline(r"\enclose{circle}{q}")
    assert _inline(r"\phase{z}") == "\u2221z"


def test_textcircled_and_operatornamewithlimits():
    assert "\u20dd" in _inline(r"\textcircled{a}")
    assert "f" in _inline(r"\operatornamewithlimits{f}")


def test_transform_wrappers():
    assert _inline(r"\reflectbox{x}") == "x"
    assert _inline(r"\scalebox{2}{y}") == "y"
    assert _inline(r"\rotatebox{90}{z}") == "z"
    assert _inline(r"\resizebox{1em}{1em}{w}") == "w"
    assert _inline(r"\raisebox{1em}{v}") == "v"


# ── 重音 / 字体 / 字符 ──────────────────────────────────


def test_text_accents():
    assert _inline(r"\u{a}") == "a\u0306"
    assert _inline(r"\c{c}") == "c\u0327"
    assert _inline(r"\H{o}") == "o\u030b"
    assert _inline(r"\r{a}") == "a\u030a"
    assert _inline(r"\k{a}") == "a\u0328"
    assert _inline(r"\b{a}") == "a\u0331"
    assert _inline(r"\d{a}") == "a\u0323"
    assert _inline(r"\t{oo}") == "o\u0361o"


def test_text_accent_chars():
    assert _inline(r"\'{a}") == "a\u0301"
    assert _inline(r"\^{a}") == "a\u0302"
    assert _inline(r"\"{o}") == "o\u0308"
    assert _inline(r"\~{n}") == "n\u0303"
    assert _inline(r"\={e}") == "e\u0304"
    assert _inline(r"\.{z}") == "z\u0307"
    assert _inline(r"\`{a}") == "a\u0300"


def test_accent_extensions():
    assert "\u20db" in _inline(r"\dddot{x}")
    assert "\u20dc" in _inline(r"\ddddot{x}")
    assert "abc" in _block(r"\wideparen{abc}")
    assert "AB" in _block(r"\overlinesegment{AB}")


def test_font_variants():
    assert _inline(r"\mathbold{B}") == "\U0001D401"
    assert _inline(r"\mathsfit{C}") == "\U0001D60A"
    assert _inline(r"\mathup{Ab}") == "Ab"
    assert _inline(r"\pmb{x}") == "x"


def test_char_and_unicode():
    assert _inline('\\char"263a') == "\u263a"
    assert _inline(r"\unicode{x263a}") == "\u263a"


def test_operatorname_processes_spacing():
    assert _inline(r"\operatorname{arg\,max}") == "arg max"


def test_text_with_inline_math():
    assert _inline(r"\text{a $x^2$ b}") == "a x\u00b2 b"
    assert _inline(r"\hbox{if $n>0$ then}") == "if n>0 then"


# ── 环境 ────────────────────────────────────────────────


def test_cd_environment():
    src = (r"\begin{CD} A @>a>> B \\ @VbVV @AcA \\ C @= D \end{CD}")
    text = _block(src)
    assert "A" in text and "B" in text and "D" in text
    assert "\u25b6" in text or "\u2192" in text or "\u2500" in text


def test_alignat_and_dcases_and_subarray():
    assert "=" in _block(r"\begin{alignat}{2} a &= b \end{alignat}")
    assert "{" in _block(r"\begin{dcases} a & b \end{dcases}")
    assert "}" in _block(r"\begin{drcases} a & b \end{drcases}")
    assert _block(r"\begin{subarray}{l} a \\ b \end{subarray}").count("\n") == 1


def test_table_cr_and_and_and_multicolumn():
    text = _block(r"\begin{array}{cc} a & b \cr c & d \end{array}")
    assert text.count("\n") == 1
    text2 = _block(r"\begin{array}{cc} a \and b \end{array}")
    assert "a" in text2 and "b" in text2
    text3 = _block(r"\begin{array}{cc}\multicolumn{2}{c}{wide} \end{array}")
    assert "wide" in text3


def test_display_math_hard_line_break():
    text = _block(r"a \\ b")
    assert "a" in text and "b" in text and text.count("\n") == 1


# ── KaTeX auto-render 显示环境（无 ``$$`` 定界符） ───────


@pytest.mark.parametrize("env", ["align", "gather", "equation", "CD"])
def test_auto_render_display_env_block(env):
    md = "正文\n\n\\begin{%s}\na &= b\n\\end{%s}\n\n尾巴\n" % (env, env)
    r = AnsiStreamRenderer(width=72)
    r.write(md)
    r.close()
    text = "\n".join(ln.plain for ln in r.take_lines())
    assert "数学公式" in text
    assert "正文" in text and "尾巴" in text
    assert "\\begin" not in text


def test_auto_render_single_line_env():
    r = AnsiStreamRenderer(width=72)
    r.write("\\begin{align} a &= b \\end{align}\n")
    r.close()
    text = "\n".join(ln.plain for ln in r.take_lines())
    assert "数学公式" in text and "\\end" not in text


def test_auto_render_env_stream_preview_updates():
    r = AnsiStreamRenderer(width=72)
    r.write("\\begin{align}\na &= b \\\\\n")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert any("数学公式" in p for p in preview)
    r.write("c &= d\n\\end{align}\n")
    r.close()
    text = "\n".join(ln.plain for ln in r.take_lines())
    assert "b" in text and "d" in text


def test_non_display_env_not_treated_as_math_block():
    md = "\\begin{itemize}\n\\item x\n\\end{itemize}\n"
    r = AnsiStreamRenderer(width=72)
    r.write(md)
    r.close()
    text = "\n".join(ln.plain for ln in r.take_lines())
    assert "数学公式" not in text


def test_displaystyle_inline_bigop_stacks():
    inline = _inline(r"\displaystyle\sum_{i=1}^{n} i")
    assert "\n" not in inline  # 行内展平后仍为单行
    assert "i=1" in inline.replace("\u2009", "") or "n" in inline


def test_limits_and_nolimits():
    # 行内 ``\limits`` 强制上下限堆叠；``\nolimits`` 强制紧凑
    assert "\n" in _block(r"\sum\limits_{i=1}^{n} i")
    assert "\n" not in _inline(r"\sum\nolimits_{i=1}^{n} i")
    assert "x\u21920" in _inline(r"\lim\nolimits_{x\to0} f").replace(" ", "")


# ── 流式一致性 ──────────────────────────────────────────


STREAM_CASES = [
    r"$$\{a \over b\}$$",
    r"$$\matrix{a & b \\ c & d}$$",
    "$$\n\\begin{CD}\nA @>a>> B \\\\\n@VbVV @AcA \\\\\nC @= D\n\\end{CD}\n$$",
    r"$$\def\foo{x^2}\foo + \foo$$",
    r"$$\newcommand{\vv}[1]{\vec{#1}}\vv{F}$$",
    r"$$\text{if $x>0$ then }y$$",
    r"$$\bra{\phi}\ket{\psi} \set{x}$$",
    r"$$\href{https://x}{\alpha} \url{https://y}$$",
    r"$$\includegraphics[alt=logo]{p.png}$$",
    "$$\\char\"263a + \\unicode{x263a}$$",
    r"$$\u{a}\c{c}\H{o}\r{a}$$",
    r"$$\'\i \~\i \`a \=e$$",
    r"$$\dddot{x} + \wideparen{ab}$$",
    r"$$\textcircled{a} \phase{z}$$",
    r"$$\xmlId{x}$$".replace("xmlId", "htmlId"),
    r"$$\prescript{14}{6}{C}$$",
    r"$$\begin{align} a &= b \\ c &= d \end{align}$$",
    "行内 $\\{a \\over b\\}$ 与 $\\char\"263a$ 与 $\\text{a $b$ c}$。",
]


@pytest.mark.parametrize("md", STREAM_CASES)
def test_stream_chunked_matches_single_write(md):
    one = AnsiStreamRenderer(width=72)
    one.write(md)
    one.close()
    whole = [ln.plain for ln in one.take_lines()]

    for step in (1, 3, 7):
        chunked = AnsiStreamRenderer(width=72)
        for i in range(0, len(md), step):
            chunked.write(md[i:i + step])
            chunked.take_preview_lines()  # 预览不抛异常
        chunked.close()
        assert [ln.plain for ln in chunked.take_lines()] == whole


def test_stream_preview_renders_new_features():
    r = AnsiStreamRenderer(width=72)
    r.write("$$\n\\begin{CD}\nA @>a>> B \\\\")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert preview
    r.write("\n\\end{CD}\n$$\n")
    r.close()
    text = "\n".join(ln.plain for ln in r.take_lines())
    assert "A" in text and "B" in text


def test_stream_preview_partial_tex_primitive():
    r = AnsiStreamRenderer(width=72)
    r.write(r"$$\frac{a}{b} \over")
    assert [ln.plain for ln in r.take_preview_lines()]
    r.write(" c\n$$\n")
    r.close()
    assert any("数学公式" in ln.plain for ln in r.take_lines())


# ── 缓存与鲁棒性 ────────────────────────────────────────


def test_cache_key_includes_macro_state():
    clear_math_cache()
    render_math_block(r"\gdef\M{x}")
    first = render_math_inline(r"\M").plain
    render_math_block(r"\gdef\M{y}")
    second = render_math_inline(r"\M").plain
    assert first == "x"
    assert second == "y"


def test_unknown_command_kept_verbatim():
    assert r"\unknowncmd" in _inline(r"\unknowncmd x")


def test_empty_and_whitespace_sources():
    assert render_math_block("") is not None
    assert render_math_inline("") is not None


def test_no_command_text_residue_for_common_features():
    samples = [
        r"{a \over b}", r"{n \choose k}", r"\matrix{a & b}",
        r"\def\z{1}\z", r"\bra{x}", r"\href{u}{t}",
        r"\u{a}", r"\char\"41", r"\begin{CD} A @>a>> B \end{CD}",
    ]
    for src in samples:
        out = _block(src)
        assert "\\over" not in out
        assert "\\choose" not in out
        assert "\\bra" not in out
        assert "\\href" not in out
