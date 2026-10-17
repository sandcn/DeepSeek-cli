"""ANSI 流式 Markdown 语法/渲染修复（第十五批）测试。

本轮改动：

  - **fenced div 任意长度栅栏**：``:::: note`` / 多级不同长度嵌套按 Pandoc
    规则闭合（闭合栅栏不短于开启栅栏），修复 4 个冒号的类型被解析成 ``:``、
    外层无法闭合、内层闭合错位的问题。
  - **引用块内列表上下文**：引用中的列表跨行保留列表上下文——列表项内的
    代码块 / 子列表 / 嵌套引用不再退化为顶层块。
  - **GFM 表格严格模式**：无 delimiter row 的含 pipe 文本保持段落，不再被
    误判为表格；紧邻表格的 ``Table: 说明`` 表注正确识别（不再被字母快速
    通道当段落）。
  - **空 ATX 标题**：单独的 ``#`` / ``##`` 识别为空标题（不再原样输出）。
  - **Pandoc 属性组代码围栏**：`` ```{python} `` / `` ```{.python
    .numberLines} `` / `` ```{lang=js} `` / `` ```{python title="x"} ``。
  - **块级脚注**：``[^1]:`` 定义内容位于后续缩进块（列表 / 代码 / 引用 /
    表格 / 多段落）时按完整 Markdown 子解析渲染，定义不再泄漏到正文。
  - **性能（格式活动行）**：同一段落追加纯文本时复用上一帧渲染结果
    （``_delta_can_append_plain``），并抑制脚注续行的错误代码块预览。
"""

from __future__ import annotations

import pytest

import src.renderer.ansi as ansi_pkg
from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _chunked(src: str, size: int = 3, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ══════════════════════════════════════════════════════════
# fenced div：任意长度栅栏 / 不同长度嵌套
# ══════════════════════════════════════════════════════════

def test_fenced_div_four_colon_fence():
    assert _render(":::: warning\nbody\n::::\n") == ["▪ WARNING", "  body"]


def test_fenced_div_nested_different_fence_lengths():
    got = _render(":::: note\n::: tip\nbody\n:::\n::::\n")
    assert got == ["▪ NOTE", "  ▪ TIP", "    body"]


def test_fenced_div_deeply_nested_fence_lengths():
    got = _render("::::: a\n:::: b\n::: c\nx\n:::\n::::\n:::::\n")
    assert got == ["▪ A", "  ▪ B", "    ▪ C", "      x"]


def test_fenced_div_same_length_nested_still_works():
    got = _render("::: outer\n::: inner\ninside\n:::\nafter\n:::\n")
    assert got == ["▪ OUTER", "  ▪ INNER", "    inside", "  after"]


# ══════════════════════════════════════════════════════════
# 引用块内列表上下文
# ══════════════════════════════════════════════════════════

def test_blockquote_list_nested_list_keeps_depth():
    assert _render("> - a\n>   - b\n") == ["│ • a", "│   ◦ b"]


def test_blockquote_nested_quote_with_list_keeps_depth():
    assert _render("> > - a\n> >   - b\n") == ["│ │ • a", "│ │   ◦ b"]


def test_blockquote_list_code_block_keeps_list_indent():
    got = _render("> - a\n>   ```js\n>   x\n>   ```\n")
    assert got == ["│ • a", "│   ```js [js]", "│   x", "│   ```"]


def test_blockquote_list_nested_quote_keeps_indent():
    got = _render("> 1. a\n>    > q\n")
    assert got[0] == "│ 1. a"
    assert got[1].startswith("│    ") and got[1].endswith("q")


# ══════════════════════════════════════════════════════════
# GFM 表格严格模式
# ══════════════════════════════════════════════════════════

def test_pipe_text_without_delimiter_with_leading_pipe_is_paragraph():
    assert _render("| a | b |\n| 1 | 2 |\n") == ["| a | b |", "| 1 | 2 |"]


def test_pipe_text_without_delimiter_in_blockquote_is_paragraph():
    assert _render("> | a | b |\n> | 1 | 2 |\n") == [
        "│ | a | b |", "│ | 1 | 2 |",
    ]


def test_table_with_delimiter_still_renders():
    lines = _render("| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert lines[0].startswith("┌")
    assert any("1" in ln and "2" in ln for ln in lines)


def test_pipe_text_without_delimiter_stream_matches_oneshot():
    src = "| a | b |\n| 1 | 2 |\n"
    assert _chunked(src, 1) == _render(src)


# ══════════════════════════════════════════════════════════
# 表格表注（紧跟表格的 Table: / : 说明）
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("caption", [
    "Table: cap", "Caption: cap", ": cap", "Table: **cap**",
])
def test_table_caption_immediately_after_table(caption):
    lines = _render("| a |\n|---|\n| 1 |\n" + caption + "\n")
    assert lines[0].startswith("┌")
    assert lines[-1] == "  cap"


def test_table_caption_stream_matches_oneshot():
    src = "| a |\n|---|\n| 1 |\nTable: cap\n"
    assert _chunked(src, 2) == _render(src)


# ══════════════════════════════════════════════════════════
# 空 ATX 标题
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src", ["#\n", "##\n", "###\n", "# \n"])
def test_empty_atx_heading_not_literal(src):
    assert _render(src) == [""]


def test_hash_without_space_still_paragraph():
    assert _render("#hashtag\n") == ["#hashtag"]


# ══════════════════════════════════════════════════════════
# Pandoc 属性组代码围栏
# ══════════════════════════════════════════════════════════

def test_fence_braced_language():
    lines = _render("```{python}\nx=1\n```\n")
    assert lines[0].startswith("```python")


def test_fence_braced_class_language_and_line_numbers():
    lines = _render("```{.python .numberLines}\nx=1\ny=2\n```\n")
    assert lines[0].startswith("```python")
    assert lines[1].startswith(" 1 ") and lines[2].startswith(" 2 ")


def test_fence_braced_lang_attr():
    lines = _render("```{lang=js}\nx=1\n```\n")
    assert lines[0].startswith("```js")


def test_fence_braced_title_attr():
    lines = _render('```{python title="demo.py"}\nx=1\n```\n')
    assert lines[0] == "┌─ demo.py"
    assert lines[1].startswith("```python")


def test_fence_braced_only_render_options_has_no_language():
    lines = _render("```{.numberLines}\nx=1\n```\n")
    assert lines[0] == "```"
    assert lines[1].startswith(" 1 ")


def test_fence_braced_attrs_stream_matches_oneshot():
    src = "```{.python .numberLines startFrom=5}\nx=1\n```\n"
    assert _chunked(src, 2) == _render(src)


# ══════════════════════════════════════════════════════════
# 块级脚注
# ══════════════════════════════════════════════════════════

def test_footnote_block_list():
    got = _render("a[^1]\n\n[^1]:\n    - one\n    - two\n")
    assert got[0] == "a[1]"
    assert "• one" in got[-2]
    assert "• two" in got[-1]
    assert not any(ln.startswith("[^1]") for ln in got)


def test_footnote_block_code():
    got = _render("a[^1]\n\n[^1]:\n    ```js\n    x\n    ```\n")
    joined = "\n".join(got)
    assert "```js" in joined and "x" in joined
    assert "[^1]:" not in joined


def test_footnote_block_quote_and_table():
    quote = _render("a[^1]\n\n[^1]:\n    > quote\n")
    assert any("│ quote" in ln for ln in quote)
    table = _render("a[^1]\n\n[^1]:\n    | a |\n    |---|\n    | 1 |\n")
    assert any("┌" in ln for ln in table)
    assert any("│ a │" in ln for ln in table)


def test_footnote_block_multiple_paragraphs():
    got = _render("a[^1]\n\n[^1]: first\n\n    - item\n\n    after\n")
    joined = "\n".join(got)
    assert "first" in joined
    assert "• item" in joined
    assert "after" in joined


def test_footnote_block_stream_matches_oneshot():
    src = "a[^1]\n\n[^1]:\n    - one\n    - two\n"
    assert _chunked(src, 1) == _render(src)


def test_footnote_continuation_preview_not_code_block():
    """脚注续行活动行预览不得出现缩进代码围栏（闭合后突然变成脚注）。"""
    r = AnsiStreamRenderer(width=50)
    r.write("a[^1]\n\n[^1]:\n    - on")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert all("```" not in ln for ln in preview)
    r.close()


# ══════════════════════════════════════════════════════════
# 性能：格式活动行纯文本追加快路径
# ══════════════════════════════════════════════════════════

def test_delta_can_append_plain_accepts_plain_text():
    assert ansi_pkg._delta_can_append_plain("hello **x** ", "tail")
    assert ansi_pkg._delta_can_append_plain("hello world", " again")


@pytest.mark.parametrize("delta", [
    "*", "_", "`", "~", "$", "!", "[", "<", "&", "^", ":", "|", "{", "%",
    "www.", "http:", "->", "...", "(c)", "1/2", "&amp;", "\\",
])
def test_delta_can_append_plain_rejects_markup(delta):
    assert not ansi_pkg._delta_can_append_plain("plain text ", delta)


@pytest.mark.parametrize("prev", [
    "open [bracket",
    "open <tag",
    "open &amp",
    "trailing \\",
])
def test_delta_can_append_plain_rejects_unclosed_structures(prev):
    assert not ansi_pkg._delta_can_append_plain(prev, "tail")


def test_format_active_line_fast_path_used_and_equivalent(monkeypatch):
    """长段落逐字符流式：快路径命中且产出与常规路径逐行一致。"""
    hits = [0]
    orig = ansi_pkg._delta_can_append_plain

    def counted(prev, delta):
        ok = orig(prev, delta)
        if ok:
            hits[0] += 1
        return ok

    monkeypatch.setattr(ansi_pkg, "_delta_can_append_plain", counted)
    src = "prefix **bold** " + "plain tail words and numbers 123, " * 25 + "\n"
    got = _chunked(src, 5)
    assert hits[0] > 0

    monkeypatch.setattr(ansi_pkg, "_delta_can_append_plain",
                        lambda prev, delta: False)
    slow = _chunked(src, 5)
    assert got == slow
    # 一次性渲染（提交路径）语义一致
    assert _render(src) == got


def test_format_active_fast_path_preserves_runs(monkeypatch):
    """快路径追加的纯文本与常规解析产出相同 Run 样式序列。"""
    src = "**bold** tail " + "words " * 40 + "\n"

    def runs_of(chunk):
        r = AnsiStreamRenderer(width=60)
        for i in range(0, len(src), chunk):
            r.write(src[i:i + chunk])
        r.close()
        return [[(run.text, run.style) for run in ln.runs]
                for ln in r.take_lines()]

    fast = runs_of(4)
    monkeypatch.setattr(ansi_pkg, "_delta_can_append_plain",
                        lambda prev, delta: False)
    slow = runs_of(4)
    assert fast == slow


def test_format_active_fast_path_rejects_large_line():
    """超过活动行窗口上限的段落不缓存（窗口滑窗，尾部追加不成立）。"""
    r = AnsiStreamRenderer(width=60)
    r.write("**bold** " + "x" * (ansi_pkg._FORMAT_ACTIVE_MAX_CHARS + 100))
    r.write(" tail")
    assert r._format_active_cache is None
    r.close()
