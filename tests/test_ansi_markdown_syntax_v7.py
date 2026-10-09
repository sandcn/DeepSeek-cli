"""ANSI 流式 Markdown 语法/渲染增强（第七批）测试。

覆盖本轮改动：

  - 新增语法：CriticMarkup 高亮 ``{==…==}``、大号文本 ``{+…+}``（与既有
    ``{-…-}`` 对称）、TUI 路径智能排版（typographer：``--``/``...``/``->``/
    ``<=``/``(c)``/``1/2``/``:emoji:``…，与 Rich 路径 ``_preprocess_text`` 同源）
  - 渲染修复：多行 setext 标题（整个段落成为标题）、块引用懒续行（``> a\\nb``）、
    列表项懒续行（``- a\\nb``）、GFM 表格列数宽容（缺列补空 / 多列忽略）、
    引用块结束后紧邻的顶层块不再被误加 ``│`` 前缀
  - 性能：智能排版快速判否（英文文本不因连字符触发逐帧预处理）
"""

from __future__ import annotations

import io
import time

from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_chunked(src: str, size: int = 1, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _rich(src: str) -> str:
    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write(src)
    r.close()
    return buf.getvalue()


# ══════════════════════════════════════════════════════════
# 新增语法 1：CriticMarkup 高亮 {==…==}
# ══════════════════════════════════════════════════════════


def test_critic_highlight_basic():
    assert _render("a {==marked==} b\n") == ["a marked b"]


def test_critic_highlight_styles_bg():
    """高亮节点使用黄底黑字样式（与 ``==x==`` 行内高亮同视觉）。"""
    r = AnsiStreamRenderer(width=72)
    r.write("{==hi==}\n")
    r.close()
    runs = [run for ln in r.take_lines() for run in ln.runs if run.text == "hi"]
    assert runs, "未找到高亮文本 run"
    st = runs[0].style
    assert st is not None and st.bg == 11 and st.fg == 0 and st.bold


def test_critic_highlight_nested_format():
    """高亮内部可嵌套行内格式（粗体等）。"""
    assert _render("{==a **b** c==}\n") == ["a b c"]


def test_critic_highlight_vs_plain_mark():
    """``{==x==}`` 与 ``==x==``（行内高亮）互不干扰。"""
    assert _render("{==a==} and ==b==\n") == ["a and b"]
    assert _render("{==a==}\n") == ["a"]


def test_critic_highlight_unclosed_is_plain():
    """未闭合 ``{==`` 原样输出（不吞内容）。"""
    assert _render("a {== b\n") == ["a {== b"]


def test_critic_highlight_all_five_marks():
    """CriticMarkup 五类标记齐备（增/删/替换/高亮/批注）。"""
    got = _render("{++add++} {--del--} {~~o~>n~~} {==hl==} {>>cmt<<}\n")
    # 各标记按语义渲染（删除线/插入/批注包裹等只在样式上体现，plain 去壳）
    assert "add" in got[0] and "del" in got[0] and "hl" in got[0]
    assert "→" in got[0] and "批注" in got[0]


def test_critic_highlight_rich_path():
    """Rich 路径同步支持高亮（两路径语义一致）。"""
    out = _rich("{==marked==}\n")
    assert "marked" in out
    assert "{==" not in out and "==}" not in out


def test_critic_highlight_chunked_consistent():
    src = "a {==marked==} b\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 新增语法 2：大号文本 {+…+}
# ══════════════════════════════════════════════════════════


def test_big_text_basic():
    assert _render("a {+big+} b\n") == ["a big b"]


def test_big_text_styles_bold():
    r = AnsiStreamRenderer(width=72)
    r.write("{+big+}\n")
    r.close()
    runs = [run for ln in r.take_lines() for run in ln.runs if run.text == "big"]
    assert runs and runs[0].style is not None and runs[0].style.bold


def test_big_text_does_not_break_critic_addition():
    """``{++add++}``（CriticMarkup 添加）不被 ``{+…+}`` 误吞。"""
    assert _render("{++added++}\n") == ["added"]
    assert _render("{+big+}\n") == ["big"]


def test_big_text_small_text_symmetry():
    assert _render("{-small-} {+big+}\n") == ["small big"]


def test_big_text_unclosed_is_plain():
    assert _render("a {+ b\n") == ["a {+ b"]


def test_big_text_rich_path():
    out = _rich("{+big+}\n")
    assert "big" in out and "{+" not in out


def test_big_text_chunked_consistent():
    src = "a {+big+} b\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 新增语法 3：智能排版（typographer，与 Rich 路径同源）
# ══════════════════════════════════════════════════════════


def test_typographer_dashes_and_ellipsis():
    assert _render("a -- b --- c ... d\n") == ["a \u2013 b \u2014 c \u2026 d"]


def test_typographer_arrows():
    assert _render("a -> b <- c => d <-> e\n") == ["a \u2192 b \u2190 c \u21d2 d \u2194 e"]


def test_typographer_math_symbols():
    assert _render("a <= b >= c != d ~= e +- f\n") == [
        "a \u2264 b \u2265 c \u2260 d \u2248 e \u00b1 f"
    ]


def test_typographer_copyright_and_fraction():
    assert _render("(c) (r) (tm) 1/2 3/4\n") == ["\u00a9 \u00ae \u2122 \u00bd \u00be"]


def test_typographer_emoji_and_entity():
    assert _render(":smile: &amp;\n") == ["\U0001f60a &"]


def test_typographer_not_in_english_words():
    """连字符/加号单词不被误改（``re-render`` / ``C++``）。"""
    assert _render("re-render C++ e-mail\n") == ["re-render C++ e-mail"]


def test_typographer_crosses_chunk_boundary():
    """跨 chunk 边界（``-`` 与 ``>`` 分处两次 write）仍正确替换。"""
    r = AnsiStreamRenderer(width=72)
    r.write("a -")
    r.write("> b\n")
    r.close()
    assert [ln.plain for ln in r.take_lines()] == ["a \u2192 b"]


def test_typographer_not_in_code_span_url():
    """代码 span 不做智能排版替换；链接 URL 中的 ``--`` 不泄漏为可见文本。"""
    assert _render("`a -- b`\n") == ["a -- b"]
    assert _render("[t](http://x/a--b)\n") == ["t"]


def test_typographer_rich_path_sync():
    """两路径同源：同一输入在 Rich 路径也做替换。"""
    out = _rich("a -> b -- c\n")
    assert "\u2192" in out and "\u2013" in out


def test_typographer_fast_path_plain_text_unchanged():
    """纯英文（无触发组合）走快路径、原样输出。"""
    text = "the quick brown fox jumps over the lazy dog"
    assert _render(text + "\n") == [text]


# ══════════════════════════════════════════════════════════
# 渲染修复 1：多行 setext 标题
# ══════════════════════════════════════════════════════════


def test_setext_multiline_h1():
    assert _render("第一行\n第二行\n===\n") == ["第一行", "第二行"]


def test_setext_multiline_h2():
    assert _render("第一行\n第二行\n---\n") == ["第一行", "第二行"]


def test_setext_multiline_not_hr():
    got = _render("第一行\n第二行\n===\n")
    assert not any("\u2500" * 10 in ln for ln in got)


def test_setext_after_blank_is_hr():
    """空行分隔后 ``---`` 仍是分隔线。"""
    got = _render("text\n\n---\n")
    assert "text" in got
    assert any("\u2500" * 10 in ln for ln in got)


def test_setext_multiline_preview_consistent():
    r = AnsiStreamRenderer(width=72)
    r.write("第一行\n第二行\n===")
    assert [ln.plain for ln in r.take_preview_lines()] == ["第一行", "第二行"]


# ══════════════════════════════════════════════════════════
# 渲染修复 2：块引用懒续行
# ══════════════════════════════════════════════════════════


def test_blockquote_lazy_continuation():
    assert _render("> foo\nbar\n") == ["\u2502 foo", "\u2502 bar"]


def test_blockquote_lazy_multiline():
    assert _render("> a\nb\nc\n") == ["\u2502 a", "\u2502 b", "\u2502 c"]


def test_blockquote_lazy_then_prefixed():
    assert _render("> a\nb\n> c\n") == ["\u2502 a", "\u2502 b", "\u2502 c"]


def test_blockquote_not_lazy_after_blank():
    assert _render("> foo\n\nbar\n") == ["\u2502 foo", "bar"]


def test_blockquote_not_lazy_for_new_block():
    """块起始（标题/列表）不作为懒续行——引用关闭、新块在顶层。"""
    assert _render("> foo\n# h\n") == ["\u2502 foo", "h"]
    assert _render("> foo\n- a\n") == ["\u2502 foo", "\u2022 a"]


def test_blockquote_lazy_chunked_consistent():
    src = "> foo\nbar\nbaz\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 渲染修复 3：列表项懒续行
# ══════════════════════════════════════════════════════════


def test_list_lazy_continuation():
    assert _render("- foo\nbar\n") == ["\u2022 foo", "  bar"]


def test_ordered_list_lazy_continuation():
    assert _render("1. foo\nbar\n") == ["1. foo", "  bar"]


def test_list_lazy_multiline():
    assert _render("- a\nb\nc\n") == ["\u2022 a", "  b", "  c"]


def test_list_not_lazy_after_blank():
    assert _render("- foo\n\nbar\n") == ["\u2022 foo", "bar"]


def test_list_new_item_starts_new_item():
    assert _render("- foo\nbar\n- baz\n") == ["\u2022 foo", "  bar", "\u2022 baz"]


def test_list_lazy_then_blank():
    assert _render("- foo\nbar\n\npara\n") == ["\u2022 foo", "  bar", "para"]


def test_list_lazy_chunked_consistent():
    src = "- foo\nbar\nbaz\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 渲染修复 4：GFM 表格列数宽容
# ══════════════════════════════════════════════════════════


def test_table_extra_column_ignored():
    lines = _render("| a | b |\n|---|---|\n| 1 | 2 | 3 |\n")
    assert lines[0].startswith("\u250c")
    assert lines[-1].startswith("\u2514")
    assert "| 1 | 2 | 3 |" not in lines


def test_table_missing_column_padded():
    lines = _render("| a | b | c |\n|---|---|---|\n| 1 |\n")
    assert lines[0].startswith("\u250c")
    assert lines[-1].startswith("\u2514")
    # 缺列补空：数据行仍渲染，且不与后续内容粘连
    assert any("1" in ln for ln in lines)


def test_table_extra_column_chunked_consistent():
    src = "| a | b |\n|---|---|\n| 1 | 2 | 3 |\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 渲染修复 5：引用块结束后的顶层块前缀
# ══════════════════════════════════════════════════════════


def test_heading_after_blockquote_has_no_prefix():
    """引用块结束后紧邻的顶层标题不带 ``│`` 前缀。"""
    got = _render("> foo\n# h\n")
    assert got == ["\u2502 foo", "h"]


def test_list_after_blockquote_has_no_prefix():
    got = _render("> foo\n1. x\n")
    assert got == ["\u2502 foo", "1. x"]


# ══════════════════════════════════════════════════════════
# 性能：智能排版判否 + 流式成本有界
# ══════════════════════════════════════════════════════════


_SENTENCE = ("Streaming renderers must re-render the active block on every "
             "single write, so the per-write cost dominates the perceived "
             "smoothness of the interface. ")


def test_english_paragraph_stream_not_slowed_by_typographer():
    """含连字符的英文长段落流式写入不被智能排版拖慢（修复前每帧做 4 遍扫描）。"""
    text = _SENTENCE * 60 + "\n\n"
    r = AnsiStreamRenderer(width=100)
    r.write(text)  # 预热
    t0 = time.perf_counter()
    r2 = AnsiStreamRenderer(width=100)
    for i in range(0, len(text), 8):
        r2.write(text[i:i + 8])
    r2.close()
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"英文长段落流式耗时 {elapsed:.3f}s"


def test_needs_typography_false_for_plain_words():
    from src.renderer.ansi.inline import _needs_typography

    assert _needs_typography("re-render C++ e-mail and/or") is False
    assert _needs_typography("a -> b") is True
    assert _needs_typography("x -- y") is True
    assert _needs_typography("1/2") is True


def test_last_typo_pos_tracks_trigger():
    from src.renderer.ansi.inline import _last_typo_pos

    assert _last_typo_pos("plain text") == -1
    assert _last_typo_pos("a -> b") == 2
    assert _last_typo_pos("x -- y") == 2
    assert _last_typo_pos("see (c)") == 4
    assert _last_typo_pos("1/2") == 0


def test_typography_trigger_incremental_across_window():
    """超长段落：开头触发被窗口滑出后仍保持正确（增量维护触发位置）。"""
    text = "a -> b " + ("word " * 2000) + "\n"
    lines = _render_chunked(text, 16, width=100)
    assert lines and "\u2192" in lines[0]
    assert "->" not in "".join(lines)


def test_para_last_typo_maintained_incrementally():
    r = AnsiStreamRenderer(width=100)
    r._note_paragraph_triggers("plain text ")
    assert r._para_last_typo == -1
    r._note_paragraph_triggers("plain text a ->")
    assert r._para_last_typo == 13
    r._note_paragraph_triggers("plain text a -> b and more words")
    assert r._para_last_typo == 13


def test_last_typo_pos_detects_cross_chunk_pair():
    """增量维护能识别跨 chunk 边界的 pattern（``-`` + ``-``）。"""
    r = AnsiStreamRenderer(width=100)
    r._note_paragraph_triggers("abc -")
    assert r._para_last_typo == -1
    r._note_paragraph_triggers("abc --")
    assert r._para_last_typo == 4


def test_critic_highlight_cross_line_paragraph():
    """``{==`` 跨软换行仍按未闭合定界符处理（段落边界扫描同步）。"""
    got = _render("a {==b\nc==} d\n")
    assert got == ["a b", "c d"] or got == ["a b c d"]

