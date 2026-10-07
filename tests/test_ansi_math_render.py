"""ANSI 数学公式渲染测试（TUI 流式路径）。

覆盖 ``src/renderer/ansi/_math_latex.py`` / ``ansi/math.py``：
  - 行内紧凑渲染（Unicode 上下标、分数斜线、定界符去除）
  - 块级二维排版（分数堆叠、根式上划线、矩阵、cases）
  - 符号表命令（希腊字母 / 关系 / 运算符 / 大算符 / 箭头 / 函数 / 间距）
  - 结构命令（\\text / \\boxed / \\color / \\overline / \\hat / \\binom / \\lim）
  - 未知命令原样保留、异常输入安全降级
  - 结果缓存（同源码复用同一结果对象）
"""

from __future__ import annotations

from src.renderer.ansi.math import (
    render_math_block, render_math_inline, clear_math_cache,
)


def _text(lines) -> str:
    return "\n".join(ln.plain for ln in lines)


# ── 行内 ──────────────────────────────────────────────────


def test_inline_basic_and_superscript():
    assert render_math_inline("E = mc^2").plain == "E = mc\u00b2"
    assert render_math_inline("x_i").plain == "x\u1d62"
    assert render_math_inline("x^{n+1}").plain == "x\u207f\u207a\u00b9"


def test_inline_commands():
    assert render_math_inline(r"\alpha + \beta").plain == "\u03b1 + \u03b2"
    assert render_math_inline(r"a \le b \ge c \ne d").plain == "a \u2264 b \u2265 c \u2260 d"
    assert render_math_inline(r"a \times b \cdot c").plain == "a \u00d7 b \u00b7 c"
    assert render_math_inline(r"\infty \nabla \partial").plain == "\u221e \u2207 \u2202"
    assert render_math_inline(r"\sin x \to 0").plain == "sin x \u2192 0"


def test_inline_frac_compact():
    assert render_math_inline(r"\frac{a}{b}").plain == "a\u2044b"
    assert render_math_inline(r"\frac{a+b}{c}").plain == "(a+b)\u2044c"


def test_inline_sqrt_and_binom():
    assert render_math_inline(r"\sqrt{x}").plain == "\u221ax"
    assert render_math_inline(r"\sqrt[3]{x}").plain == "3\u221ax"
    assert render_math_inline(r"\binom{n}{k}").plain == "(n\u00a6k)"


def test_inline_bigop_and_limit():
    assert render_math_inline(r"\sum_{i=1}^{n} i").plain == "\u2211_{i=1}^{n} i"
    assert render_math_inline(r"\lim_{x \to 0} f(x)").plain == "lim(x \u2192 0) f(x)"
    assert render_math_inline(r"\int_{0}^{1} f").plain == "\u222b_{0}^{1} f"


def test_inline_text_and_boxed():
    assert render_math_inline(r"\text{速度} v").plain == "速度 v"
    assert render_math_inline(r"\boxed{x}").plain == "[x]"


def test_inline_unknown_command_preserved():
    assert r"\unknowncmd" in render_math_inline(r"\unknowncmd").plain


def test_inline_empty_and_exception_safe():
    assert render_math_inline("").plain == ""
    # 未闭合花括号不抛异常
    render_math_inline(r"\frac{a}{")


# ── 块级 ──────────────────────────────────────────────────


def test_block_basic_frame():
    lines = render_math_block("E = mc^2")
    assert lines[0].plain.startswith("\u256d\u2500")
    assert "E = mc\u00b2" in lines[1].plain
    assert lines[-1].plain.startswith("\u2570")


def test_block_frac_stacked_rows():
    text = _text(render_math_block(r"\frac{a}{b}"))
    rows = text.split("\n")
    # 分数为二维排版：分子行、横线行、分母行
    assert any("a" in r for r in rows)
    assert any("\u2500" in r for r in rows)
    assert any("b" in r for r in rows)


def test_block_sqrt_has_overline():
    text = _text(render_math_block(r"\sqrt{x+1}"))
    assert "\u221a" in text
    assert "\u203e" in text  # 上划线


def test_block_matrix_pmatrix():
    text = _text(render_math_block(r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}"))
    assert "a" in text and "b" in text and "c" in text and "d" in text
    assert "\u239b" in text  # ⎛ 高左括号


def test_block_cases():
    text = _text(render_math_block(r"\begin{cases} x & x > 0 \\ 0 & x \le 0 \end{cases}"))
    assert "\u2264" in text
    assert "\u23a7" in text  # ⎧


def test_block_overline_on_content():
    rows = _text(render_math_block(r"y = \overline{AB}")).split("\n")
    assert any("\u203e" in r for r in rows)
    assert any("AB" in r for r in rows)


def test_block_style_commands():
    assert "\u2261" in _text(render_math_block(r"a \equiv b"))
    assert "\u2192" in _text(render_math_block(r"f: X \to Y"))


# ── 缓存 ──────────────────────────────────────────────────


def test_render_cache_reuses_result():
    clear_math_cache()
    a = render_math_block("a+b")
    b = render_math_block("a+b")
    assert a is b
    clear_math_cache()
    c = render_math_block("a+b")
    assert c is not a
