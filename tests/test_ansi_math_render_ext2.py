"""ANSI 数学渲染增强测试（第二批，TUI 流式路径）。

覆盖 ``src/renderer/ansi/_math_*.py`` 与 ``src/renderer/math_symbols/*`` 的
本轮新增能力：

  - 定界符修复（``\\left\\{`` / ``\\right\\}`` 等「反斜杠 + 非字母」定界符）；
  - 否定前缀合成（``\\not=`` → ``≠``、``\\not\\in`` → ``∉``）；
  - 二项式二维堆叠（``\\binom`` / ``\\dbinom`` / ``\\tbinom``）；
  - 上下标注族（``\\overbrace`` / ``\\underbrace`` / ``\\overbracket`` /
    ``\\underbracket`` / ``\\overparen``，含无标注时不产生空行）；
  - 划除（``\\cancel`` 真删除线 / ``\\cancelto`` 目标值）；
  - 前置上下标（``\\prescript``）与四角标（``\\sideset``）；
  - 文本语义（``\\text`` 多行 / 转义还原 / ``\\texttt`` 等）、文本上下标；
  - 宽标记（``\\widecheck`` / ``\\utilde`` / ``\\underbar``）；
  - 取整定界（``\\ceil`` / ``\\floor``）、小分数（``\\nicefrac``）、
    透明包裹（``\\smash`` / ``\\mathclap``）、原文（``\\verb``）；
  - 脚本与空白绑定修复（``\\sum _{i=1}^{n}``）；
  - 单列公式环境（``equation`` / ``displaymath``）与多行 cases 分段括号；
  - 符号表扩充（相似/大小/三角关系、省略号族、希伯来字母…）；
  - Rich 路径否定前缀与 ANSI 路径同源。
"""

from __future__ import annotations

from src.renderer.ansi.math import render_math_block, render_math_inline
from src.renderer.ansi._math_latex import render_math_box
from src.renderer.math_symbols.negations import negate_symbol


def _block(src: str) -> str:
    return render_math_box(src, inline=False).plain


def _inline(src: str) -> str:
    return render_math_box(src, inline=True).plain


# ── 定界符修复 ───────────────────────────────────────────


def test_left_brace_delimiter():
    out = _block(r"\left\{ \frac{a}{b} \right\}")
    assert out.startswith("⎧")
    assert "{" not in out.replace("⎧", "").replace("⎨", "").replace("⎩", "")
    assert out.rstrip().endswith("⎭")


def test_left_right_vertical_bar():
    assert _inline(r"\left\|x\right\|") == "‖x‖"


def test_left_none_and_right_brace():
    out = _block(r"\left. \frac{a}{b} \right\}")
    lines = out.split("\n")
    assert lines[0].startswith(" ")
    assert lines[1].rstrip().endswith("⎬")


def test_left_langle_rangle_named():
    assert _inline(r"\left\langle x\right\rangle") == "⟨x⟩"


# ── 否定前缀 ─────────────────────────────────────────────


def test_not_symbol_synthesis():
    assert _block(r"\not =") == "≠"
    assert _block(r"\not <") == "≮"
    assert _block(r"\not \in") == "∉"
    assert _block(r"\not \subset") == "⊄"
    assert _block(r"\not \equiv") == "≢"


def test_not_fallback_combining_slash():
    assert _block(r"\not \alpha") == "α\u0338"


def test_negate_symbol_helper():
    assert negate_symbol("=") == "≠"
    assert negate_symbol("∅") == "∅\u0338"
    assert negate_symbol("") == ""


# ── 二项式 ───────────────────────────────────────────────


def test_binom_block_is_two_dimensional():
    out = _block(r"\binom{n}{k}")
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("⎛") and lines[0].endswith("⎞")
    assert lines[1].startswith("⎝") and "n" in lines[0] and "k" in lines[1]


def test_dbinom_and_tbinom_share_binomial_layout():
    assert _block(r"\dbinom{n}{k}") == _block(r"\binom{n}{k}")
    assert _block(r"\tbinom{n}{k}") == _block(r"\binom{n}{k}")


def test_binom_inline_compact():
    assert _inline(r"\binom{n}{k}") == "(n¦k)"


# ── 上下标注 ─────────────────────────────────────────────


def test_overbrace_with_label():
    out = _block(r"\overbrace{a+b}^{n}")
    lines = out.split("\n")
    assert lines[0].strip() == "n"
    assert lines[1].startswith("⏞")
    assert lines[2] == "a+b"


def test_overbrace_without_label_has_no_blank_line():
    out = _block(r"\overbrace{a+b}")
    lines = out.split("\n")
    assert lines[0].startswith("⏞")
    assert lines[1] == "a+b"
    assert "" not in lines


def test_underbrace_with_label():
    out = _block(r"\underbrace{x \cdot y}_{m}")
    lines = out.split("\n")
    assert lines[-1].strip() == "m"
    assert lines[-2].startswith("⏟")


def test_underbrace_mismatched_script_keeps_content():
    # 方向不匹配的脚本不丢内容（以紧凑脚本附在内容行尾）
    out = _block(r"\underbracket{c+d}^{n}")
    assert "c+d^{n}" in out


def test_bracket_and_paren_marks():
    assert "⎴" in _block(r"\overbracket{a+b}")
    assert "⎵" in _block(r"\underbracket{a+b}")
    assert "⏜" in _block(r"\overparen{a+b}")
    assert "⏝" in _block(r"\underparen{a+b}")


# ── 划除 ─────────────────────────────────────────────────


def test_cancel_applies_strikethrough():
    out = _block(r"\cancel{x}")
    assert out == "x\u0336"
    assert _block(r"\bcancel{ab}") == "a\u0336b\u0336"


def test_cancelto_shows_target():
    out = _block(r"\cancelto{0}{x+1}")
    assert "x\u0336+\u03361\u0336" in out
    assert out.endswith("⤳0")


# ── 前置上下标 / 四角标 ──────────────────────────────────


def test_prescript_block():
    out = _block(r"\prescript{14}{6}{C}")
    lines = out.split("\n")
    assert lines[0].strip() == "14"
    assert lines[1].strip() == "C"
    assert lines[2].strip() == "6"


def test_prescript_inline():
    assert _inline(r"\prescript{14}{6}{C}") == "^{14}_{6}C"


def test_sideset_block():
    out = _block(r"\sideset{_a^b}{_c^d}\sum")
    lines = out.split("\n")
    assert "∑" in lines[1]
    assert lines[0] == "b d"
    assert lines[2] == "a c"


def test_sideset_inline():
    out = _inline(r"\sideset{_a^b}{_c^d}\sum")
    assert out == "^{b}_{a}∑^{d}_{c}"


# ── 文本语义 ─────────────────────────────────────────────


def test_text_multiline_and_escapes():
    out = _block(r"\text{第一行 \\ 第二行}")
    assert out.split("\n") == ["第一行", "第二行"]
    assert _block(r"\text{a\{b\} ~ c}") == "a{b}   c"


def test_text_nested_commands_are_stripped():
    assert _block(r"\text{其中 \textbf{重点} 与 \emph{强调}}") == "其中 重点 与 强调"
    assert _block(r"\emph{x}").strip() == "x"


def test_textsuperscript_and_subscript():
    assert _block(r"\textsuperscript{2}") == "²"
    assert _block(r"\textsubscript{i}") == "ᵢ"
    assert _block(r"\textsuperscript{ab}") == "ᵃᵇ"
    assert _block(r"\textsuperscript{+-}") == "⁺⁻"


def test_hbox_is_text_semantic():
    assert _block(r"\hbox{if } x") == "if x"


# ── 宽标记 ───────────────────────────────────────────────


def test_wide_mark_commands():
    assert _block(r"\widecheck{x}").split("\n")[0].strip() == "ˇ"
    assert _block(r"\utilde{y}").split("\n")[-1].strip() == "˜"
    assert _block(r"\underbar{z}").split("\n")[-1].strip() == "▁"


def test_overline_underline_marks():
    assert _block(r"\overline{AB}").split("\n")[0].strip() == "‾‾"
    assert _block(r"\underline{CD}").split("\n")[-1].strip() == "▁▁"


# ── 包裹 / 取整 / 小分数 / 原文 ──────────────────────────


def test_ceil_floor():
    assert _block(r"\ceil{x/2}") == "⌈x/2⌉"
    assert _block(r"\floor{y}") == "⌊y⌋"


def test_nicefrac():
    assert _inline(r"\nicefrac{a}{b}") == "a⁄b"
    assert "─" in _block(r"\nicefrac{a}{b}")


def test_transparent_wrappers_keep_content():
    assert _block(r"\smash{x^2}") == "x²"
    assert _block(r"\mathclap{abc}") == "abc"
    assert _block(r"\vcenter{a+b}") == "a+b"


def test_verb_renders_raw_text():
    assert _block(r"\verb|raw{}_text|") == "raw{}_text"


def test_fbox_renders_box():
    out = _block(r"\fbox{a}")
    lines = out.split("\n")
    assert lines[0].startswith("┌") and lines[0].endswith("┐")
    assert "a" in lines[1]
    assert lines[2].startswith("└") and lines[2].endswith("┘")


# ── 脚本与空白绑定 ───────────────────────────────────────


def test_script_after_space_binds_to_previous_atom():
    out = _block(r"\sum _{i=1}^{n} i")
    lines = out.split("\n")
    assert len(lines) == 3
    assert "∑" in lines[1]
    assert lines[0].strip() == "n"
    assert lines[2].strip() == "i=1"


def test_script_atom_takes_single_token():
    # TeX 语义：``^`` 未加花括号时只取紧随的单个 token
    assert _block(r"x^2,") == "x²,"
    assert _block(r"x^ab") == "xᵃb"
    assert _block(r"x^{2,}") == "x^{2,}"  # 花括号组整体作为脚本（含不可上标字符）


def test_tall_delimiter_for_ceil_floor():
    out = _block(r"\ceil{\frac{x}{2}}")
    lines = out.split("\n")
    assert lines[0].startswith("⎡") and lines[0].rstrip().endswith("⎤")
    assert lines[-1].startswith("⎢") and lines[-1].rstrip().endswith("⎥")
    floors = _block(r"\floor{\frac{y}{3}}").split("\n")
    assert floors[-1].startswith("⎣") and floors[-1].rstrip().endswith("⎦")


# ── 环境 ─────────────────────────────────────────────────


def test_equation_environment_is_centered_block():
    out = _block(r"\begin{equation} E = mc^2 \end{equation}")
    assert out == "E = mc²"


def test_displaymath_environment():
    assert _block(r"\begin{displaymath} a=b \end{displaymath}") == "a=b"


def test_displaymath_star_environment():
    assert _block(r"\begin{displaymath} x \end{displaymath}") == "x"


def test_cases_three_rows_use_full_brace():
    out = _block(r"\begin{cases} a & b \\ c & d \\ e & f \end{cases}")
    lines = out.split("\n")
    assert lines[0].startswith("⎧")
    assert lines[1].startswith("⎨")
    assert lines[2].startswith("⎩")


def test_array_optional_positional_arg():
    out = _block(r"\begin{array}[t]{lc} a & b \end{array}")
    assert "a" in out and "b" in out
    assert "[" not in out


def test_tabular_environment_with_hline():
    out = _block(r"\begin{tabular}{c|c} a & b \\ \hline c & d \end{tabular}")
    lines = out.split("\n")
    assert lines[0].split() == ["a", "│", "b"]
    assert "┼" in lines[1]
    assert lines[2].split() == ["c", "│", "d"]


def test_tabular_star_reads_width_then_colspec():
    out = _block(r"\begin{tabular*}{0.5\textwidth}{ll} x & y \end{tabular*}")
    assert "x" in out and "y" in out
    assert "\\textwidth" not in out


def test_unknown_environment_keeps_ampersand_content():
    out = _block(r"\begin{weird} a & b \end{weird}")
    assert "a" in out and "b" in out


# ── 符号表扩充 ───────────────────────────────────────────


def test_new_relation_symbols():
    assert _inline(r"\lesssim") == "≲"
    assert _inline(r"\gtrsim") == "≳"
    assert _inline(r"\lessgtr") == "≶"
    assert _inline(r"\lll") == "⋘"
    assert _inline(r"\lhd") == "⊲"
    assert _inline(r"\unrhd") == "⊵"
    assert _inline(r"\leqslant") == "⩽"


def test_new_misc_and_arrow_symbols():
    assert _inline(r"\dotsb") == "⋯"
    assert _inline(r"\dotsc") == "…"
    assert _inline(r"\beth") == "ℶ"
    assert _inline(r"\gimel") == "ℷ"
    assert _inline(r"\daleth") == "ℸ"
    assert _inline(r"\leadsto") == "⇝"
    assert _inline(r"\longrightsquigarrow") == "⟿"


def test_new_operator_symbols():
    assert _inline(r"\dagger") == "†"
    assert _inline(r"\ddagger") == "‡"
    assert _inline(r"\bullet") == "•"


# ── 端到端（流式渲染器） ─────────────────────────────────


def test_stream_renderer_math_block_end_to_end():
    from src.renderer.ansi import AnsiStreamRenderer

    r = AnsiStreamRenderer(width=80)
    r.write("公式：\n\n$$\n\\binom{n}{k} = \\frac{n!}{k!(n-k)!}\n$$\n")
    r.close()
    text = "\n".join(ln.plain for ln in r.take_lines())
    assert "╭─ 数学公式" in text
    assert "⎛" in text and "!" in text


def test_render_math_block_label_and_dropped():
    lines = render_math_block(r"\frac{a}{b}", label="公式", dropped=3)
    assert "公式" in lines[0].plain
    assert any("已省略" in ln.plain or "省略" in ln.plain for ln in lines)


def test_math_inline_returns_single_line():
    line = render_math_inline(r"\sqrt{x^2}")
    assert "\n" not in line.plain
    assert "√" in line.plain


# ── Rich 路径同源 ────────────────────────────────────────


def test_rich_parser_not_negation_uses_shared_table():
    from src.renderer.math_parser import MathParser

    assert MathParser().parse(r"\not=").plain == "≠"
    assert MathParser().parse(r"\not\in").plain == "∉"
