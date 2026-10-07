"""ANSI 流式 Markdown 语法/渲染增强（第四批）测试。

覆盖本轮改动：

  - 缺陷修复：段落含 ``|`` 时内容顺序错乱（表格候选行越序）、表格与段落
    顺序、引用块内含 ``|`` 文本不误判为表格
  - 代码块属性：连续多组 ``{...}``、裸属性、代码首行保留、
    ``linenostart``/``linenostep``
  - Diff/patch 语义高亮（TUI 路径）
  - ``hl_lines`` 高亮行整行背景
  - 行内脚注 ``^[文本]``（正文序号 + 文末脚注列表 + 幂等合并 +
    与定义式脚注共用编号）
  - 链接 OSC 8（Run.link 贯通：行内/自动/邮箱/表格/换行/截断/TUI 转换层）
  - 分隔线整宽渲染 + 两端渐隐
  - 引用前缀 / 列表符号按嵌套深度分级着色
  - 数学多行水平拼接（分数/根式）列对齐
  - 词法分析器失败结果缓存
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi import blocks as _blocks
from src.renderer.ansi import table as _table
from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line, truncate_line
from src.renderer.ansi.inline import render_inline


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_chunked(src: str, size: int = 3, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _inline_plain(text: str, ctx=None) -> str:
    return "".join(run.text for run in render_inline(text, ctx=ctx))


# ══════════════════════════════════════════════════════════
# 缺陷修复：内容顺序
# ══════════════════════════════════════════════════════════


def test_paragraph_with_pipe_preserves_order():
    lines = _render("第一行\n含有 || 的内容\n第三行\n")
    assert lines[0] == "第一行"
    assert "含有" in lines[1]
    assert lines[2] == "第三行"


def test_chunked_paragraph_with_pipe_preserves_order():
    lines = _render_chunked("第一行\n含有 || 的内容\n第三行\n")
    joined = "\n".join(lines)
    assert joined.index("第一行") < joined.index("含有")
    assert joined.index("含有") < joined.index("第三行")


def test_paragraph_before_table_keeps_order():
    lines = _render("前言文字\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    assert lines[0] == "前言文字"
    # 表格边框紧随段落之后
    assert lines[1].startswith("\u250c")


def test_table_still_renders_after_blank_line():
    lines = _render("\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    body = [l for l in lines if l]
    assert body[0].startswith("\u250c")


def test_blockquote_pipe_text_not_table():
    lines = _render("> a | b\n> c | d\n")
    joined = "\n".join(lines)
    assert "a | b" in joined and "c | d" in joined
    assert "\u250c" not in joined


# ══════════════════════════════════════════════════════════
# 代码块属性
# ══════════════════════════════════════════════════════════


def test_fence_multiple_brace_attr_groups():
    lines = _render("```python {1,3}{linenos}\na = 1\nb = 2\nc = 3\n```\n")
    joined = "\n".join(lines)
    assert "{linenos}" not in joined
    # 行号开启（"1 " 前缀）
    assert any(l.strip().startswith("1 ") for l in lines)


def test_fence_bare_attrs_recognized():
    lines = _render('```python hl_lines="1" linenos\na = 1\nb = 2\n```\n')
    joined = "\n".join(lines)
    assert "hl_lines" not in joined
    assert any("\u25b8" in l for l in lines)  # 高亮行前缀


def test_fence_code_first_line_preserved():
    lines = _render("```python print(1)\nx = 1\n```\n")
    joined = "\n".join(lines)
    assert "print(1)" in joined


def test_fence_hash_first_line_preserved():
    lines = _render("```python # 注释\nx = 1\n```\n")
    assert "# 注释" in "\n".join(lines)


def test_linenostart_and_step():
    lines = _render("```python {linenos}{linenostart=10}{linenostep=5}\n"
                    "a = 1\nb = 2\nc = 3\n```\n")
    text = [l.strip() for l in lines if l.strip()]
    assert text[1].startswith("10 ")
    assert text[2].startswith("15 ")
    assert text[3].startswith("20 ")


def test_linenostart_implies_linenos():
    from src.renderer._utils import parse_lineno_options

    assert parse_lineno_options("{linenostart=5}") == (True, 5, 1)
    assert parse_lineno_options("{linenos}{step=2}") == (True, 1, 2)
    assert parse_lineno_options("") == (False, 1, 1)
    assert parse_lineno_options("{linenos}{linenostart=0}") == (True, 1, 1)


def test_parse_highlight_lines_still_works_with_braces():
    from src.renderer._utils import parse_highlight_lines

    assert parse_highlight_lines("{1,3}{linenos}") == [1, 3]


# ══════════════════════════════════════════════════════════
# Diff / patch 高亮
# ══════════════════════════════════════════════════════════


def _styled_lines(src: str):
    r = AnsiStreamRenderer(width=72)
    r.write(src)
    r.close()
    return r.take_lines()


def test_diff_lines_semantic_colors():
    src = "```diff\n@@ -1 +1 @@\n-old line\n+new line\n context\n```\n"
    lines = _styled_lines(src)
    by_plain = {l.plain: l for l in lines}
    add = by_plain["+new line"]
    dele = by_plain["-old line"]
    assert add.runs[0].style.bg != dele.runs[0].style.bg
    assert add.runs[0].style.fg != dele.runs[0].style.fg


def test_patch_lang_uses_diff_style():
    lines = _styled_lines("```patch\n+a\n-b\n```\n")
    joined = "\n".join(l.plain for l in lines)
    assert "+a" in joined and "-b" in joined
    add = next(l for l in lines if l.plain == "+a")
    assert add.runs[0].style.bg is not None


def test_hl_lines_row_background():
    lines = _styled_lines("```python {1}\na = 1\nb = 2\n```\n")
    hl = next(l for l in lines if "a = 1" in l.plain)
    assert any(r.style is not None and r.style.bg is not None for r in hl.runs)


def test_rich_code_state_display_line_number():
    from src.renderer.states import _CodeBlockState

    st = _CodeBlockState(linenos=True, lineno_start=10, lineno_step=5)
    st.line_num = 1
    assert st.display_line_number() == 10
    st.line_num = 3
    assert st.display_line_number() == 20


def test_rich_diff_langs_covers_patch():
    from src.renderer.handlers.code import _DIFF_LANGS

    assert "diff" in _DIFF_LANGS and "patch" in _DIFF_LANGS


# ══════════════════════════════════════════════════════════
# 行内脚注 ^[...]
# ══════════════════════════════════════════════════════════


def test_inline_footnote_number_and_list():
    lines = _render("正文含脚注^[这是脚注内容]结尾。\n")
    assert lines[0] == "正文含脚注[1]结尾。"
    assert any("这是脚注内容" in l and l.strip().startswith("[1]")
               for l in lines)


def test_inline_footnote_merges_same_content():
    lines = _render("A^[同一内容]B^[同一内容]C\n")
    assert lines[0] == "A[1]B[1]C"
    assert sum(1 for l in lines if "同一内容" in l) == 1


def test_inline_footnote_distinct_content():
    lines = _render("A^[甲]B^[乙]C\n")
    assert lines[0] == "A[1]B[2]C"


def test_chunked_inline_footnote_not_duplicated():
    lines = _render_chunked("正文^[脚注甲]与^[脚注乙]结束。\n", size=2)
    assert sum(1 for l in lines if "脚注甲" in l) == 1
    assert sum(1 for l in lines if "脚注乙" in l) == 1


def test_inline_footnote_shared_numbering_with_definition():
    lines = _render("引用[^a] 与行内^[行内注]。\n\n[^a]: 定义脚注\n")
    body = lines[0]
    assert "[1]" in body and "[2]" in body
    joined = "\n".join(lines)
    assert "定义脚注" in joined and "行内注" in joined


def test_inline_footnote_with_markdown_content():
    lines = _render("文本^[**粗**内容]尾。\n")
    assert any("粗" in l for l in lines if "内容" in l)


def test_inline_footnote_unclosed_left_as_text():
    assert _inline_plain("文本^[未闭合") == "文本^[未闭合"


def test_superscript_still_works_after_inline_footnote():
    # 无上下文时行内脚注保留原语法文本（不臆造编号）
    assert _inline_plain("x^2^ 与 ^[注]") == "x\u00b2 与 [^\u6ce8]"


def test_rich_path_registers_inline_footnote_handler():
    from src.renderer.inline_nodes import InlineFootnoteNode
    from src.renderer import inline_renderer

    assert InlineFootnoteNode in inline_renderer.InlineRenderer._NODE_DISPATCH


# ══════════════════════════════════════════════════════════
# 链接 OSC 8
# ══════════════════════════════════════════════════════════


def test_link_run_carries_url():
    runs = render_inline("看 [示例](https://example.com/x) 链接")
    link_run = next(r for r in runs if r.text == "示例")
    assert link_run.link == "https://example.com/x"


def test_autolink_and_email_carry_link():
    runs = render_inline("<https://a.example> 与 <b@c.com>")
    urls = {r.text: r.link for r in runs if r.link}
    assert urls["https://a.example"] == "https://a.example"
    assert urls["b@c.com"] == "mailto:b@c.com"


def test_adjacent_links_not_merged_across_urls():
    runs = render_inline("[一](https://1.e) [二](https://2.e)")
    assert runs[0].link == "https://1.e"
    assert runs[-1].link == "https://2.e"


def test_wrap_line_preserves_link():
    url = "https://example.com/very/long/path"
    line = AnsiLine([Run("很长的链接文本" * 6, None, url)])
    parts = wrap_line(line, 10)
    assert len(parts) > 1
    for seg in parts:
        for r in seg.runs:
            assert r.link == url


def test_truncate_line_preserves_link():
    url = "https://example.com/x"
    line = AnsiLine([Run("链接文本内容", None, url)])
    out = truncate_line(line, 4)
    assert out.runs and all(r.link == url for r in out.runs)


def test_table_cell_link_preserved():
    lines = _styled_lines("| 表头 |\n|---|\n| [链接](https://t.example) |\n")
    run = next(r for l in lines for r in l.runs if r.text == "链接")
    assert run.link == "https://t.example"


def test_tui_convert_row_preserves_link():
    from src.tui.app._ansi_convert import _convert_ansi_row

    aline = AnsiLine([Run("示例", None, "https://example.com")])
    rows = _convert_ansi_row(aline, 80, "content")
    assert rows[0][0].link == "https://example.com"


def test_ink_line_renders_osc8():
    from src.tui.ink.output import Line, StyledRun

    line = Line([StyledRun("示例", None, "https://example.com")])
    assert "\x1b]8;;https://example.com" in line.render()


# ══════════════════════════════════════════════════════════
# 分隔线
# ══════════════════════════════════════════════════════════


def test_hr_full_width():
    lines = _render("---\n")
    hr = [l for l in lines if l and set(l) == {"\u2500"}]
    assert hr and len(hr[0]) == 72


def test_hr_fade_styles():
    line = _blocks.render_hr(None, 60)[0]
    assert len({r.style for r in line.runs}) > 1


def test_hr_default_width_without_width():
    line = _blocks.render_hr(None)[0]
    assert line.plain == "\u2500" * 40


# ══════════════════════════════════════════════════════════
# 嵌套层次配色
# ══════════════════════════════════════════════════════════


def test_blockquote_prefix_depth_colors():
    s1 = _blocks.bq_prefix_style(1)
    s2 = _blocks.bq_prefix_style(2)
    assert s1 != s2
    assert _blocks.bq_prefix_style(99) == _blocks.bq_prefix_style(4)


def test_bullet_depth_colors():
    assert _blocks.bullet_style(1) != _blocks.bullet_style(2)
    line = _blocks.render_blockquote_line("x", depth=0)
    assert line.runs[0].style == _blocks.bq_prefix_style(1)


# ══════════════════════════════════════════════════════════
# 数学多行拼接对齐
# ══════════════════════════════════════════════════════════


def _math_body(src: str) -> list:
    """数学块内容行（取边框内文本）。"""
    from src.renderer.ansi.math import render_math_block

    out = []
    for line in render_math_block(src):
        parts = line.plain.split("\u2502")
        if len(parts) >= 3:
            out.append(parts[1])
    return out


def test_math_fraction_column_alignment():
    body = _math_body(r"E = mc^2 + \frac{a}{b}")
    num, bar = body[0], body[1]
    assert num.strip() == "a"
    assert abs(num.index("a") - bar.index("\u2500")) <= 2


def test_math_sqrt_rows_aligned():
    body = _math_body(r"\sqrt{x+1} + \frac{1}{2}")
    num, bar = body[0], body[1]
    assert "1" in num and "\u2500" in bar
    assert abs(num.index("1") - bar.index("\u2500")) <= 2


# ══════════════════════════════════════════════════════════
# 词法分析器失败缓存
# ══════════════════════════════════════════════════════════


def test_get_lexer_unknown_lang_cached():
    from src.renderer._rendering._code import _LEXER_CACHE, get_lexer

    assert get_lexer("no-such-lexer-xyz") is None
    assert "no-such-lexer-xyz" in _LEXER_CACHE
    assert _LEXER_CACHE["no-such-lexer-xyz"] is None


def test_wrap_line_without_link_unchanged_identity():
    line = AnsiLine([Run("plain text")])
    out = wrap_line(line, 40)
    assert len(out) == 1
    assert out[0].plain == "plain text"
