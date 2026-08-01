"""测试 renderer/ansi — 端到端 AnsiStreamRenderer + 各块渲染。

纯 StyledText/AnsiLine 断言，StringIO 捕获。
"""

from __future__ import annotations

from src.renderer.ansi.style import Style
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline
from src.renderer.ansi.code import render_code_block
from src.renderer.ansi.table import render_table


def _render(md: str) -> list:
    r = AnsiStreamRenderer()
    r.write(md)
    r.close()
    return r.take_lines()


class TestStreamRenderer:
    def test_heading(self):
        lines = _render("# Hello\n")
        assert len(lines) >= 1
        assert lines[0].plain == "Hello"
        assert lines[0].runs[0].style.bold is True

    def test_paragraph(self):
        lines = _render("plain paragraph\n")
        assert lines[0].plain == "plain paragraph"

    def test_bold_inline(self):
        lines = _render("a **bold** text\n")
        plain = lines[0].plain
        assert plain == "a bold text"
        # bold run 存在
        bold_runs = [r for r in lines[0].runs if r.text == "bold"]
        assert bold_runs and bold_runs[0].style.bold is True

    def test_inline_code(self):
        lines = _render("use `code` here\n")
        code_runs = [r for r in lines[0].runs if r.text == "code"]
        assert code_runs and code_runs[0].style.bold is True

    def test_list(self):
        lines = _render("- one\n- two\n")
        plains = [l.plain for l in lines if l.plain]
        assert plains == ["\u2022 one", "\u2022 two"]

    def test_blockquote(self):
        lines = _render("> quoted\n")
        assert lines[0].plain.startswith("\u2502")

    def test_table(self):
        lines = _render("| A | B |\n|---|---|\n| 1 | 2 |\n")
        plains = [l.plain for l in lines if l.plain]
        assert plains[0].startswith("\u250c")
        assert "A" in plains[1]
        assert plains[-1].startswith("\u2514")

    def test_code_block(self):
        lines = _render("```python\nprint(1)\n```\n")
        plains = [l.plain for l in lines if l.plain]
        assert plains[0].startswith("```")
        assert "print(1)" in plains
        assert plains[-1] == "```"

    def test_hr(self):
        lines = _render("---\n")
        assert lines and lines[0].plain.startswith("\u2500")

    def test_admonition(self):
        lines = _render("> [!NOTE]\n> body text\n")
        plains = [l.plain for l in lines if l.plain]
        assert any("NOTE" in p for p in plains)

    def test_empty_input(self):
        lines = _render("")
        assert lines == []

    def test_toc_at_end(self):
        """流式 markdown 结束时在末尾渲染目录（TOC）。"""
        r = AnsiStreamRenderer(width=50)
        r.write("# 第一章\n\n正文。\n\n# 第二章\n\n结束。\n")
        r.close()
        plains = [l.plain for l in r.take_lines()]
        toc_idx = next(i for i, p in enumerate(plains) if "目录" in p)
        end_idx = next(i for i, p in enumerate(plains) if "结束。" in p)
        assert toc_idx > end_idx, "TOC 应在内容末尾"
        assert any("第一章" in p and "┣" in p for p in plains)

    def test_toc_no_headings(self):
        """无标题时无 TOC。"""
        r = AnsiStreamRenderer(width=50)
        r.write("plain text\n\nmore\n")
        r.close()
        plains = [l.plain for l in r.take_lines()]
        assert not any("目录" in p for p in plains)

    def test_streaming_chunks(self):
        """分块流式输入逐块累积。"""
        r = AnsiStreamRenderer()
        r.write("para ")
        r.write("one\n\n")
        r.write("para two\n")
        r.close()
        plains = [l.plain for l in r.take_lines()]
        assert plains[0] == "para one"

    def test_take_lines_consumes(self):
        r = AnsiStreamRenderer()
        r.write("hi\n")
        r.close()
        lines1 = r.take_lines()
        lines2 = r.take_lines()
        assert lines1 and lines2 == []

    def test_close_idempotent(self):
        r = AnsiStreamRenderer()
        r.write("x\n")
        r.close()
        n = len(r.take_lines())
        r.close()
        assert len(r.take_lines()) == 0
        assert n >= 1


class TestInline:
    def test_bold_italic_code(self):
        runs = render_inline("**b** *i* `c`")
        texts = [r.text for r in runs]
        assert "b" in texts and "i" in texts and "c" in texts
        b = [r for r in runs if r.text == "b"][0]
        assert b.style.bold is True
        i = [r for r in runs if r.text == "i"][0]
        assert i.style.italic is True

    def test_link(self):
        runs = render_inline("[label](https://x.com)")
        label = [r for r in runs if r.text == "label"]
        assert label and label[0].style.underline is True

    def test_strike(self):
        runs = render_inline("~~gone~~")
        g = [r for r in runs if r.text == "gone"]
        assert g and g[0].style.dim is True

    def test_plain_fallback(self):
        assert render_inline("plain text")[0].text == "plain text"


class TestCode:
    def test_code_block_python(self):
        lines = render_code_block("def f():\n    return 1", lang="python")
        plains = [l.plain for l in lines]
        assert "def f():" in plains
        assert "    return 1" in plains

    def test_code_block_no_lexer(self):
        lines = render_code_block("plain text", lang="notalang")
        assert lines[0].plain.startswith("```")

    def test_code_block_highlight_lines(self):
        lines = render_code_block("a\nb", lang="text", highlight_lines=[1])
        assert lines  # 不抛异常


class TestTable:
    def test_table_direct(self):
        class _Tok:
            meta = {"rows": [["A", "B"], ["1", "2"]], "alignments": ["left", "right"]}
        lines = render_table(_Tok())
        assert lines[0].plain.startswith("\u250c")
        assert "A" in lines[1].plain
        assert lines[-1].plain.startswith("\u2514")

    def test_table_alignment_matches_border(self):
        """边框宽度与单元格宽度对齐（border 段 = 列宽+2 = 单元格总宽）。"""
        class _Tok:
            meta = {"rows": [["Name", "Age"], ["Alice", "30"]], "alignments": ["left", "left"]}
        lines = render_table(_Tok())
        border = lines[0]  # ┌───────┬─────┐
        header = lines[1]  # │ Name  │ Age │
        assert border.width == header.width
        # 各单元格两侧留白：'│ Name  │ Age │'
        assert " Name  " in header.plain
        assert " Age " in header.plain

    def test_table_with_emoji_alignment(self):
        """含 emoji 的表格对齐：📖(宽2) 与 ✔(窄1) 宽度正确，边框与单元格一致。"""
        from src.tui._screen import wcswidth_simple
        assert wcswidth_simple("📖") == 2
        assert wcswidth_simple("✔") == 1
        class _Tok:
            meta = {
                "rows": [["工具", "状态"], ["📖 read", "✔ 完成"]],
                "alignments": ["left", "left"],
            }
        lines = render_table(_Tok())
        border = lines[0]
        rows = lines[1:]
        assert all(line.width == border.width for line in rows)
