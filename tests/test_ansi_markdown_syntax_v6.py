"""ANSI 流式 Markdown 语法/渲染增强（第六批）测试。

覆盖本轮改动：

  - 反斜杠转义补全：CommonMark「任意 ASCII 标点」可转义（修复前
    ``" % & ' , / ; ? @`` 前的反斜杠原样泄漏）
  - 行内代码反引号串：任意长度 k 的反引号定界（修复前仅 1/2）
  - 链接/图片标题转义：``[t](url "a \\"b\\" c")``
  - 参考式链接定义的多行形态：标题在下一行、URL 在下一行
  - ``[id]:`` 无目标时按段落输出（不静默丢弃）
  - Setext：无上文的 ``=====`` 不再误判为分隔线
  - 性能：超长单行代码预览的帧累计成本（活动行节流 + 同样式 token 合并）
"""

from __future__ import annotations

import io
import string
import time

from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_chunked(src: str, size: int = 2, width: int = 72):
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
# 反斜杠转义（任意 ASCII 标点）
# ══════════════════════════════════════════════════════════


def test_escape_all_ascii_punctuation():
    """每个 ASCII 标点前的反斜杠都应还原为字面标点。"""
    for ch in string.punctuation:
        got = _render("a\\" + ch + "b\n")
        assert got == ["a" + ch + "b"], (ch, got)


def test_escape_missing_set_before_fix():
    """回归锁定修复前会泄漏反斜杠的字符。"""
    for ch in "\"%&',/;?@":
        assert _render("x\\" + ch + "y\n") == ["x" + ch + "y"]


def test_escape_non_punctuation_keeps_backslash():
    """非标点字符前的反斜杠保持字面（CommonMark：仅 ASCII 标点可转义）。"""
    assert _render("a\\nb\n")[0].endswith("\\nb") or _render("a\\zb\n") == ["a\\zb"]


def test_escape_backslash():
    assert _render("a\\\\b\n") == ["a\\b"]


def test_escape_chunked_consistent():
    src = "\\*not em\\* \\# \\% \\@ \\` end\n"
    assert _render_chunked(src) == _render(src)


def test_rich_escape_parity():
    out = _rich("a\\%b \\@c\n")
    assert "a%b @c" in out


# ══════════════════════════════════════════════════════════
# 行内代码反引号串
# ══════════════════════════════════════════════════════════


def test_inline_code_double_backtick():
    assert _render("a ``code ` here`` b\n") == ["a code ` here b"]


def test_inline_code_triple_backtick():
    assert _render("a ```x `` y``` b\n") == ["a x `` y b"]


def test_inline_code_backtick_inside():
    assert _render("`` ` ``\n") == [" ` "]


def test_inline_code_unclosed_literal():
    assert _render("a `code b\n")[0].endswith("a `code b")


# ══════════════════════════════════════════════════════════
# 链接 / 图片标题转义
# ══════════════════════════════════════════════════════════


def test_link_title_escaped_quotes():
    got = _render('[t](url "a \\"b\\" c")\n')
    assert got == ['t "a "b" c"']


def test_image_title_escaped_quote():
    got = _render('![alt](img.png "x \\"y\\"")\n')
    assert got == ['🖼️ alt (img.png) "x "y""']


def test_link_title_single_quoted_escape():
    got = _render("[t](url 'a \\'b\\'')\n")
    assert got == ["t \"a 'b'\""]


def test_rich_link_title_escape_parity():
    out = _rich('[t](url "a \\"b\\" c")\n')
    assert "t" in out and "a \"b\" c" in out


# ══════════════════════════════════════════════════════════
# 参考式链接定义的多行形态
# ══════════════════════════════════════════════════════════


def test_ref_def_title_next_line():
    lines = _render('[r]: http://a.com\n  "标题"\n\nsee [r]\n')
    assert lines[0] == 'see r ① (http://a.com) "标题"'
    assert any("[r] http://a.com" in ln for ln in lines)
    # 标题行不得作为正文残留
    assert not any(ln.strip() == '"标题"' for ln in lines)


def test_ref_def_url_next_line():
    lines = _render("[r]:\n  http://a.com\n\nsee [r]\n")
    assert lines[0] == "see r ① (http://a.com)"
    assert not any(ln.strip() == "http://a.com" for ln in lines)


def test_ref_def_url_and_title_next_line():
    lines = _render('[r]:\n  http://a.com "T"\n\nsee [r]\n')
    assert lines[0] == 'see r ① (http://a.com) "T"'


def test_ref_def_multiline_chunked_consistent():
    src = '[r]: http://a.com\n  "标题"\n\nsee [r]\n'
    assert _render_chunked(src) == _render(src)


def test_ref_def_empty_target_is_paragraph():
    """``[r]:`` 无目标且无后继 URL → 按段落输出（不静默丢弃）。"""
    lines = _render("[r]:\n\ntext\n")
    assert "[r]:" in lines
    assert "text" in lines


def test_ref_def_empty_target_eof():
    assert "[r]:" in _render("[r]:\n")


# ══════════════════════════════════════════════════════════
# Setext / 分隔线
# ══════════════════════════════════════════════════════════


def test_bare_equals_is_paragraph_not_hr():
    """``=====`` 不是分隔线字符 → 无上文时按段落（修复前凭空生成分隔线）。"""
    lines = _render("=====\n")
    assert lines == ["====="]


def test_equals_after_blank_is_paragraph():
    lines = _render("text\n\n=====\n")
    assert "text" in lines
    assert "=====" in lines
    assert not any(set(ln) == {"\u2500"} for ln in lines)


def test_setext_single_line_is_heading():
    assert _render("标题\n=====\n") == ["标题"]


def test_setext_multiline_still_hr():
    """多行上文 + ``===`` 保持既有「分隔线」语义（与预览一致，回归锁定）。"""
    lines = _render("第一行\n第二行\n===\n")
    assert "第一行" in lines and "第二行" in lines
    assert any("\u2500" * 10 in ln for ln in lines)


def test_bare_dashes_is_hr():
    assert any("\u2500" * 10 in ln for ln in _render("---\n"))


# ══════════════════════════════════════════════════════════
# 性能：超长单行代码预览
# ══════════════════════════════════════════════════════════


def test_long_single_line_code_preview_budget():
    """超长单行代码预览的累计成本受控（活动行节流 + 同样式 token 合并）。

    修复前 20 万字符单行代码流式约 35s；现应 < 1.5s。
    """

    def _run(chunks: int, chunk_size: int = 200) -> float:
        r = AnsiStreamRenderer(width=120)
        r.write("```json\n")
        t0 = time.perf_counter()
        for _ in range(chunks):
            r.write("x" * chunk_size)
        r.take_preview_lines()
        return time.perf_counter() - t0

    assert _run(400) < 1.5


def test_long_single_line_code_committed_intact():
    """超长单行代码闭合后内容完整（窗口/节流只作用于未闭合预览）。"""
    payload = "a" * 200000
    r = AnsiStreamRenderer(width=120)
    r.write("```text\n")
    r.write(payload)
    r.write("\n```\n")
    r.close()
    joined = "".join(ln.plain for ln in r.take_lines())
    assert joined.count("a") >= len(payload)


def test_short_code_active_line_reflects_latest():
    """短活动行（< 节流起点）逐帧刷新，预览始终反映最新内容。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```py\n")
    r.write("value = 1")
    assert any("value = 1" in ln.plain for ln in r.take_preview_lines())
    r.write("2")
    texts = [ln.plain for ln in r.take_preview_lines()]
    assert any("value = 12" in t for t in texts)


def test_code_highlight_groups_same_style(monkeypatch):
    """同样式连续 token 合并为单个 Run（长字符串按字符出 token 时不退化）。"""
    from src.renderer.ansi import code as _code
    from src.renderer._rendering._code import get_lexer
    from src.renderer._utils import get_code_style

    lexer = get_lexer("json")
    style = get_code_style("monokai")
    line = _code._highlight_line('"' + "x" * 5000 + '"', lexer, style)
    assert len(line.runs) == 1
    assert len(line.runs[0].text) == 5002
