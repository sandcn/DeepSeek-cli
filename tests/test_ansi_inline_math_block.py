"""行内公式二维多行渲染测试（TUI 流式路径）。

覆盖本轮新增能力：行内 ``$...$`` / ``\\(...\\)`` 公式在终端以**二维多行**
排版呈现，并与同一行组内的周围文本按**基线**水平拼接：

  - ``inline.inline_lines`` 识别 ``Run.block``（``InlineBlock``）并拼接；
  - 段落 / 引用 / 列表项 / 定义项 / 容器正文的多行与基线对齐；
  - ``inline_lines_with_baseline`` 供列表项把项目符号放在公式基线行；
  - 单行公式（``x^2``）不引入额外行；
  - 不认识 block 的消费者（表格、单行渲染 API）使用展平降级文本，内容不丢；
  - 流式预览的行级缓存支持「一行源文本产出多行」的增量复用。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import AnsiLine
from src.renderer.ansi.inline import (
    inline_lines, inline_lines_with_baseline, render_inline,
)
from src.renderer.ansi.blocks import render_paragraph_line
from src.renderer.ansi._preview_cache import LinePreviewCache


def _render(md: str, width: int = 80) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ── inline_lines 多行块拼接 ──────────────────────────────


def test_fraction_expands_to_three_lines():
    lines = inline_lines(r"设 $x=\frac{a}{b}$ 为解")
    assert len(lines) == 3
    plains = [ln.plain for ln in lines]
    # 基线行：公式主体与前后文本同行
    assert "设" in plains[1] and "为解" in plains[1]
    assert "─" in plains[1]
    # 分子 / 分母各占一行
    assert plains[0].strip() == "a"
    assert plains[2].strip() == "b"


def test_run_carries_block_for_multiline_math():
    runs = render_inline(r"$\frac{a}{b}$")
    blocks = [r for r in runs if getattr(r, "block", None) is not None]
    assert len(blocks) == 1
    block = blocks[0].block
    assert len(block.lines) == 3
    assert block.baseline == 1
    # 展平降级文本仍含全部内容（不认识 block 的消费者可显示）
    assert "a" in blocks[0].text and "b" in blocks[0].text


def test_simple_inline_math_stays_single_line():
    assert len(inline_lines(r"值为 $x^2$ 结束")) == 1
    assert len(inline_lines(r"值为 $x+y$ 结束")) == 1


def test_only_one_line_of_multi_line_math_gets_surrounding_text():
    lines = inline_lines(r"A $\sqrt{\frac{x}{y}}$ B")
    plains = [ln.plain for ln in lines]
    assert len(plains) >= 3
    with_text = [p for p in plains if "A" in p or "B" in p]
    assert len(with_text) == 1
    assert "A" in with_text[0] and "B" in with_text[0]


def test_baseline_rows_from_helper():
    rows, baseline = inline_lines_with_baseline(r"前 $\frac{a}{b}$ 后")
    assert len(rows) == 3
    assert baseline == 1
    assert "前" in rows[baseline].plain and "后" in rows[baseline].plain


def test_hard_break_still_splits_lines():
    lines = inline_lines("a<br>b")
    assert [ln.plain for ln in lines] == ["a", "b"]


# ── 各块级场景 ───────────────────────────────────────────


def test_paragraph_renders_multiline_inline_math():
    out = _render(r"公式 $\frac{a+b}{c}$ 结束" + "\n")
    plains = [p for p in out if p.strip()]
    assert len(plains) == 3
    assert any("公式" in p and "结束" in p and "─" in p for p in plains)


def test_list_item_bullet_on_baseline_row():
    out = _render(r"- 结果 $\frac{a}{b}$" + "\n")
    plains = [p for p in out if p.strip()]
    bullet_rows = [p for p in plains if p.lstrip().startswith("•")]
    assert len(bullet_rows) == 1
    assert "─" in bullet_rows[0]          # 项目符号与分数线同行
    assert "结果" in bullet_rows[0]


def test_definition_item_on_baseline_row():
    out = _render("术语\n: 定义 $\\frac{a}{b}$\n")
    plains = [p for p in out if p.strip()]
    assert any(p.lstrip().startswith("术语") for p in plains)
    assert any("─" in p and "定义" in p for p in plains)


def test_blockquote_prefix_on_every_math_row():
    out = _render("> $\\frac{a}{b}$\n")
    plains = [p for p in out if p.strip()]
    assert len(plains) == 3
    assert all(p.startswith("│ ") for p in plains)


def test_admonition_body_renders_multiline_math():
    out = _render("> [!NOTE]\n> 公式 $\\frac{a}{b}$ 结束\n")
    plains = [p for p in out if p.strip()]
    assert any("NOTE" in p for p in plains)
    assert any("─" in p for p in plains)
    assert any("公式" in p and "结束" in p for p in plains)


def test_table_cell_uses_flattened_fallback():
    out = _render("| 公式 |\n| --- |\n| $\\frac{a}{b}$ |\n")
    joined = "\n".join(out)
    assert "a" in joined and "b" in joined
    assert "│" in joined


def test_table_cell_fraction_expands_to_multiline_row():
    out = _render("| 公式 | 说明 |\n| --- | --- |\n| $\\frac{a}{b}$ | 分式 |\n")
    # 上边框 + 表头 + 中边框 + 3 行（分子/线/分母）+ 下边框
    assert len(out) == 7
    assert out[0].startswith("┌") and out[-1].startswith("└")
    # 说明文本与公式**基线行**（分数线）同行
    assert any("─" in p and "分式" in p for p in out)
    assert any("a" in p and "│" in p for p in out)


def test_table_cell_big_operator_multiline():
    out = _render("| 公式 | 说明 |\n| --- | --- |\n| $\\sum_{i=1}^{n}$ | 求和 |\n")
    joined = "\n".join(out)
    assert "∑" in joined and "i=1" in joined
    assert any("求和" in p and "∑" in p for p in out)


def test_table_column_width_uses_max_line_width():
    out = _render("| x |\n| --- |\n| $\\frac{abc}{de}$ |\n")
    # 分数线宽 = max(3,2)+2 = 5 → 边框段 5+2 = 7 个 ─
    assert "\u2500" * 7 in out[0]


def test_table_cell_br_still_multiline():
    out = _render("| h |\n| --- |\n| a<br>b |\n")
    plains = [p for p in out]
    # 上下边框 + 表头(1) + 中边框 + 数据行(2) = 6 行
    assert len(plains) == 6
    assert any("a" in p for p in plains)
    assert any("b" in p for p in plains)


def test_table_preview_cache_multiline_cells():
    from src.renderer.ansi.table import TablePreviewCache

    cache = TablePreviewCache()
    rows = [["公式"], ["$\\frac{a}{b}$"]]
    out = cache.render(("公式",), rows, ["left"], 80)
    plains = [ln.plain for ln in out]
    assert len(plains) >= 6
    assert any("a" in p for p in plains) and any("b" in p for p in plains)
    # 追加一行后仍复用历史渲染行（列宽不变时对象复用）
    out2 = cache.render(("公式",), rows + [["$\\frac{c}{d}$"]], ["left"], 80)
    assert out2[0] is out[0]
    assert len(out2) > len(out)


def test_table_cell_shrink_and_wrap_with_formula():
    out = _render("| 列 |\n| --- |\n| $\\frac{a}{b}$ " + "很长的说明" * 3 + " |\n",
                  width=30)
    assert out[0].startswith("┌")
    assert out[-1].startswith("└")
    assert any("│" in p for p in out)


def test_render_paragraph_line_keeps_content():
    line = render_paragraph_line(r"$\frac{a}{b}$")
    assert "a" in line.plain and "b" in line.plain


# ── 流式 ─────────────────────────────────────────────────


def test_stream_preview_contains_multiline_math():
    r = AnsiStreamRenderer(width=70)
    r.write(r"公式 $\frac{a}{b}$ 结束")
    preview = [ln.plain for ln in r.take_preview_lines()]
    committed = [ln.plain for ln in r.take_lines()]
    r.close()
    final = [ln.plain for ln in r.take_lines()]
    all_lines = preview + committed + final
    assert any("─" in p for p in all_lines)
    assert any("公式" in p for p in all_lines)


def test_stream_chunked_matches_whole():
    md = r"前 $\frac{a}{b}$ 后" + "\n"
    whole = _render(md)
    r = AnsiStreamRenderer(width=80)
    for ch in md:
        r.write(ch)
    r.close()
    chunked = [ln.plain for ln in r.take_lines()]
    assert chunked == whole


# ── 预览缓存（一行源文本产出多行） ───────────────────────


def _two_rows(text: str) -> list[AnsiLine]:
    return [AnsiLine.of(text), AnsiLine.of(text + "!")]


def test_line_preview_cache_multiline_incremental():
    cache = LinePreviewCache()
    rows = cache.render(("k",), ["a", "b"], _two_rows)
    assert [r.plain for r in rows] == ["a", "a!", "b", "b!"]
    rows2 = cache.render(("k",), ["a", "b", "c"], _two_rows)
    assert [r.plain for r in rows2] == ["a", "a!", "b", "b!", "c", "c!"]
    rows3 = cache.render(("k",), ["a", "B"], _two_rows)
    assert [r.plain for r in rows3] == ["a", "a!", "B", "B!"]


def test_line_preview_cache_zero_row_output():
    cache = LinePreviewCache()

    def _maybe(text: str) -> list[AnsiLine]:
        return [] if text.startswith("#") else [AnsiLine.of(text)]

    rows = cache.render(("k",), ["a", "#b", "c"], _maybe)
    assert [r.plain for r in rows] == ["a", "c"]
    rows2 = cache.render(("k",), ["a", "#b", "c", "d"], _maybe)
    assert [r.plain for r in rows2] == ["a", "c", "d"]
