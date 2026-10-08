"""流式 Markdown 分析修复回归测试（一致性 / 容错 / 性能 / 契约）。

覆盖清单：
  - A1 段落 / 引用 / 告示 / 折叠块 / FencedDiv 预览截断省略提示
  - A2 段落预览跨软换行行内标记配对（预览与提交一致）
  - A2 无标记段落保持行级增量渲染（不回退整段解析）
  - B1 <details> / FencedDiv 流式预览正文不丢失（engine 预览路径取 body_lines）
  - C1 围栏自动关闭加固：块内出现普通内容行后放弃自动关闭
  - C1 纯结构块仍触发自动关闭（容错保留）
  - D1 表格预览列宽收缩结果缓存（未变化时不再重算）
  - E1 表格预览缓存键显式区分不同表头的表格
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer._block_parser import RegexFreeBlockParser


OMITTED_FMT = "\u2026 前 {n} 行省略（本块结束后完整显示）"


def _render(chunks, width=80):
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    r.close()
    return [line.plain for line in r.take_lines()]


def _preview(chunks, width=80):
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    return [line.plain for line in r.take_preview_lines()]


# ═══════════════════════════════════════════════════════════
# A1 预览截断省略提示（段落 / 引用 / 告示 / 折叠块）
# ═══════════════════════════════════════════════════════════


def test_paragraph_preview_truncation_notice():
    """超长段落预览保留尾部并在头部给出省略提示（与代码块一致）。"""
    limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
    total = limit + 30
    r = AnsiStreamRenderer(width=80)
    for i in range(total):
        r.write(f"line{i}\n")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev[0] == OMITTED_FMT.format(n=30)
    assert prev[1] == "line30"
    assert len(prev) == limit + 1


def test_paragraph_preview_no_notice_when_short():
    """未超上限的段落预览不出现省略提示。"""
    prev = _preview([f"line{i}\n" for i in range(20)])
    assert all("省略" not in p for p in prev)


def test_blockquote_preview_truncation_notice():
    """超长引用预览同样给出省略提示（保留尾部行）。"""
    limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
    total = limit + 15
    r = AnsiStreamRenderer(width=80)
    for i in range(total):
        r.write(f"> quote{i}\n")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev[0] == OMITTED_FMT.format(n=15)
    assert prev[1] == "\u2502 quote15"


def test_admonition_preview_truncation_notice_keeps_head():
    """超长告示预览：首行（head）保留 + 尾部正文 + 省略提示。"""
    limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
    total = limit + 12
    r = AnsiStreamRenderer(width=80)
    r.write("> [!NOTE] 概要\n")
    # ★ 长容器预览按行数节流（见 ``_throttle_container_preview``）——最后一批
    #   追加需 >= 刷新步长才会刷新到最新尾行（逐行写入时尾部最多滞后一个
    #   步长，见 test_stream_markdown_render_fixes_v7）。
    half = total // 2
    r.write("".join(f"> body{i}\n" for i in range(half)))
    r.write("".join(f"> body{i}\n" for i in range(half, total)))
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev[0] == "\u25a0 NOTE 概要"
    assert any("省略" in p for p in prev)
    assert prev[-1] == "    body%d" % (total - 1)


def test_details_preview_truncation_notice():
    """超长 <details> 预览给出省略提示（含正文行）。"""
    limit = RegexFreeBlockParser._PREVIEW_MAX_LINES
    total = limit + 10
    r = AnsiStreamRenderer(width=80)
    r.write("<details><summary>标题</summary>\n")
    for i in range(total):
        r.write(f"body{i}\n")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev[0] == "\u25b6 标题"
    assert any("省略" in p for p in prev)


# ═══════════════════════════════════════════════════════════
# A2 段落预览跨软换行行内标记配对
# ═══════════════════════════════════════════════════════════


def test_paragraph_preview_multiline_bold_paired():
    """跨软换行的 ``**加粗**`` 在预览期即正确配对（与提交一致）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("这是 **加粗\n")
    r.write("跨行** 结尾\n")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev == ["这是 加粗", "跨行 结尾"]
    r.write("\n")
    assert [l.plain for l in r.take_lines()] == ["这是 加粗", "跨行 结尾"]


def test_paragraph_preview_multiline_code_paired():
    """跨软换行的行内代码在预览期即正确配对。"""
    prev = _preview(["行内 `code\n", "跨行` 结尾\n"])
    assert prev == ["行内 code", "跨行 结尾"]


def test_paragraph_preview_line_level_without_markers(monkeypatch):
    """无行内标记的多行段落保持行级增量渲染（不整段重解析）。"""
    from src.renderer.ansi import blocks as _blocks

    calls = {"n": 0}
    orig = _blocks.render_paragraph_line

    def spy(text):
        calls["n"] += 1
        return orig(text)

    monkeypatch.setattr(_blocks, "render_paragraph_line", spy)
    r = AnsiStreamRenderer(width=80)
    for i in range(60):
        r.write(f"行{i}\n")
        r.take_preview_lines()
    assert calls["n"] <= 80, calls["n"]


# ═══════════════════════════════════════════════════════════
# B1 折叠块 / FencedDiv 流式预览正文不丢失
# ═══════════════════════════════════════════════════════════


def test_details_preview_renders_body_lines():
    """未闭合 <details> 预览渲染正文行（修复前正文全部丢失）。"""
    prev = _preview([
        "<details><summary>标题</summary>\n",
        "正文一\n",
        "正文二\n",
    ])
    assert prev[0] == "\u25b6 标题"
    assert "正文一" in "\n".join(prev)
    assert "正文二" in "\n".join(prev)


def test_fenced_div_preview_renders_body_lines():
    """未闭合 FencedDiv 预览渲染正文行（修复前正文全部丢失）。"""
    prev = _preview([
        ":::tip 提示\n",
        "正文一\n",
        "正文二\n",
    ])
    assert prev[0].startswith("\u25aa TIP")
    assert "正文一" in "\n".join(prev)
    assert "正文二" in "\n".join(prev)


# ═══════════════════════════════════════════════════════════
# C1 围栏自动关闭加固
# ═══════════════════════════════════════════════════════════


def test_auto_close_disabled_after_plain_content():
    """块内出现过普通内容行后，尾部 Markdown 结构不再触发自动关闭。"""
    lines = _render(
        ["```text\n", "print('hello')\n"]
        + ["## H0\n", "---\n", "## H1\n", "---\n", "## H2\n", "---\n"]
        + ["正文\n"]
    )
    joined = "\n".join(lines)
    assert "print('hello')" in joined
    assert "## H0" in joined
    # 未触发自动关闭 → 不产出块外 HR（整行 ─ 组成的分隔线）
    assert not any(l and set(l) == {"\u2500"} for l in lines)


def test_auto_close_still_triggers_for_pure_structure():
    """纯结构行（无普通内容行）仍触发自动关闭（围栏漏闭合容错保留）。"""
    lines = _render(
        ["```text\n"]
        + ["## H0\n", "---\n", "## H1\n", "---\n", "## H2\n", "---\n"]
        + ["正文\n"]
    )
    assert any(l and set(l) == {"\u2500"} for l in lines)


def test_auto_close_reset_between_blocks():
    """自动关闭的「普通内容」标记按代码块隔离——新块重新判定。"""
    lines = _render([
        "```text\n", "x = 1\n", "```\n",
        "```text\n",
        "## H0\n", "---\n", "## H1\n", "---\n", "## H2\n", "---\n",
        "正文\n",
    ])
    assert any(l and set(l) == {"\u2500"} for l in lines)


# ═══════════════════════════════════════════════════════════
# D1 表格预览列宽收缩缓存
# ═══════════════════════════════════════════════════════════


def test_table_preview_shrink_cached(monkeypatch):
    """列宽与终端宽度不变时不再重算列宽收缩。"""
    from src.renderer.ansi import table as _t

    calls = {"n": 0}
    orig = _t._shrink_widths

    def spy(widths, max_total, ncols):
        calls["n"] += 1
        return orig(widths, max_total, ncols)

    monkeypatch.setattr(_t, "_shrink_widths", spy)
    cache = _t.TablePreviewCache()
    rows = [["h1", "h2"], ["a" * 100, "b"]]
    cache.render(("h1", "h2"), rows, ["left", "left"], 40)
    assert calls["n"] == 1
    for _ in range(5):
        cache.render(("h1", "h2"), rows, ["left", "left"], 40)
    assert calls["n"] == 1
    # 终端宽度变化 → 重新收缩
    cache.render(("h1", "h2"), rows, ["left", "left"], 50)
    assert calls["n"] == 2


def test_table_preview_shrink_cache_invalidated_on_new_row(monkeypatch):
    """新增更宽的数据行后列宽变化 → 重新收缩。"""
    from src.renderer.ansi import table as _t

    calls = {"n": 0}
    orig = _t._shrink_widths

    def spy(widths, max_total, ncols):
        calls["n"] += 1
        return orig(widths, max_total, ncols)

    monkeypatch.setattr(_t, "_shrink_widths", spy)
    cache = _t.TablePreviewCache()
    cache.render(("h",), [["h"], ["a"]], ["left"], 40)
    assert calls["n"] == 1
    cache.render(("h",), [["h"], ["a"], ["z" * 200]], ["left"], 40)
    assert calls["n"] == 2


# ═══════════════════════════════════════════════════════════
# E1 表格预览缓存键
# ═══════════════════════════════════════════════════════════


def test_table_preview_cache_key_isolates_tables():
    """不同表头的表格预览不互相复用缓存行。"""
    from src.renderer.ansi.table import TablePreviewCache

    cache = TablePreviewCache()
    out1 = cache.render(("h1", "h2"), [["h1", "h2"], ["a", "b"]],
                        ["left", "left"], 80)
    out2 = cache.render(("x1", "x2"), [["x1", "x2"], ["c", "d"]],
                        ["left", "left"], 80)
    plain1 = ["".join(r.text for r in line.runs) for line in out1]
    plain2 = ["".join(r.text for r in line.runs) for line in out2]
    assert any("h1" in p for p in plain1)
    assert any("x1" in p for p in plain2)
    assert not any("h1" in p for p in plain2)
