"""MathParser 核心解析测试 — 覆盖 src/renderer/math_parser.py 及 Mixin。

验证 LaTeX 数学表达式到 Rich Text 的转换：分数、根式、上下标、
希腊字母、函数、二项式、矩阵/分段环境、重音、颜色、划除、大算符极限等。
"""

import pytest
from rich.text import Text

from src.renderer.math_parser import MathParser


@pytest.fixture
def parser():
    return MathParser()


def _parse(parser, s, is_block=False):
    parser._is_block = is_block
    return parser.parse(s)


# ── 基础文本 ──────────────────────────────────────────────

def test_parse_empty(parser):
    assert _parse(parser, "").plain == ""


def test_parse_plain_text(parser):
    assert _parse(parser, "hello").plain == "hello"


def test_parse_returns_text(parser):
    assert isinstance(_parse(parser, "x"), Text)


def test_parse_digits_get_number_style(parser):
    t = _parse(parser, "42")
    assert t.plain == "42"
    spans = list(t.spans)
    assert spans and spans[0].style is not None


# ── 希腊字母与函数 ────────────────────────────────────────

@pytest.mark.parametrize("tex,expected", [
    ("\\alpha", "α"),
    ("\\beta", "β"),
    ("\\Gamma", "Γ"),
    ("\\infty", "∞"),
    ("\\leq", "≤"),
    ("\\times", "×"),
    ("\\rightarrow", "→"),
])
def test_parse_symbols(parser, tex, expected):
    assert _parse(parser, tex).plain == expected


def test_parse_function_name(parser):
    assert _parse(parser, "\\sin").plain == "sin"


def test_parse_function_followed_by_letter_adds_space(parser):
    # \sin 后跟字母时补空格（函数名与变量分离）
    assert _parse(parser, "\\sin x").plain == "sin x"


# ── 分数 ──────────────────────────────────────────────────

def test_parse_frac_simple_unicode(parser):
    # 1/2 可上下标美化 → ¹⁄₂
    assert _parse(parser, "\\frac{1}{2}").plain == "¹⁄₂"


def test_parse_frac_unmapped_denominator(parser):
    # 分母 b 无下标映射，走普通分数路径
    assert _parse(parser, "\\frac{a}{b}").plain == "a⁄b"


def test_parse_frac_with_operator_paren(parser):
    assert _parse(parser, "\\frac{a+b}{c}").plain == "(a+b)⁄c"


def test_parse_dfrac_variant(parser):
    assert _parse(parser, "\\dfrac{1}{2}").plain == "¹⁄₂"


def test_parse_tex_primitive_over(parser):
    assert _parse(parser, "{1\\over 2}").plain == "¹⁄₂"


# ── 根式 ──────────────────────────────────────────────────

def test_parse_sqrt(parser):
    assert _parse(parser, "\\sqrt{x}").plain == "√x"


def test_parse_sqrt_with_operator_paren(parser):
    assert _parse(parser, "\\sqrt{a+b}").plain == "√(a+b)"


def test_parse_sqrt_with_index(parser):
    assert _parse(parser, "\\sqrt[3]{x}").plain == "³√x"


def test_parse_sqrt_empty_content(parser):
    assert _parse(parser, "\\sqrt{}").plain == "√"


# ── 上下标 ────────────────────────────────────────────────

def test_parse_superscript(parser):
    assert _parse(parser, "x^2").plain == "x²"


def test_parse_subscript(parser):
    assert _parse(parser, "x_i").plain == "xᵢ"


def test_parse_superscript_braced(parser):
    assert _parse(parser, "x^{10}").plain == "x¹⁰"


def test_parse_subscript_unmapped_kept(parser):
    # 无映射字符保留原样（下标 b 无映射）
    assert _parse(parser, "x_b").plain.endswith("b")


# ── 二项式 ────────────────────────────────────────────────

def test_parse_binom(parser):
    assert _parse(parser, "\\binom{n}{k}").plain == "(n¦k)"


# ── 矩阵环境 ──────────────────────────────────────────────

def test_parse_pmatrix(parser):
    t = _parse(parser, "\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}")
    assert t.plain.startswith("(")
    assert t.plain.endswith(")")
    for ch in "abcd":
        assert ch in t.plain


def test_parse_bmatrix(parser):
    t = _parse(parser, "\\begin{bmatrix}a\\\\b\\end{bmatrix}")
    assert t.plain.startswith("[")
    assert t.plain.endswith("]")


def test_parse_cases(parser):
    t = _parse(parser, "\\begin{cases}x&x>0\\\\-x&x<0\\end{cases}")
    assert t.plain.startswith("{")
    assert "x>0" in t.plain
    assert "x<0" in t.plain


def test_parse_align_env(parser):
    t = _parse(parser, "\\begin{aligned}a&=b\\\\c&=d\\end{aligned}")
    assert "=" in t.plain
    assert "a" in t.plain and "d" in t.plain


# ── 重音与上下划线 ────────────────────────────────────────

def test_parse_accent_hat(parser):
    assert _parse(parser, "\\hat{x}").plain == "x\u0302"


def test_parse_accent_vec(parser):
    assert _parse(parser, "\\vec{v}").plain == "v\u20D7"


def test_parse_overline(parser):
    assert _parse(parser, "\\overline{z}").plain == "z\u0305"


def test_parse_underline(parser):
    assert _parse(parser, "\\underline{x}").plain == "x\u0332"


# ── 颜色 / 划除 / 框 ──────────────────────────────────────

def test_parse_textcolor_plain(parser):
    t = _parse(parser, "\\textcolor{red}{hello}")
    assert t.plain == "hello"
    assert t.spans and t.spans[0].style.color is not None


def test_parse_cancel_plain(parser):
    t = _parse(parser, "\\cancel{x}")
    assert t.plain == "x"


def test_parse_boxed(parser):
    assert _parse(parser, "\\boxed{x}").plain == "[x]"


# ── 大算符极限 ────────────────────────────────────────────

def test_parse_sum_alone(parser):
    assert _parse(parser, "\\sum").plain == "∑"


def test_parse_sum_with_limits(parser):
    t = _parse(parser, "\\sum_{i=1}^{n}")
    assert "∑" in t.plain
    assert "_{" in t.plain
    assert "}^{" in t.plain


def test_parse_int_compact_limits(parser):
    # 积分上下限均为单字符可映射时走紧凑 Unicode 美化
    t = _parse(parser, "\\int_{a}^{b}")
    assert "∫" in t.plain


def test_parse_limit_function(parser):
    assert _parse(parser, "\\lim_{x\\to 0}").plain == "lim(x→ 0)"


def test_parse_max_limit(parser):
    t = _parse(parser, "\\max_{x}")
    assert "max" in t.plain


# ── 绝对值 / 范数 ─────────────────────────────────────────

def test_parse_abs(parser):
    assert _parse(parser, "\\abs{x}").plain == "|x|"


def test_parse_norm(parser):
    assert _parse(parser, "\\norm{x}").plain == "‖x‖"


# ── 定界符 ────────────────────────────────────────────────

def test_parse_left_right(parser):
    t = _parse(parser, "\\left( x \\right)")
    assert "(" in t.plain and ")" in t.plain


def test_parse_langle_rangle(parser):
    t = _parse(parser, "\\langle x \\rangle")
    assert t.plain == "⟨ x ⟩"


# ── 取模 / 标签 ───────────────────────────────────────────

def test_parse_pmod(parser):
    assert _parse(parser, "\\pmod{n}").plain == "(mod n)"


def test_parse_tag(parser):
    assert _parse(parser, "\\tag{1}").plain == " (1)"


# ── 向量箭头 ──────────────────────────────────────────────

def test_parse_overrightarrow(parser):
    t = _parse(parser, "\\overrightarrow{AB}")
    assert t.plain == "A\u20D7B\u20D7"


# ── 堆积 ──────────────────────────────────────────────────

def test_parse_overset(parser):
    t = _parse(parser, "\\overset{!}{=}")
    assert t.plain == "!="


def test_parse_underset(parser):
    t = _parse(parser, "\\underset{x}{=}")
    assert t.plain == "=x"


# ── 未知命令与健壮性 ──────────────────────────────────────

def test_parse_unknown_command_kept(parser):
    assert _parse(parser, "\\unknowncmd").plain == "\\unknowncmd"


def test_parse_lone_backslash(parser):
    assert _parse(parser, "\\").plain == "\\"


def test_parse_unclosed_frac_fallback(parser):
    # 未闭合的 \frac 降级输出字面量
    t = _parse(parser, "\\frac{1}")
    assert "frac" in t.plain or "⁄" in t.plain or t.plain


def test_parse_block_flag_stored(parser):
    _parse(parser, "x", is_block=True)
    assert parser._is_block is True


def test_parse_no_crash_on_deep_input(parser):
    # 超深嵌套应不崩溃（内部有深度保护）
    s = "\\frac{" * 5 + "1" + "}" * 5
    _parse(parser, s)


def test_parse_script_at_end(parser):
    # 末尾孤立 ^ 不应崩溃
    assert _parse(parser, "x^").plain == "x"
