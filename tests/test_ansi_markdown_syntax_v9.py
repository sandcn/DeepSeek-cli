"""ANSI 流式 Markdown 语法/渲染增强（第九批）测试。

覆盖本轮改动：

  - **新增语法特性**：``\\begin{cases}`` / ``\\begin{matrix}`` / ``\\begin{split}``
    等 KaTeX 显示环境（此前仅部分环境识别，其余落到段落，环境语法失效）；
    setext underline 支持 1~2 个字符（``Foo\\n=`` / ``Foo\\n--`` 是标题）；
    任务列表取消态 ``[~]``（与 ``[-]`` 等同）；列表项内**首行**块级容器
    （``- ``` `` / ``- | a | b |`` / ``- > x``）；尖括号自动链接的**任意 scheme**
    （``<irc://…>`` / ``<a+b+c:d>``）；完整 HTML5 命名实体表（``&AElig;`` /
    ``&Dcaron;`` …）；空链接目标（``[t]()`` / ``[t](<>)``）；链接目标内的
    反斜杠转义（``[a](b\\)c)``）与跨行目标（``[t](   /uri\\n  "title")``）；
    参考式链接标签的**大小写不敏感**与转义归一（``[Foo][]`` 命中 ``[foo]:``）。

  - **渲染错误修复**：强调定界符条件（``a * foo bar*`` / ``**foo bar **`` /
    ``a_"foo"_`` 等「内侧空白」组合不再剥离标记）；强调 run 分配
    （``**a*b***`` → ``ab``、``***foo** bar*`` → ``foo bar``）；词内下划线
    （``_a_b_`` 不再丢字符）；列表项内缩进代码块的缩进剥离（多留 2 空格）；
    列表项标记后的多余空白（``-   wide`` → ``wide``）；列表项内代码块的结构
    错乱（多出闭合围栏）；``&#0;`` 不再产出 NUL 字符。

  - **性能**：超长代码块活动行的**帧成本节流**（活动行超过窗口上限时按增长
    步长刷新，短行仍逐帧实时）；围栏 / 块定界行判定的探测长度上限（超长活动
    行免每帧 ``strip()`` 扫描）；``_is_punct_char`` 的字母数字快速路径
    （CJK 文本不再逐字符走 ``unicodedata``）；延迟围栏预览携带行列表
    （渲染层按行增量高亮，免每帧整段 split + 重高亮）。
"""

from __future__ import annotations

import io
import re
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


def _tokens(src: str):
    from src.renderer.recursive_parser import RecursiveDescentParser
    from src.renderer.types import RenderContext

    p = RecursiveDescentParser(ctx=RenderContext())
    return [(t.type.name, t.content, dict(t.meta)) for t in (p.feed(src) + p.flush())]


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _rich_plain(src: str) -> list:
    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write(src)
    r.close()
    return [_ANSI.sub("", ln).rstrip() for ln in buf.getvalue().splitlines()]


# ══════════════════════════════════════════════════════════
# 新增语法 1：KaTeX 显示环境（矩阵 / cases / 对齐 / 数组 …）
# ══════════════════════════════════════════════════════════


def test_display_math_env_cases_recognized():
    """``\\begin{cases}…`` 识别为公式块——修复前落到段落（语法完全失效）。"""
    toks = _tokens("\\begin{cases}\na & b\n\\end{cases}\n")
    assert any(t[0] == "MATH_BLOCK_CLOSE" for t in toks), toks
    out = _render("\\begin{cases}\na & b\n\\end{cases}\n")
    assert any("数学公式" in ln for ln in out), out


def test_display_math_env_single_line():
    out = _render("\\begin{cases} a & b \\end{cases}\n")
    assert any("数学公式" in ln for ln in out), out


def test_display_math_env_matrix_render():
    out = _render("\\begin{pmatrix}\n1 & 2 \\\\\n3 & 4\n\\end{pmatrix}\n")
    joined = "\n".join(out)
    assert "1" in joined and "4" in joined, out
    assert "\\begin" not in joined, out


def test_display_math_env_split_and_array():
    for src in ("\\begin{split}\na &= b\n\\end{split}\n",
                "\\begin{array}{cc}\n1 & 2\n\\end{array}\n",
                "\\begin{aligned}\na &= b\n\\end{aligned}\n"):
        toks = _tokens(src)
        assert any(t[0] == "MATH_BLOCK_CLOSE" for t in toks), (src, toks)


def test_display_math_envs_cover_render_known_envs():
    """解析层识别的显示环境集合必须覆盖渲染层支持的全部环境（防漂移）。"""
    from src.renderer._block_parser_state import _DISPLAY_MATH_ENVS
    from src.renderer.ansi._math_env import KNOWN_ENVS

    missing = set(KNOWN_ENVS) - set(_DISPLAY_MATH_ENVS)
    assert not missing, f"渲染层支持但解析层未识别的环境: {sorted(missing)}"


def test_display_math_env_plain_text_not_math():
    """普通 ``\\begin`` 文本（非环境名）不误判为公式块。"""
    toks = _tokens("\\begin{nope}\nx\n\\end{nope}\n")
    assert not any(t[0] == "MATH_BLOCK_CLOSE" for t in toks), toks


# ══════════════════════════════════════════════════════════
# 新增语法 2：setext underline 1~2 个字符
# ══════════════════════════════════════════════════════════


def test_setext_single_equals_is_heading():
    """``Foo\\n=`` 是 h1（CommonMark：underline 为 1 个及以上）。"""
    toks = _tokens("Foo\n=\n")
    assert toks and toks[0][0] == "HEADING" and toks[0][2].get("level") == 1, toks


def test_setext_single_dash_is_h2():
    toks = _tokens("Foo\n-\n")
    assert toks and toks[0][0] == "HEADING" and toks[0][2].get("level") == 2, toks


def test_setext_double_chars():
    for src, level in (("Foo\n==\n", 1), ("Foo\n--\n", 2)):
        toks = _tokens(src)
        assert toks[0][0] == "HEADING" and toks[0][2].get("level") == level, (src, toks)


def test_setext_single_char_rendered_one_line():
    assert _render("Foo\n=\n") == ["Foo"]


def test_bare_dash_pair_is_paragraph_not_hr():
    """无上文段落的 ``--`` 既不是标题也不是分隔线 → 段落。"""
    toks = _tokens("--\n")
    assert toks and toks[0][0] != "HR", toks


def test_bare_triple_dash_still_hr():
    toks = _tokens("---\n")
    assert toks and toks[0][0] == "HR", toks


def test_setext_chunked_consistent():
    for src in ("Foo\n=\n", "Foo\n--\n", "a\nb\n==\n"):
        assert _render_chunked(src, 1) == _render(src), src


# ══════════════════════════════════════════════════════════
# 新增语法 3：列表项内首行块级容器
# ══════════════════════════════════════════════════════════


def test_list_item_code_fence_first_line():
    """``- ``` `` 首行内容属列表项内代码块（修复前多出闭合围栏）。"""
    out = _render("- ```\n  code\n  ```\n")
    assert sum(1 for ln in out if "```" in ln) == 2, out
    assert any("code" in ln for ln in out), out


def test_list_item_code_fence_with_lang():
    out = _render("- ```py\n  x = 1\n  ```\n")
    assert any("py" in ln for ln in out), out


def test_list_item_table_first_line():
    out = _render("- | a | b |\n  |---|---|\n  | 1 | 2 |\n")
    joined = "\n".join(out)
    assert "┌" in joined and "a" in joined and "1" in joined, out


def test_list_item_blockquote_first_line():
    out = _render("- > quote\n")
    assert any("quote" in ln for ln in out), out
    assert not any(ln.strip().startswith("- >") for ln in out), out


def test_list_item_block_plain_text_unchanged():
    assert _render("- plain text\n") == ["• plain text"]


def test_list_item_block_chunked_consistent():
    for src in ("- ```\n  code\n  ```\n",
                "- | a | b |\n  |---|---|\n  | 1 | 2 |\n",
                "- > quote\n"):
        assert _render_chunked(src, 1) == _render(src), src
        assert _render_chunked(src, 3) == _render(src), src


def test_list_item_indented_code_padding():
    """``- a`` 空行后的 6 空格缩进代码：内容是 ``code``（列表内容列 + 4）。"""
    assert _render("- a\n\n      code\n") == ["• a", "", "```", "code", "```"]


def test_list_item_indented_code_deeper_keeps_extra():
    assert _render("- a\n\n        code\n") == ["• a", "", "```", "  code", "```"]


def test_document_indented_code_unchanged():
    assert _render("text\n\n    code\n") == ["text", "", "```", "code", "```"]


def test_list_marker_extra_spaces_trimmed():
    """``-   wide`` 的内容是 ``wide``（1~4 个空白属标记宽度）。"""
    assert _render("-   wide space\n") == ["• wide space"]


def test_list_marker_five_spaces_is_indented_code():
    toks = _tokens("-     code\n")
    assert any(t[0] == "CODE_FENCE_OPEN" for t in toks), toks


def test_strip_marker_spaces_unit():
    from src.renderer._block_parser import _strip_marker_spaces

    assert _strip_marker_spaces("   wide") == "wide"
    assert _strip_marker_spaces(" wide") == "wide"
    assert _strip_marker_spaces("     code") == "    code"
    assert _strip_marker_spaces("\tcode") == "code"


# ══════════════════════════════════════════════════════════
# 新增语法 4：任务列表取消态 [~]
# ══════════════════════════════════════════════════════════


def test_task_cancel_tilde_recognized():
    toks = _tokens("- [~] c\n")
    assert toks[0][0] == "LIST_ITEM" and toks[0][2].get("cancelled") is True, toks
    assert _render("- [~] c\n") == ["• [~] c"]


def test_task_cancel_dash_recognized():
    toks = _tokens("- [-] c\n")
    assert toks[0][2].get("cancelled") is True, toks


def test_task_cancel_requires_space():
    toks = _tokens("- [~]c\n")
    assert toks[0][2].get("cancelled") is False, toks


def test_task_states_render():
    out = _render("- [ ] a\n- [x] b\n- [~] c\n")
    assert out == ["• [ ] a", "• [x] b", "• [~] c"]


def test_is_todo_accepts_tilde():
    from src.renderer._rendering import is_todo

    assert is_todo("[~] c")[0] == "~"
    assert is_todo("[-] c")[0] == "-"
    assert is_todo("[~]c")[0] is None


# ══════════════════════════════════════════════════════════
# 新增语法 5：链接 / 自动链接 / 参考式标签
# ══════════════════════════════════════════════════════════


def test_link_empty_target():
    assert _render("[t]()\n") == ["t"]


def test_link_empty_angle_target():
    assert _render("[t](<>)\n") == ["t"]


def test_link_escaped_closing_paren_in_url():
    out = _render("[a](b\\)c)\n")
    assert out == ["a"], out


def test_link_escaped_parens_balanced():
    out = _render("[link](foo\\(and\\(bar\\))\n")
    assert out and out[0].startswith("link"), out


def test_link_multiline_target_and_title():
    """链接目标与标题可跨软换行（CommonMark）。"""
    out = _render('[link](   /uri\n  "title"  )\n')
    assert out and out[0].startswith("link"), out


def test_ref_label_case_insensitive():
    """``[Foo][]`` 命中 ``[foo]:``（CommonMark：标签大小写不敏感）。"""
    out = _render('[Foo][]\n\n[foo]: /url "T"\n')
    assert any("/url" in ln for ln in out), out


def test_ref_label_case_insensitive_named():
    out = _render("[bar][BaR]\n\n[bar]: /url\n")
    assert any("/url" in ln for ln in out), out


def test_ref_label_escaped_normalized():
    """``[bar][foo\\!]`` 命中 ``[foo!]:``（标签内转义归一）。"""
    out = _render("[bar][foo\\!]\n\n[foo!]: /url\n")
    assert any("/url" in ln for ln in out), out


def test_ref_label_unresolved_keeps_original_case():
    """未命中定义时按原始标签回退（归一化只用于查表，不改显示形态）。"""
    out = _render("[Foo] text\n")
    assert out == ["[Foo] text"], out


def test_escape_all_ascii_punctuation_kept():
    """CommonMark：任意 ASCII 标点可反斜杠转义（含 ``(`` / ``)``）。"""
    src = ("\\!\\\"\\#\\$\\%\\&\\'\\(\\)\\*\\+\\,\\-\\.\\/\\:\\;\\<\\=\\>"
           "\\?\\@\\[\\\\\\]\\^\\_\\`\\{\\|\\}\\~")
    expect = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    assert _render(src + "\n") == [expect], _render(src + "\n")


def test_paren_math_still_works():
    assert _render("\\(a^2\\)\n") == ["a\u00b2"]


def test_empty_paren_math_falls_back_to_escape():
    """``\\(\\)`` 是转义括号（不是空公式）——修复前括号字符丢失。"""
    assert _render("\\(\\)\n") == ["()"]


def test_ref_definition_label_with_brackets_is_not_consumed():
    """``[Foo][] 与 [foo]: /url`` 是**正文**（标签含方括号 → 非定义）。

    修复前该行被 ``_try_ref_link`` 整行吞掉并登记一条伪造定义（文末「引用
    链接」附录出现乱码条目、正文丢失）。
    """
    src = "[Foo][] 与 [foo]: /url 大小写不敏感。\n"
    out = _render(src)
    assert out and "大小写不敏感" in out[0], out
    assert not any("🔗" in ln for ln in out), out


def test_ref_valid_label_still_registered():
    out = _render("[a]: http://x\n\nsee [a]\n")
    assert any("http://x" in ln for ln in out), out


def test_ref_label_validation_unit():
    from src.renderer._block_parser import RegexFreeBlockParser

    assert RegexFreeBlockParser._is_valid_ref_label("ok")
    assert RegexFreeBlockParser._is_valid_ref_label("foo bar")
    assert not RegexFreeBlockParser._is_valid_ref_label("a[b")
    assert not RegexFreeBlockParser._is_valid_ref_label("a]b")
    assert not RegexFreeBlockParser._is_valid_ref_label("")
    assert not RegexFreeBlockParser._is_valid_ref_label("x" * 1000)


def test_ref_definition_with_brackets_chunked_consistent():
    src = "[Foo][] 与 [foo]: /url 大小写不敏感。\n"
    assert _render_chunked(src, 1) == _render(src)


def test_normalize_ref_label_unit():
    from src.renderer._inline_links import normalize_ref_label, unescape_label

    assert normalize_ref_label("  Foo  Bar ") == "foo bar"
    assert normalize_ref_label("İ") == normalize_ref_label("İ")
    assert unescape_label("foo\\!") == "foo!"
    assert unescape_label("a\\]b") == "a]b"
    assert unescape_label("plain") == "plain"


def test_autolink_generic_scheme():
    for src, expect in (("<irc://foo.bar:2233/baz>\n", "irc://foo.bar:2233/baz"),
                        ("<a+b+c:d>\n", "a+b+c:d"),
                        ("<made-up-scheme://foo,bar>\n", "made-up-scheme://foo,bar")):
        out = _render(src)
        assert out and expect in out[0], (src, out)


def test_autolink_is_uri_unit():
    from src.renderer._inline_links import _is_uri_autolink

    assert _is_uri_autolink("http://x.com")
    assert _is_uri_autolink("irc://a.b:1/c")
    assert not _is_uri_autolink("a:b")        # scheme 需 2 字符以上
    assert not _is_uri_autolink("http://a b")  # 含空白
    assert not _is_uri_autolink("1abc:x")      # 非字母开头


def test_autolink_email_with_backslash_not_autolink():
    """``<foo\\+@bar.example.com>`` 含反斜杠 → 不是 autolink（CommonMark）。"""
    toks = _tokens("<foo\\+@bar.example.com>\n")
    assert all(t[0] != "PARAGRAPH" or "AutoLink" not in t[0] for t in toks), toks
    from src.renderer._inline_links import _is_uri_autolink

    assert not _is_uri_autolink("foo\\+@bar.example.com")


def test_link_chunked_consistent():
    for src in ("[t]()\n", "[a](b\\)c)\n", '[link](   /uri\n  "title"  )\n'):
        assert _render_chunked(src, 1) == _render(src), src


# ══════════════════════════════════════════════════════════
# 新增语法 6：完整 HTML5 命名实体 + 无效码点
# ══════════════════════════════════════════════════════════


def test_html5_named_entities_full_table():
    from src.renderer._utils import decode_html_entities as d

    # 标准库 HTML5 实体表：含多码点实体（&NotNestedGreaterGreater; → ⪢̸）
    assert d("&AElig; &Dcaron; &Afr;") == "\u00c6 \u010e \U0001d504"
    assert d("&NotNestedGreaterGreater;") == "\u2aa2\u0338"


def test_html5_entity_unknown_kept():
    from src.renderer._utils import decode_html_entities as d

    assert d("&notanentity;") == "&notanentity;"


def test_numeric_entity_invalid_codepoint():
    from src.renderer._utils import decode_html_entities as d

    assert d("&#0;") == "\ufffd"
    assert d("&#xD800;") == "\ufffd"
    assert d("&#1114112;") == "\ufffd"
    assert d("&#35; &#x41;") == "# A"


def test_entity_render_no_nul():
    out = _render("&#0;\n")
    assert "\x00" not in "".join(out), out


# ══════════════════════════════════════════════════════════
# 修复 7：强调定界符条件（不剥离标记）
# ══════════════════════════════════════════════════════════

_NOT_EMPHASIS = [
    "a * foo bar*",
    "_ foo bar_",
    'a_"foo"_',
    "_foo bar _",
    "** foo bar**",
    'a**"foo"**',
    "__ foo bar__",
    "**foo bar **",
    "**(**foo)",
    "2 * 3 * 4",
    "snake_case_name",
    "a_b_c",
]


def test_emphasis_flanking_not_stripped():
    """内侧空白的定界符不构成强调：标记与正文原样保留（不剥字符）。"""
    for text in _NOT_EMPHASIS:
        out = _render(text + "\n")
        assert out == [text], (text, out)


def test_emphasis_flanking_chunked_consistent():
    for text in _NOT_EMPHASIS:
        src = text + "\n"
        assert _render_chunked(src, 1) == _render(src), text


def test_emphasis_still_works_common():
    for src, expect in (("*foo bar*\n", "foo bar"), ("_foo bar_\n", "foo bar"),
                        ("**foo bar**\n", "foo bar"), ("***foo***\n", "foo"),
                        ("foo*bar*\n", "foobar"), ("a**b**c\n", "abc")):
        assert _render(src) == [expect], (src, _render(src))


def test_emph_whitespace_inside_keeps_markers_rich():
    """Rich 路径同步（``_preprocess_text`` 后仍不剥标记）。"""
    out = _rich_plain("a * foo bar*\n")
    assert any("* foo bar*" in ln for ln in out), out


# ══════════════════════════════════════════════════════════
# 修复 8：强调 run 分配（嵌套 / 三连星 / 词内下划线）
# ══════════════════════════════════════════════════════════


def test_emphasis_run_split_strong_em():
    """``**a*b***`` → strong(a + em(b))（修复前残留 ``*ab***``）。"""
    assert _render("**a*b***\n") == ["ab"]


def test_emphasis_em_outer_with_inner_strong():
    """``***foo** bar*`` → em(strong(foo) + " bar")。"""
    assert _render("***foo** bar*\n") == ["foo bar"]


def test_emphasis_intraword_underscore_keeps_char():
    """``_a_b_`` → em("a_b")（词内下划线不参与配对，修复前丢字符 → ``ab_``）。"""
    assert _render("_a_b_\n") == ["a_b"]


def test_emphasis_mixed_nesting():
    for src, expect in (("*foo**bar**baz*\n", "foobarbaz"),
                        ("*foo **bar *baz* bim** bop*\n", "foo bar baz bim bop"),
                        ("*a **b** c*\n", "a b c"),
                        ("**foo [*bar*](/url)**\n", "foo bar")):
        assert _render(src) == [expect], (src, _render(src))


def test_emphasis_dunder_protection_kept():
    assert _render("__init__\n") == ["__init__"]
    assert _render("a__b__c\n") == ["a__b__c"]


def test_emphasis_run_split_chunked_consistent():
    for src in ("**a*b***\n", "***foo** bar*\n", "_a_b_\n"):
        assert _render_chunked(src, 1) == _render(src), src


def test_emphasis_run_split_rich_parity():
    """Rich 路径同源解析（内联解析器共享）→ 文本一致。"""
    for src, expect in (("**a*b***\n", "ab"), ("***foo** bar*\n", "foo bar")):
        joined = "".join(_rich_plain(src))
        assert joined == expect, (src, joined)


def test_is_punct_char_unit():
    from src.renderer._inline_formatting import _is_punct_char

    assert _is_punct_char("!")
    assert _is_punct_char("—")
    assert not _is_punct_char("a")
    assert not _is_punct_char("中")
    assert not _is_punct_char("")
    assert not _is_punct_char("1")


# ══════════════════════════════════════════════════════════
# 流式一致性（本轮改动矩阵）
# ══════════════════════════════════════════════════════════

_STREAM_CASES = [
    "\\begin{cases}\na & b\n\\end{cases}\n",
    "Foo\n=\n",
    "- ```\n  code\n  ```\n",
    "- | a | b |\n  |---|---|\n  | 1 | 2 |\n",
    "- [~] task\n",
    "- a\n\n      code\n",
    "&AElig; &#0;\n",
    "a * foo bar*\n",
    "**a*b***\n",
]


def test_ref_label_forward_definition_stream_placeholder():
    """参考式链接的**前向定义**在流式期间显示占位、定义出现后展开。

    ``AnsiStreamRenderer`` 逐字符写入时，``[Foo][]`` 早于定义行到达——此时
    输出占位（``[Foo]`` / ``[?Foo]``），定义到达后（整块重渲染，等价于一次性
    渲染）展开为链接。TUI 层由 ``_FORWARD_REF_RE`` 命中后整块重渲染修正。
    """
    src = "[Foo][]\n\n[foo]: /url\n"
    one_shot = _render(src)
    assert one_shot[0].startswith("Foo") and "/url" in one_shot[0], one_shot
    chunked = _render_chunked(src, 1)
    assert chunked[0] == "[Foo]", chunked  # 占位（预览中间态）
    from src.tui.app.model import _FORWARD_REF_RE

    assert _FORWARD_REF_RE.search(src)  # 命中 → TUI 关闭时整块重渲染


def test_stream_consistency_matrix():
    for src in _STREAM_CASES:
        base = _render(src)
        for size in (1, 2, 3, 5):
            assert _render_chunked(src, size) == base, (src, size)


# ══════════════════════════════════════════════════════════
# 两路径同步（Rich ↔ TUI）
# ══════════════════════════════════════════════════════════


def test_rich_path_display_math_env():
    out = _rich_plain("\\begin{cases}\na & b\n\\end{cases}\n")
    assert any("\\begin" not in ln for ln in out), out
    assert any("a" in ln for ln in out), out


def test_rich_path_entities():
    joined = "".join(_rich_plain("&AElig; &Dcaron;\n"))
    assert "\u00c6" in joined and "\u010e" in joined, joined


def test_rich_path_list_item_code():
    out = _rich_plain("- ```\n  code\n  ```\n")
    assert any("code" in ln for ln in out), out


def test_rich_path_emphasis_flanking():
    out = _rich_plain("a * foo bar*\n")
    assert any("* foo bar*" in ln for ln in out), out


# ══════════════════════════════════════════════════════════
# 性能：超长活动行节流 + 快速路径
# ══════════════════════════════════════════════════════════


def test_long_code_active_line_preview_budget():
    """20 万字符单行代码流式预览（chunk=24）成本受控。"""

    def _run() -> float:
        r = AnsiStreamRenderer(width=100)
        r.write("```json\n")
        t0 = time.perf_counter()
        for i in range(0, 200000, 24):
            r.write("x" * 24)
        r.take_preview_lines()
        return time.perf_counter() - t0

    assert _run() < 2.0


def test_short_code_active_line_still_realtime():
    """短活动行仍逐帧刷新（节流只作用于超长行）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```py\n")
    r.write("value = 1")
    assert any("value = 1" in ln.plain for ln in r.take_preview_lines())
    r.write("2")
    assert any("value = 12" in ln.plain for ln in r.take_preview_lines())


def test_long_code_active_line_reuses_incremental_cache():
    """超长单行活动行不因自身内容变化而重置增量缓存。

    修复前缓存键含**首行完整文本**（单行内容时首行即活动行）→ 每帧 key
    变化 → 行缓存与增量高亮全部失效。现单行只取前 64 字符作锚点。
    """
    r = AnsiStreamRenderer(width=80)
    r.write("```\n")
    r.write("x" * 2000)
    r.take_preview_lines()
    key = r._code_preview_key
    assert key is not None
    r.write("y" * 100)
    r.take_preview_lines()
    assert r._code_preview_key == key, "活动行增长不应重置预览缓存键"


def test_long_code_active_line_window_keeps_latest():
    """窗口化后活动行最新内容仍可见（流式实时性契约）。"""
    r = AnsiStreamRenderer(width=100)
    r.write("```\n")
    r.write("x" * 8000)
    r.write("TAILMARKER")
    joined = "".join(ln.plain for ln in r.take_preview_lines())
    assert "TAILMARKER" in joined


def test_long_code_active_line_throttled_above_threshold():
    """超过 32KB 的活动行按增长步长刷新（小增量不重渲，帧成本封顶）。"""
    from src.renderer.ansi import _CODE_ACTIVE_THROTTLE_MIN

    r = AnsiStreamRenderer(width=80)
    r.write("```\n")
    r.write("x" * (_CODE_ACTIVE_THROTTLE_MIN + 1000))
    r.take_preview_lines()
    before = r._code_active_len
    assert before > _CODE_ACTIVE_THROTTLE_MIN
    r.write("y")  # 增量 1 < 步长（上限 256）→ 沿用上一帧
    r.take_preview_lines()
    assert r._code_active_len == before


def test_medium_length_active_line_not_throttled():
    """中等长度活动行（数 KB）逐帧刷新（不被节流）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```\n")
    r.write("x" * 5000)
    r.take_preview_lines()
    r.write("ZLATEST")
    joined = "".join(ln.plain for ln in r.take_preview_lines())
    assert "ZLATEST" in joined


def test_ttype_style_cache_used():
    """token 类型 → 样式按 (主题, 类型) 缓存（热路径命中免重复解析）。"""
    from src.renderer.ansi import code as _code
    from src.renderer._rendering._code import get_lexer
    from src.renderer._utils import get_code_style

    _code._TTYPE_STYLE_CACHE.clear()
    lexer = get_lexer("python")
    style = get_code_style("monokai")
    _code._highlight_line("def f(x):", lexer, style, "monokai")
    assert _code._TTYPE_STYLE_CACHE, "token 类型样式缓存未生效"
    hits = len(_code._TTYPE_STYLE_CACHE)
    _code._highlight_line("def g(y):", lexer, style, "monokai")
    assert len(_code._TTYPE_STYLE_CACHE) == hits, "重复 token 类型不应新增缓存条目"


def test_format_heavy_paragraph_stream_budget():
    sent = "**bold** _italic_ `code` [link](http://x) ~~del~~ ==hi== :smile: "
    text = sent * 500
    t0 = time.perf_counter()
    r = AnsiStreamRenderer(width=100)
    for i in range(0, len(text), 24):
        r.write(text[i:i + 24])
    r.close()
    elapsed = time.perf_counter() - t0
    assert elapsed < 4.0, f"格式密集段落流式耗时 {elapsed:.3f}s"


def test_fence_probe_length_guard():
    """超长活动行不做围栏判定（即时返回，不扫描整行）。"""
    from src.renderer._block_parser import _FENCE_LINE_MAX_PROBE

    assert _FENCE_LINE_MAX_PROBE > 0
    r = AnsiStreamRenderer(width=80)
    r.write("```\n")
    t0 = time.perf_counter()
    for _ in range(50):
        r.write("z" * 5000)
        r.take_preview_lines()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.5, f"单行追加耗时 {elapsed:.3f}s"
