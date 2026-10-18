"""流式 Markdown 渲染优化（第十二批）的正确性 / 等价性回归。

本批两项优化（性能预算用例见 ``test_stream_markdown_perf_v12.py``）：

  1. ``_inline_links._scan_url_body`` —— 裸 URL 主体扫描由逐字符 Python 循环
     改为一次 C 级正则定位；与旧循环在随机文本上**逐位置**等价，解析树
     （链接 URL / 文本切分）与旧实现完全一致；
  2. ``AnsiStreamRenderer._throttle_paragraph_whole`` —— 跨软换行未闭合标记
     导致的「整段解析」预览按源码增长节流（消除长段落每帧重解析的 O(n²)）：
     同段落纯追加时按步长复用上一帧结果；段落切换 / 预览滑窗 / 内容回退一律
     重渲染，流式提交结果与一次性渲染一致。

本文件不含时间断言（--cov 插桩下仍会执行，保证新增代码被覆盖）。
"""

from __future__ import annotations

import random

import src.renderer._inline_links as _links
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi import blocks as _blocks
from src.renderer.ansi.helpers import AnsiLine
from src.renderer.inline_parser import _InlineParser


# ═══════════════════════════════════════════════════════════
# 1. 裸 URL 主体扫描：与旧逐字符循环等价
# ═══════════════════════════════════════════════════════════

#: 旧实现的终止字符集（含半角逗号；半角 ``. ! ? ; :`` 仅在后跟空白时终止）
_OLD_STOP = set('<>"\'[]{},，。、！？；：】》》）—–—')


def _old_scan_url_body(text: str, pos: int, n: int) -> int:
    """旧实现（逐字符扫描）——作为等价性参考。"""
    while (pos < n
           and not text[pos].isspace()
           and text[pos] not in _OLD_STOP):
        if text[pos] in ',.!?;:' and pos + 1 < n and text[pos + 1].isspace():
            break
        pos += 1
    return pos


_URL_ALPHABET = list(
    "abcXYZ0123456789/._-~%&=+?#@!$'()[]{}<>\"`*|^:;, "
    "，。、！？；：】》》）—–\n\t中文字符"
)


def test_scan_url_body_matches_char_loop() -> None:
    """随机文本上，任一位置的扫描结果与旧逐字符循环一致。"""
    rnd = random.Random(20261018)
    for _ in range(3000):
        text = "".join(
            rnd.choice(_URL_ALPHABET) for _ in range(rnd.randrange(0, 60)))
        n = len(text)
        for pos in range(n):
            expected = _old_scan_url_body(text, pos, n)
            assert _links._scan_url_body(text, pos, n) == expected, (
                repr(text), pos)


def test_scan_url_body_edge_cases() -> None:
    """半角逗号恒终止；``. ! ? ; :`` 仅后跟空白时终止；全角标点终止。"""
    scan = _links._scan_url_body
    # 半角逗号：无论后跟什么字符都终止
    assert scan("a,b", 0, 3) == 1
    assert scan("a, b", 0, 4) == 1
    # 半角句点：后跟字母属于主体，后跟空白终止，文本末尾保留（交 balance 剥离）
    assert scan("a.b", 0, 3) == 3
    assert scan("a. b", 0, 4) == 1
    assert scan("a.", 0, 2) == 2
    # 冒号 / 分号 / 问号 / 感叹号
    assert scan("a:b", 0, 3) == 3
    assert scan("a: b", 0, 4) == 1
    assert scan("a;b", 0, 3) == 3
    # 全角标点 / HTML 定界符 / 换行
    assert scan("a，b", 0, 3) == 1
    assert scan("a>b", 0, 3) == 1
    assert scan("a\nb", 0, 3) == 1
    # 空主体
    assert scan("", 0, 0) == 0
    # ``!`` 后跟非空白 → 属于主体
    assert scan("!x", 0, 2) == 2


def _ser(nodes) -> list:
    """解析树序列化（类名 / 内容 / URL / 子节点）——比较新旧实现产出的结构。"""
    out = []
    for nd in nodes:
        out.append((
            type(nd).__name__,
            nd.content,
            getattr(nd, "url", None),
            _ser(nd.children) if nd.children else None,
        ))
    return out


_PREFIXES = (
    "http://", "https://", "ftp://", "ftps://", "www.",
    "HTTP://", "Https://", "FTP://", "WWW.", "www", "http", "ht",
)


def test_bare_url_parse_tree_matches_old_scanner(monkeypatch) -> None:
    """解析树级别等价：随机 URL 文本在新旧扫描实现下产出一致。"""
    rnd = random.Random(42)
    cases = []
    for _ in range(300):
        parts = []
        for _ in range(rnd.randrange(1, 6)):
            if rnd.random() < 0.5:
                parts.append(rnd.choice(_PREFIXES))
                parts.append("".join(
                    rnd.choice(_URL_ALPHABET)
                    for _ in range(rnd.randrange(0, 15))))
            else:
                parts.append("".join(
                    rnd.choice(_URL_ALPHABET)
                    for _ in range(rnd.randrange(0, 6))))
        cases.append("".join(parts))

    new = [_ser(_InlineParser(t).parse()) for t in cases]
    monkeypatch.setattr(_links, "_scan_url_body", _old_scan_url_body)
    old = [_ser(_InlineParser(t).parse()) for t in cases]
    for text, a, b in zip(cases, new, old):
        assert a == b, repr(text)


def test_bare_url_trailing_punctuation_variants() -> None:
    """常见 URL 结尾形态（标点 / 括号 / 强调定界符）解析结果稳定。"""
    from src.renderer.ansi import render_inline

    def urls(md: str) -> list[str]:
        found: list[str] = []

        def walk(nodes) -> None:
            for nd in nodes:
                if type(nd).__name__ == "AutoLinkNode":
                    found.append(nd.url)
                if nd.children:
                    walk(nd.children)

        walk(_InlineParser(md).parse())
        return found

    assert urls("见 https://example.com/a.") == ["https://example.com/a"]
    assert urls("见 https://example.com/a, 后文") == ["https://example.com/a"]
    assert urls("见 https://example.com/a?b=1 后文") == [
        "https://example.com/a?b=1"]
    assert urls("(https://example.com/a)") == ["https://example.com/a"]
    assert urls("**https://example.com/a**") == ["https://example.com/a"]
    # 全角标点终止
    assert urls("见 https://example.com/a。后文") == ["https://example.com/a"]
    # 渲染文本不吞掉后续内容
    rows = render_inline("见 https://example.com/a. 后文")
    assert "后文" in "".join(r.text for r in rows)


# ═══════════════════════════════════════════════════════════
# 2. 整段解析节流：行为与正确性
# ═══════════════════════════════════════════════════════════

def _stream_lines(md: str, chunk: int, width: int = 90) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(md), chunk):
        r.write(md[i:i + chunk])
        r.take_preview_lines()
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _batch_lines(md: str, width: int = 90) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def test_whole_delta_forbidden_matches_core_format_chars() -> None:
    """节流禁用字符集与行内解析核心触发字符（去 ``:``）保持同步（防漂移）。"""
    from src.renderer.ansi import _WHOLE_DELTA_FORBIDDEN
    from src.renderer.inline_parser import _CORE_FORMAT_CHARS

    assert _WHOLE_DELTA_FORBIDDEN == frozenset(_CORE_FORMAT_CHARS) - {":"}


def test_whole_paragraph_throttle_reuses_until_growth_step() -> None:
    """同段落纯追加时按增长步长复用；回退 / 分歧一律重渲染。"""
    r = AnsiStreamRenderer(width=80)
    calls = {"n": 0}

    def render() -> list[AnsiLine]:
        calls["n"] += 1
        return [AnsiLine.of("x")]

    key = ("test-whole",)
    # 超过 ``_PARAGRAPH_WHOLE_MIN_CHARS`` 才会启用节流（短段落逐帧实时）
    base = ["x" * 100 for _ in range(11)]
    first = r._throttle_paragraph_whole(key, base, render)
    assert calls["n"] == 1
    # 活动行追加 1 字符（增长量 < 步长 64）→ 复用
    extended = base[:-1] + [base[-1] + "y"]
    same = r._throttle_paragraph_whole(key, extended, render)
    assert same is first
    assert calls["n"] == 1
    # 增长达到步长 → 重渲染
    big = extended + ["line-%d" % i for i in range(40)]
    r._throttle_paragraph_whole(key, big, render)
    assert calls["n"] == 2
    # 内容回退（滑窗 / 段落切换）→ 重渲染
    r._throttle_paragraph_whole(key, base[:-1], render)
    assert calls["n"] == 3
    # 首行不同（滑窗形态）→ 重渲染
    r._throttle_paragraph_whole(key, ["z" * 100] + base[1:-1], render)
    assert calls["n"] == 4


def test_whole_paragraph_short_content_not_throttled() -> None:
    """短段落（< ``_PARAGRAPH_WHOLE_MIN_CHARS``）逐帧实时（预览与提交一致）。"""
    r = AnsiStreamRenderer(width=80)
    calls = {"n": 0}

    def render() -> list[AnsiLine]:
        calls["n"] += 1
        return [AnsiLine.of("x")]

    key = ("test-short",)
    r._throttle_paragraph_whole(key, ["a"], render)
    r._throttle_paragraph_whole(key, ["a", "b"], render)
    r._throttle_paragraph_whole(key, ["a", "b", "c"], render)
    assert calls["n"] == 3


def test_whole_paragraph_throttle_rejects_cross_line_triggers() -> None:
    """新增文本含跨行触发字符（``*`` / 反引号等）时立即重渲染。"""
    r = AnsiStreamRenderer(width=80)
    calls = {"n": 0}

    def render() -> list[AnsiLine]:
        calls["n"] += 1
        return [AnsiLine.of("x")]

    key = ("test-trigger",)
    base = ["x" * 100 for _ in range(11)]
    r._throttle_paragraph_whole(key, base, render)
    assert calls["n"] == 1
    # ``*`` 可能闭合跨行粗体 → 重渲染
    r._throttle_paragraph_whole(key, base[:-1] + [base[-1] + "*"], render)
    assert calls["n"] == 2
    # 纯文本追加 → 复用
    plain = base[:-1] + [base[-1] + "*" + "y"]
    r._throttle_paragraph_whole(key, plain[:-1] + [plain[-1] + "z"], render)
    assert calls["n"] == 2


def test_whole_paragraph_throttle_reduces_parse_calls(monkeypatch) -> None:
    """跨行未闭合标记的长段落：整段解析次数远少于帧数（节流生效）。"""
    calls = {"n": 0}
    orig = _blocks.render_paragraph

    def spy(token):
        calls["n"] += 1
        return orig(token)

    monkeypatch.setattr(_blocks, "render_paragraph", spy)
    md = "**跨行粗体开始\n" + "\n".join(
        f"第{i}行内容 https://example.com/{i}" for i in range(120)
    ) + "\n结束**\n\n"
    frames = 0
    r = AnsiStreamRenderer(width=90)
    for i in range(0, len(md), 8):
        r.write(md[i:i + 8])
        r.take_preview_lines()
        frames += 1
    r.close()
    assert frames > 100
    assert calls["n"] < frames // 4, (calls["n"], frames)


def test_whole_paragraph_stream_commit_matches_batch() -> None:
    """节流只影响中间预览：最终提交内容与一次性渲染完全一致。"""
    md = "**跨行粗体开始\n" + "\n".join(
        f"第{i}行内容，含 *斜体* 与 https://example.com/{i}" for i in range(120)
    ) + "\n结束**\n\n"
    assert _stream_lines(md, 7) == _batch_lines(md)
    assert _stream_lines(md, 64) == _batch_lines(md)


def test_whole_paragraph_preview_reflects_new_paragraph() -> None:
    """段落切换后预览不复用上一段落的渲染行。"""
    r = AnsiStreamRenderer(width=80)
    r.write("**第一段未闭合\n第一段内容 A\n")
    first = "".join(ln.plain for ln in r.take_preview_lines())
    assert "第一段未闭合" in first and "第一段内容 A" in first
    # 闭合第一段（** 收尾），随后开始结构相似的第二段
    r.write("第一段结束**\n\n")
    r.take_lines()
    r.write("**第二段未闭合\n第二段内容 B\n")
    second = "".join(ln.plain for ln in r.take_preview_lines())
    assert "第二段未闭合" in second and "第二段内容 B" in second
    assert "第一段" not in second


def test_whole_paragraph_sliding_window_preview_correct() -> None:
    """长段落预览滑窗（超过解析器预览行数上限）时整段渲染仍正确。"""
    md = "**跨行粗体\n" + "\n".join(
        f"第{i}行普通内容" for i in range(260)
    ) + "\n结束**\n\n"
    r = AnsiStreamRenderer(width=80)
    with_preview = 0
    frames = 0
    for i in range(0, len(md), 11):
        r.write(md[i:i + 11])
        preview = [ln.plain for ln in r.take_preview_lines()]
        frames += 1
        if preview:
            with_preview += 1
    r.close()
    # 段落未闭合期间应有预览（结尾 ``\n\n`` 闭合后 pending 为空除外）
    assert with_preview > frames * 0.9, (with_preview, frames)
    assert [ln.plain for ln in r.take_lines()] == _batch_lines(md)
