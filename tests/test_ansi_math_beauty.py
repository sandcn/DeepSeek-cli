"""ANSI（TUI 流式）公式排版美化测试。

覆盖本轮的排版质量改进（``src/renderer/ansi/_math_latex.py`` /
``_math_box.py`` / ``_math_env.py`` / ``math_symbols/scripts.py``）：

  - **上下标 Unicode 化补全**：嵌套脚本（``e^{-x^2}`` → ``e⁻ˣ²``）与别名
    （转置 ``x^\\top`` → ``xᵀ``）——修复前一律退化为字面 ``^{-x²}``；
  - **块级脚本二维堆叠**：无对应 Unicode 字形的脚本（``\\mathbb{R}^{n\\times m}``
    / ``a_{i,j}`` / ``\\nabla_\\theta`` / ``x^{\\frac{a}{b}}``）排在主体右上
    / 右下，而不是把 LaTeX 源码字面上屏；
  - **空脚本不占位**：``\\sum_`` / ``x^{}`` 不输出 ``_{}`` / ``^{}``；
  - **根式**：多行被开方体沿左侧加根号竖线（真正「包住」分数等），度数
    （``\\sqrt[3]{}``）的上划线与内容左缘对齐；
  - **环境单元格按基线对齐**：``aligned`` / 矩阵里含分数的单元格与单行兄弟
    单元格的数学基线共线（``=`` 落在分数线高度）。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi.math import (
    render_math_block, render_math_inline, clear_math_cache,
)


@pytest.fixture(autouse=True)
def _isolate_math_state():
    """每个用例前后清空公式缓存与跨公式宏状态（``\\gdef`` 会持久）。"""
    clear_math_cache()
    yield
    clear_math_cache()


def _inner_lines(src: str) -> list[str]:
    """块级公式的正文行（去掉圆角框线与两侧留白，保留行首缩进）。"""
    out: list[str] = []
    for line in render_math_block(src)[1:-1]:
        text = line.plain
        if text.startswith("\u2502 "):
            text = text[2:]
        if text.endswith(" \u2502"):
            text = text[:-2]
        out.append(text.rstrip())
    return out


def _plain(src: str) -> str:
    return "\n".join(ln.plain for ln in render_math_block(src))


# ── 上下标 Unicode 化补全 ────────────────────────────────


def test_nested_script_unicode_identity():
    """幂里已是 Unicode 上标的嵌套脚本可整体上标化（``e^{-x²}`` → ``e⁻ˣ²``）。"""
    assert render_math_inline(r"e^{-x^2}").plain == "e\u207b\u02e3\u00b2"
    assert render_math_inline(r"x^{2^k}").plain == "x\u00b2\u1d4f"
    assert _inner_lines(r"e^{-x^2}") == ["e\u207b\u02e3\u00b2"]


def test_script_containing_subscript_glyph_needs_stacking():
    """幂里嵌的是**下标**字形（``x^{a_i}``）→ 单行无法表达 → 二维堆叠。"""
    assert _inner_lines(r"x^{a_i}") == [" a\u1d62", "x"]


def test_transpose_and_degree_aliases():
    """无上标字形的惯例别名：转置 ``\\top`` → ``ᵀ``、``\\circ`` → ``°``。"""
    assert render_math_inline(r"x^\top").plain == "x\u1d40"
    assert render_math_inline(r"x^\circ").plain == "x\u00b0"
    assert render_math_inline(r"A^{-1}").plain == "A\u207b\u00b9"


# ── 块级脚本二维堆叠 ─────────────────────────────────────


def test_non_unicode_subscript_is_stacked():
    """``a_{i,j}``：下标（含无字形字符）排在主体右下，而不是字面 ``_{i,j}``。"""
    assert _inner_lines(r"a_{i,j}") == ["a", " i,j"]


def test_non_unicode_superscript_is_stacked():
    assert _inner_lines(r"\mathbb{R}^{n \times m}") == [" n \u00d7 m", "\u211d"]


def test_greek_script_is_stacked():
    assert _inner_lines(r"\nabla_\theta") == ["\u2207", " \u03b8"]


def test_sup_and_sub_stacked_around_base():
    """上下标都不可 Unicode 化时：上标占上方行、下标占下方行，主体居中。"""
    assert _inner_lines(r"T_{\alpha\beta}^{\mu\nu}") == [
        " \u03bc\u03bd", "T", " \u03b1\u03b2",
    ]


def test_multiline_script_keeps_its_layout():
    """多行脚本（分数）整体堆叠在主体右上（不展平、不破坏二维结构）。"""
    assert _inner_lines(r"x^{\frac{a}{b}}") == ["  a", " \u2500\u2500\u2500", "  b", "x"]


def test_stacked_script_aligns_with_following_content():
    """同一行的两个块按**基线**水平拼接（后续内容落在主体行）。"""
    assert _inner_lines(r"a_{i,j} + b") == ["a    + b", " i,j"]


def test_compact_path_keeps_unicode_fallback():
    """紧凑路径（``render_math_inline``）无二维空间，保留 ``^{...}`` 回退。"""
    assert render_math_inline(r"\mathbb{R}^{n \times m}").plain == "\u211d^{n \u00d7 m}"


def test_empty_scripts_are_not_emitted():
    """空脚本不占位（流式预览里的 ``\\sum_`` 不再出现 ``_{}``）。"""
    assert "_{}" not in _plain(r"\sum_")
    assert "^{}" not in _plain(r"\sum^")
    assert _inner_lines(r"x^{}") == ["x"]


# ── 根式 ────────────────────────────────────────────────


def test_sqrt_encloses_multiline_content():
    """多行被开方体（分数）：根号竖线沿左侧延伸，首行上划线覆盖整体。"""
    assert _inner_lines(r"\sqrt{\frac{a}{b}}") == [
        "\u203e\u203e\u203e\u203e",
        "\u221a a",
        "\u2502\u2500\u2500\u2500",
        "\u2502 b",
    ]


def test_sqrt_overline_spans_hook_and_content():
    """单行被开方体：上划线自根号列起（与 ``√`` 相接），长度 = 内容 + 1。"""
    assert _inner_lines(r"\sqrt{x^2+y^2}") == ["\u203e" * 6, "\u221ax\u00b2+y\u00b2"]


def test_sqrt_degree_overline_aligned_with_content():
    """度数占一列：上划线与被开方体左缘对齐（修复前整体左移一列）。"""
    assert _inner_lines(r"\sqrt[3]{x}") == [" \u203e\u203e", "3\u221ax"]


def test_sqrt_nested_radicals_stack():
    """嵌套根式：内层根号完整保留（上划线 + 竖线），内容不丢。"""
    rows = _inner_lines(r"\sqrt{1+\sqrt{x}}")
    assert rows[0].count("\u203e") >= 2
    assert "\u221a" in rows[1] and "\u221a" in rows[2]
    assert sum(1 for r in rows if "\u2502" in r) == 1


# ── 环境单元格基线对齐 ───────────────────────────────────


def test_aligned_cells_share_math_baseline():
    """``aligned`` 中分数单元格与单行单元格的基线共线（``=`` 落在分数线行）。"""
    rows = _inner_lines(r"\begin{aligned} x &= 1 \\ \frac{a}{b} &= 2 \end{aligned}")
    bar_row = next(r for r in rows if "\u2500\u2500\u2500" in r)
    assert "= 2" in bar_row                       # 等号与分数线同行
    assert "=" not in rows[rows.index(bar_row) - 1]  # 分子行不含等号


def test_matrix_cells_share_math_baseline():
    """矩阵里含分数的单元格与同行的单行单元格按基线共线（``c`` 落在分数线行）。"""
    rows = _inner_lines(r"\begin{pmatrix} \frac{a}{b} & c \\ d & e \end{pmatrix}")
    bar_row = next(r for r in rows if "\u2500" in r)
    assert "c" in bar_row
    assert "a" in rows[rows.index(bar_row) - 1]      # 分子在分数线上一行
    assert "b" in rows[rows.index(bar_row) + 1]      # 分母在分数线下一行
    assert any("d" in r and "e" in r for r in rows)  # 单行行不受影响


# ── 与流式路径的一致性 ───────────────────────────────────


def test_tui_stream_render_stacked_scripts():
    """TUI 内容块路径：分段流式与一次性渲染结果一致，且堆叠脚本可见。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    md = "$$\n\\mathbb{R}^{n \\times m}, \\quad a_{i,j}\n$$\n"

    def _lines(chunks: int) -> list[str]:
        m = AppModel()
        m.width = 80
        if chunks <= 1:
            _do_content(m, ContentCmd(text=md))
        else:
            step = max(1, len(md) // chunks)
            for i in range(0, len(md), step):
                _do_content(m, ContentCmd(text=md[i:i + step]))
        _do_content(m, ContentCmd(text="\n"))
        return [ln.plain for ln in m.blocks[m.content_block_index].lines]

    whole = _lines(1)
    assert whole == _lines(12)
    joined = "\n".join(whole)
    assert "n \u00d7 m" in joined and "\u211d" in joined
    assert "^{" not in joined and "_{" not in joined


def test_tui_math_preview_hides_empty_script():
    """流式预览：``\\sum_`` 未写全时不出现 ``_{}``（提交态与预览态一致）。"""
    from src.renderer.ansi import AnsiStreamRenderer

    renderer = AnsiStreamRenderer(width=76)
    for i in range(0, len("$$\n\\sum_"), 3):
        renderer.write("$$\n\\sum_"[i:i + 3])
    preview = "\n".join(ln.plain for ln in renderer.take_preview_lines())
    assert "\u2211" in preview
    assert "_{}" not in preview and "^{}" not in preview
