"""ANSI 流式 Markdown 扩展语法测试（需求：流式 markdown 支持更多语法）。

覆盖 TUI 流式路径（``AnsiStreamRenderer`` / ``ansi.inline.render_inline``）
新增的内联语法与文末附录：

  - 高亮 ``==x==`` / 上标 ``^x^`` / 下标 ``~x~`` / 下划线 ``++x++``
  - 剧透 ``||x||`` / 行内数学 ``$x$`` / ``\\(x\\)``
  - 脚注 ``[^id]``（引用编号 + 文末脚注列表）
  - 图片 ``![alt](url)`` / Emoji ``:name:`` / 反斜杠转义
  - 参考式链接 ``[t][ref]``（含文末引用链接列表）
  - ```<br>``` 硬换行 / 维基链接 ``[[page]]``
  - CriticMarkup ``{-- --}{++ ++}{~~ ~> ~~}{>> <<}``
  - 小字 ``{-x-}`` / 着色 ``{color:red}x{color}`` / 行内注释 ``%%x%%``
  - 自动邮箱 ``<a@b.com>`` / HTML 实体 ``&amp;`` / 缩写定义替换
  - 传统语法（粗体/斜体/行内码/删除线/链接）样式回归
  - 任务列表 / 定义列表 ANSI 回归
  - 纯文本快路径（单 Run）与无上下文安全降级
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


def _plain(runs) -> str:
    return "".join(r.text for r in runs)


def _render_doc(text: str, width: int = 60):
    r = AnsiStreamRenderer(width=width)
    r.write(text)
    r.close()
    return r.take_lines()


def _style_of(runs, text: str):
    for run in runs:
        if run.text == text:
            return run.style
    raise AssertionError(f"未找到 run: {text!r} in {_plain(runs)!r}")


# ── 内联格式 ──────────────────────────────────────────────


def test_highlight():
    runs = render_inline("==hi==")
    assert _plain(runs) == "hi"
    assert runs[0].style.bg == 11
    assert runs[0].style.bold


def test_subscript_and_superscript_unicode():
    assert _plain(render_inline("H~2~O")) == "H\u2082O"
    assert _plain(render_inline("x^2^")) == "x\u00b2"


def test_underline():
    runs = render_inline("++u++")
    assert _plain(runs) == "u"
    assert runs[0].style.underline


def test_spoiler_masked():
    runs = render_inline("||secret||")
    plain = _plain(runs)
    assert "secret" not in plain
    assert plain == "\u2588" * len("secret")


def test_inline_math():
    for src in ("$x^2$", r"\(y\)"):
        runs = render_inline(src)
        plain = _plain(runs)
        assert "$" not in plain and "\\(" not in plain
        assert runs[0].style.italic


def test_image_placeholder():
    plain = _plain(render_inline("![alt](pic.png)"))
    assert "alt" in plain and "pic.png" in plain


def test_emoji_shortcode():
    assert "\U0001f60a" in _plain(render_inline(":smile:"))


def test_backslash_escape():
    runs = render_inline(r"\*not italic\*")
    assert _plain(runs) == "*not italic*"
    assert not any(r.style and r.style.italic for r in runs)


def test_wikilink():
    assert _plain(render_inline("[[page]]")) == "page"
    assert _plain(render_inline("[[page|显示]]")) == "显示"


def test_critic_markup():
    assert _plain(render_inline("{--gone--}")) == "gone"
    assert _plain(render_inline("{++added++}")) == "added"
    assert _plain(render_inline("{~~old~>new~~}")) == "old → new"
    assert "note" in _plain(render_inline("{>>note<<}"))


def test_small_color_inline_comment():
    assert _plain(render_inline("{-small-}")) == "small"
    runs = render_inline("{color:red}x{color}")
    assert _plain(runs) == "x"
    assert runs[0].style.fg == 196
    assert _plain(render_inline("%%hidden%%")) == "hidden"


def test_autolink_email_and_entity():
    runs = render_inline("<a@b.com>")
    assert _plain(runs) == "a@b.com"
    assert runs[0].style.underline
    assert _plain(render_inline("&amp; &#65;")) == "& A"


# ── 块级 / 渲染器集成 ─────────────────────────────────────


def test_br_hard_break():
    plains = [ln.plain for ln in _render_doc("a<br>b")]
    assert plains == ["a", "b"]


def test_footnote_endnote():
    joined = "\n".join(
        ln.plain for ln in _render_doc("see[^1]\n\n[^1]: body text\n")
    )
    assert "see[1]" in joined
    assert "body text" in joined
    assert "\u21a9" in joined


def test_reference_link_endnote():
    joined = "\n".join(
        ln.plain for ln in _render_doc("ref[t][r1]\n\n[r1]: http://e.com \"T\"\n")
    )
    assert "http://e.com" in joined
    assert "[r1]" in joined
    assert "T" in joined


def test_abbreviation_replacement():
    lines = _render_doc("*[HTML]: HyperText Markup Language\n\nThe HTML lang\n")
    runs = [run for ln in lines for run in ln.runs]
    assert any(
        r.text == "HTML" and r.style is not None and r.style.underline
        for r in runs
    )


def test_traditional_styles_preserved():
    runs = render_inline("**b** *i* `c` ~~s~~ [t](http://u)")
    assert _style_of(runs, "b").bold
    assert _style_of(runs, "i").italic
    assert _style_of(runs, "c").fg == 46
    assert _style_of(runs, "s").dim
    assert _style_of(runs, "t").underline


def test_task_list_and_definition_list():
    plains = [ln.plain for ln in _render_doc(
        "- [x] done\n- [ ] todo\n\nTerm\n: definition\n"
    )]
    assert any("[x] done" in p for p in plains)
    assert any("[ ] todo" in p for p in plains)
    assert any(p.startswith("Term: definition") for p in plains)


def test_plain_text_fast_path_single_run():
    runs = render_inline("plain text without any markers, \u4e2d\u6587\u5185\u5bb9\u3002")
    assert len(runs) == 1


def test_no_context_is_safe():
    assert _plain(render_inline("x[^1]")) == "x[^1]"
