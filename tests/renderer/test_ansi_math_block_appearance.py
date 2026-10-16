"""块级公式外观与公式配色（「让公式更好看」）测试。

用户需求（2026-10-16）：「改进流式 markdown 渲染，让公式更好看」。
改动范围：TUI 流式渲染路径（``src/renderer/ansi/``）。

覆盖：

  1. **块级外观**：圆角框内容**居中**、标签精简为「公式」、框线**等宽**
     （修复前短公式 ``$x$`` 顶边比内容行宽、底边比内容行窄——圆形框残缺）；
  2. **公式编号** ``\\tag{1}``：块级右对齐到框内末行（修复前作为普通文本拼在
     公式尾部，既破坏居中也不符 LaTeX 习惯）、``\\tag*{}`` 去括号、仅编号时
     不输出空内容行；行内公式编号附在末行（修复前随布局块传递、行内路径不
     消费，编号静默丢失）；
  3. **配色素雅统一**：数字与符号同色（近白）、算符青不加粗、函数蓝、分数线
     与根式线同品红、``\\boxed`` 框与上下花括号并入青色（黄 / 橙旁支色去除）；
  4. **显示宽度**：CJK 标注 / 根式度数按显示宽度居中与占位（修复前按字符数
     计算，中文标注偏移一列）；
  5. **流式一致性**：分段流式与一次性渲染结果一致（TUI 内容块 +
     未闭合块预览两条路径）。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi._math_latex import render_math_box
from src.renderer.ansi.helpers import visual_width
from src.renderer.ansi.math import (
    render_math_block, render_math_inline, render_math_inline_block,
    render_math_omitted, clear_math_cache,
)
from src.renderer.ansi import _math_style as P


@pytest.fixture(autouse=True)
def _isolate_math_state():
    """每个用例前后清空公式缓存与跨公式宏状态。"""
    clear_math_cache()
    yield
    clear_math_cache()


def _rows(src: str, **kw) -> list[str]:
    """块级公式各行纯文本。"""
    return [ln.plain for ln in render_math_block(src, **kw)]


def _body_rows(src: str) -> list[str]:
    """布局块正文行（不含块级框与居中留白）。"""
    return [ln.plain for ln in render_math_box(src, inline=False).lines]


# ══════════════════════════════════════════════════════════
# 1. 块级外观：标签 / 框线等宽 / 内容居中
# ══════════════════════════════════════════════════════════


def test_default_label_is_concise():
    """默认标签「公式」（修复前「数学公式」——框头冗长且抢眼）。"""
    head = _rows("x")[0]
    assert head.startswith("\u256d\u2500 \u516c\u5f0f")


def test_custom_label_is_honoured():
    assert _rows("x", label="矩阵")[0].startswith("\u256d\u2500 \u77e9\u9635")
    assert "矩阵" in _rows("x", label="矩阵")[0]


def test_short_formula_frame_is_closed():
    """短公式（``x``）的圆形框闭合：顶 / 内容 / 底三行等宽。"""
    assert _rows("x") == [
        "\u256d\u2500 \u516c\u5f0f \u2500\u256e",
        "\u2502    x   \u2502",
        "\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f",
    ]


@pytest.mark.parametrize("src", [
    "x",
    "E = mc^2",
    r"\frac{a}{b}",
    r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
    r"\tag{1} E = mc^2",
    r"\sum_{i=1}^{n} \frac{1}{i}",
])
def test_frame_rows_share_width(src):
    """框线等宽不变量：同一公式的所有行显示宽度一致。"""
    widths = {ln.width for ln in render_math_block(src)}
    assert len(widths) == 1, _rows(src)


def test_single_line_content_is_centered():
    """单行内容居中（左右留白差 ≤ 1 列）。"""
    assert _rows("x")[1] == "\u2502    x   \u2502"
    assert _rows("E = mc^2")[1] == "\u2502 E = mc\u00b2 \u2502"


def test_multiline_content_each_row_centered():
    """多行内容（分数）逐行居中，分数线仍居中于分子 / 分母。"""
    assert _rows(r"\frac{a}{b}")[1:4] == [
        "\u2502    a   \u2502",
        "\u2502   \u2500\u2500\u2500  \u2502",
        "\u2502    b   \u2502",
    ]


def test_centering_keeps_body_layout():
    """居中只加左右留白：正文结构（堆叠 / 根式）逐行原样保留。"""
    body = _body_rows(r"\sqrt{\frac{a}{b}}")
    framed = [row[2:-2] for row in _rows(r"\sqrt{\frac{a}{b}}")[1:-1]]
    assert len(body) == len(framed)
    inner_w = max(visual_width(row) for row in framed)
    for raw, row in zip(body, framed):
        left = (inner_w - visual_width(raw)) // 2
        assert row == " " * left + raw + " " * (inner_w - visual_width(raw) - left)


def test_centering_with_wide_chars():
    """CJK 内容按显示宽度居中（``和`` 宽 2 列）。"""
    assert _rows(r"\text{和}")[1] == "\u2502    \u548c  \u2502"


def test_brace_label_centered_width_aware():
    """``\\overbrace`` 中文标注按显示宽度居中（修复前按字符数偏移一列）。"""
    rows = _body_rows(r"\overbrace{a+b+c}^{和}")
    label_row, brace_row = rows[0], rows[1]
    assert brace_row.startswith("\u23de" * 5)
    assert label_row.index("\u548c") == 1        # (5 - 2) // 2 = 1
    assert visual_width(label_row) <= visual_width(brace_row)


def test_sqrt_degree_wide_char_alignment():
    """根式度数（CJK）占 2 列：上划线与被开方体左缘对齐。"""
    assert _body_rows(r"\sqrt[甲]{x}") == ["  \u203e\u203e", "\u7532\u221ax"]


def test_omitted_notice_row_is_left_aligned():
    """预览截断提示行左对齐（提示文本不参与居中）。"""
    rows = _rows(r"\frac{a}{b}", dropped=3)
    assert rows[1] == "\u2502 \u2026 \u524d 3 \u884c\u7701\u7565 \u2502"
    assert len({visual_width(r) for r in rows}) == 1


def test_overflow_placeholder_frame_is_closed():
    """超长公式预览占位框：三行等宽、含提示语。"""
    rows = [ln.plain for ln in render_math_omitted(2000)]
    assert len(rows) == 3
    assert rows[0].startswith("\u256d\u2500 \u516c\u5f0f")
    assert "\u516c\u5f0f\u8fc7\u957f" in rows[1]
    assert len({visual_width(r) for r in rows}) == 1


def test_frame_inside_blockquote_keeps_width():
    """引用块内的公式框：加 ``│ `` 前缀后仍逐行等宽。"""
    from src.renderer.ansi import AnsiStreamRenderer

    r = AnsiStreamRenderer(width=60)
    r.write("> $$\n> x\n> $$\n")
    r.close()
    rows = [ln.plain for ln in r.take_lines() if "\u2502" in ln.plain]
    assert rows, "引用块内公式应渲染"
    assert len({visual_width(x) for x in rows}) == 1


# ══════════════════════════════════════════════════════════
# 2. 公式编号 \tag
# ══════════════════════════════════════════════════════════


def test_tag_is_right_aligned_in_frame():
    """编号右对齐到框内末行（不再挤在公式尾部破坏居中）。"""
    rows = _rows(r"\tag{1} E = mc^2")
    assert rows[1] == "\u2502  E = mc\u00b2 \u2502"
    assert rows[2] == "\u2502      (1) \u2502"


def test_tag_keeps_formula_centering():
    """编号不占公式宽度：公式文本在框内的起始列与不带编号时一致。"""
    plain_row = _rows("E = mc^2")[1]
    assert _rows(r"E = mc^2 \tag{1}")[1].index("E") == plain_row.index("E")
    assert _rows(r"\tag{1}E = mc^2")[1] == plain_row


def test_tag_star_omits_parentheses():
    rows = _rows(r"\tag*{A}")
    assert rows[1].rstrip().endswith("A \u2502")
    assert "(" not in rows[1]


def test_tag_only_has_no_blank_body_row():
    """只有编号没有公式时不输出空内容行。"""
    rows = _rows(r"\tag{1}")
    assert len(rows) == 3
    assert rows[1].rstrip().endswith("(1) \u2502")


def test_tag_placed_mid_formula():
    rows = _rows(r"E \tag{2} = mc^2")
    assert any("(2)" in r for r in rows)
    assert len({ln.width for ln in render_math_block(r"E \tag{2} = mc^2")}) == 1


def test_inline_block_tag_appended():
    """行内公式（二维块）的编号附在末行（修复前编号丢失）。"""
    lines, _base = render_math_inline_block(r"\frac{a}{b} \tag{1}")
    assert lines[-1].plain.endswith("(1)")


def test_compact_inline_tag_is_text():
    """紧凑行内路径的编号以文本形式保留。"""
    assert render_math_inline(r"E \tag{1}").plain.endswith("(1)")


def test_tag_reaches_tui_stream_path():
    """TUI 内容块路径同样右对齐编号（非仅单测直调渲染函数）。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="$$\n\\tag{1} E = mc^2\n$$\n"))
    _do_content(m, ContentCmd(text="\n"))
    rows = [ln.plain for ln in m.blocks[m.content_block_index].lines]
    assert rows == _rows(r"\tag{1} E = mc^2")


# ══════════════════════════════════════════════════════════
# 3. 配色：素雅统一
# ══════════════════════════════════════════════════════════


def test_numbers_share_symbol_color():
    """数字与符号同色（近白）——修复前数字黄、符号近白，颜色混杂。"""
    assert P._M_NUM.fg == P._M_SYM.fg


def test_operator_is_cyan_without_bold():
    """算符青色但不加粗（修复前 bold 叠加亮青，视觉噪点重）。"""
    assert P._M_OP.fg == 51
    assert not P._M_OP.bold


def test_bigop_still_bold_for_hierarchy():
    """大算符仍加粗（与普通算符形成层级，强调求和 / 积分主体）。"""
    assert P._M_BIGOP.fg == P._M_OP.fg
    assert P._M_BIGOP.bold


def test_function_is_blue():
    assert P._M_FN.fg == 75


def test_structure_lines_share_magenta():
    """分数线与根式线同色（结构线统一，减少颜色种类）。"""
    assert P._M_FRAC.fg == P._M_SQRT.fg == 213


def test_box_and_brace_join_cyan_family():
    """``\\boxed`` 框与上下花括号并入青色族（修复前黄 / 橙旁支色）。"""
    assert P._M_BOX.fg == 51
    assert P._M_BRACE.fg == 51


def test_no_legacy_warm_colors():
    """旧配色（数字黄 221 / 框黄 226 / 花括号橙 214）不再出现在基础表。"""
    legacy = {221, 226, 214}
    for name in ("_M_SYM", "_M_NUM", "_M_OP", "_M_SUP", "_M_SUB", "_M_FRAC",
                 "_M_SQRT", "_M_BOX", "_M_BRACE", "_M_BIGOP"):
        assert getattr(P, name).fg not in legacy, name


def test_rendered_fraction_bar_uses_structure_color():
    bar = next(ln for ln in render_math_box(r"\frac{a}{b}").lines
               if "\u2500" in ln.plain)
    assert [r.style.fg for r in bar.runs if "\u2500" in r.text] == [P._M_FRAC.fg]


def test_rendered_boxed_frame_uses_cyan():
    top = render_math_box(r"\boxed{x}").lines[0]
    assert [r.style.fg for r in top.runs] == [P._M_BOX.fg]


def test_rendered_math_text_merges_numbers_and_letters():
    """同一原子内的数字与字母合并为同一样式 run（素雅：不再数字一色）。"""
    line = render_math_box("x1").lines[0]
    assert len(line.runs) == 1
    assert line.runs[0].style.fg == P._M_SYM.fg


def test_secondary_labels_are_readable():
    """次要标注（极限下标 / ``\\overset`` 标注 / 公式编号）不再 dim 压暗。

    ``dim``（SGR 2）叠加暗灰在深色终端上几乎不可见——极限条件、公式编号是
    阅读公式的必要信息，必须保持可读。
    """
    assert not P._M_NOTICE.dim
    assert not P._M_TAG.dim
    assert P._M_NOTICE.fg == P._M_TAG.fg


def test_block_frame_border_style_stable():
    """框线用柔和灰（不与正文争色）。"""
    rows = render_math_block("x")
    assert rows[0].runs[0].style.fg == 45          # 标签
    assert rows[0].runs[-1].style.fg == 240        # 框线


# ══════════════════════════════════════════════════════════
# 4. 流式一致性与缓存
# ══════════════════════════════════════════════════════════


def _content_lines(text: str, chunks: int = 1, width: int = 80) -> list[str]:
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = width
    if chunks <= 1:
        _do_content(m, ContentCmd(text=text))
    else:
        step = max(1, len(text) // chunks)
        for i in range(0, len(text), step):
            _do_content(m, ContentCmd(text=text[i:i + step]))
    _do_content(m, ContentCmd(text="\n"))
    return [ln.plain for ln in m.blocks[m.content_block_index].lines]


def test_tui_stream_matches_oneshot_and_direct_render():
    md = "$$\n\\tag{1} \\frac{a}{b} + \\sum_{i=1}^{n} x_i\n$$\n"
    whole = _content_lines(md)
    assert whole == _content_lines(md, chunks=13)
    assert whole == _rows(r"\tag{1} \frac{a}{b} + \sum_{i=1}^{n} x_i")


def test_tui_preview_is_centered_and_closed():
    """未闭合公式块的流式预览：框闭合 + 内容居中。"""
    from src.renderer.ansi import AnsiStreamRenderer

    r = AnsiStreamRenderer(width=60)
    r.write("$$\n\\frac{a}{b}\n")
    rows = [ln.plain for ln in r.take_preview_lines()]
    assert rows == _rows(r"\frac{a}{b}")


def test_label_participates_in_cache_key():
    """不同标签不复用同一缓存条目。"""
    a = render_math_block("x")
    b = render_math_block("x", label="矩阵")
    assert a is not b
    assert "矩阵" in b[0].plain
    assert render_math_block("x") is a


def test_inline_block_cache_keeps_tag():
    """行内二维块结果按源码缓存，编号在缓存命中时同样存在。"""
    first, _ = render_math_inline_block(r"\frac{a}{b} \tag{9}")
    second, _ = render_math_inline_block(r"\frac{a}{b} \tag{9}")
    assert first is second
    assert first[-1].plain.endswith("(9)")
