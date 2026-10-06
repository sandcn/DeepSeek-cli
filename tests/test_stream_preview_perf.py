"""流式预览性能 / 一致性加固回归测试。

覆盖清单：
  - 表格预览行数上限（消除无界整表重渲染）+ 表头保留
  - 段落 / 引用 / 告示预览行级增量（``LinePreviewCache``）
  - 超长未换行活动行尾部窗口化（封顶单帧渲染成本）
  - UI 层预览行 styled 缓存（``_block_styled_lines`` 复用 runs 对象）
  - 缩进代码块预览形态（不再"纯文本→代码块"跳变）
  - 超长输入安全消化（缓冲区裁剪不再丢内容）
  - 自动关闭 fence 结构多样性门槛（纯同类型行不再误触发）
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer._block_parser import RegexFreeBlockParser
from src.renderer.types import TokenType


def _render(chunks, width=80):
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    r.close()
    return [line.plain for line in r.take_lines()]


# ═══════════════════════════════════════════════════════════
# 表格预览行数上限
# ═══════════════════════════════════════════════════════════


def test_table_preview_rows_bounded_with_header():
    """超长表格预览只保留表头 + 尾部行（行数有界）。"""
    p = RegexFreeBlockParser()
    p.feed("| a | b |\n")
    p.feed("| --- | --- |\n")
    for i in range(500):
        p.feed(f"| {i} | v{i} |\n")
    toks = p.peek_pending()
    table = next(t for t in toks if t.type is TokenType.TABLE)
    rows = table.meta["rows"]
    assert len(rows) <= RegexFreeBlockParser._PREVIEW_MAX_LINES
    assert rows[0] == ["a", "b"]
    assert rows[-1] == ["499", "v499"]


def test_table_preview_short_table_full():
    """未超上限的表格预览保持全量。"""
    p = RegexFreeBlockParser()
    p.feed("| a | b |\n")
    p.feed("| --- | --- |\n")
    p.feed("| 1 | 2 |\n")
    toks = p.peek_pending()
    table = next(t for t in toks if t.type is TokenType.TABLE)
    assert table.meta["rows"] == [["a", "b"], ["1", "2"]]


def test_table_preview_no_deep_copy_rows():
    """表格预览行与解析器状态同源（浅拷贝，行内容只读共享）。"""
    p = RegexFreeBlockParser()
    p.feed("| a | b |\n")
    p.feed("| --- | --- |\n")
    p.feed("| 1 | 2 |\n")
    rows = next(t for t in p.peek_pending() if t.type is TokenType.TABLE).meta["rows"]
    assert rows[-1] is p._table_rows[-1]


def test_preview_header_only_row_as_table():
    """表头单行（分隔行未到）按表格形态预览（消除字面 pipe → 框线跳变）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("| a | b |\n")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev and prev[0].startswith("\u250c")


def test_preview_ambiguous_pipe_row_stays_paragraph():
    """无前导 pipe 的歧义单行 ``a | b`` 保持段落预览（不误判为表格）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("a | b\n")
    prev = [l.plain for l in r.take_preview_lines()]
    assert prev == ["a | b"]


def test_preview_setext_underline_after_multiline_is_hr():
    """多行上文 + ``===`` 不构成 Setext（与提交一致）→ 预览为分隔线。"""
    r = AnsiStreamRenderer(width=80)
    r.write("第一行\n")
    r.write("第二行\n")
    r.write("===")
    prev = [l.plain for l in r.take_preview_lines()]
    assert any("\u2500" * 10 in p for p in prev)
    assert any("第一行" in p for p in prev)


def test_preview_setext_single_line_is_heading():
    """单行上文 + ``===`` 构成 Setext 标题 → 预览为标题（无下划线行）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("标题\n")
    r.write("===")
    assert [l.plain for l in r.take_preview_lines()] == ["标题"]


# ═══════════════════════════════════════════════════════════
# 段落 / 引用预览行级增量
# ═══════════════════════════════════════════════════════════


def test_paragraph_preview_incremental(monkeypatch):
    """段落预览按行增量：历史行只渲染一次（非每帧整段重渲染）。"""
    from src.renderer.ansi import blocks as _blocks

    calls = {"n": 0}
    orig = _blocks.render_paragraph_line

    def spy(text):
        calls["n"] += 1
        return orig(text)

    monkeypatch.setattr(_blocks, "render_paragraph_line", spy)
    r = AnsiStreamRenderer(width=80)
    for i in range(100):
        r.write(f"line{i}\n")
        r.take_preview_lines()
    # 全量重渲染为 sum(1..100)=5050；增量应接近 100
    assert calls["n"] <= 130


def test_paragraph_preview_content_matches_committed():
    """增量预览与提交后的行内容一致。"""
    r = AnsiStreamRenderer(width=80)
    r.write("第一行\n")
    r.take_preview_lines()
    r.write("第二行\n")
    assert [l.plain for l in r.take_preview_lines()] == ["第一行", "第二行"]
    r.write("\n")
    assert [l.plain for l in r.take_lines()] == ["第一行", "第二行"]


def test_blockquote_preview_incremental(monkeypatch):
    """引用预览同样按行增量。"""
    from src.renderer.ansi import blocks as _blocks

    calls = {"n": 0}
    orig = _blocks.render_blockquote_line

    def spy(text, depth=0):
        calls["n"] += 1
        return orig(text, depth)

    monkeypatch.setattr(_blocks, "render_blockquote_line", spy)
    r = AnsiStreamRenderer(width=80)
    for i in range(80):
        r.write(f"> 引用行{i}\n")
        r.take_preview_lines()
    assert calls["n"] <= 100


def test_admonition_preview_renders_body():
    """告示预览渲染正文行（增量路径不丢内容）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("> [!NOTE]\n")
    r.write("> 正文第一行\n")
    r.write("> 正文第二行\n")
    prev = "\n".join(l.plain for l in r.take_preview_lines())
    assert "正文第一行" in prev
    assert "正文第二行" in prev


# ═══════════════════════════════════════════════════════════
# 超长未换行活动行窗口化
# ═══════════════════════════════════════════════════════════


def test_long_active_line_windowed():
    """超长未换行活动行只预览尾部窗口（单帧成本封顶）。"""
    from src.renderer.ansi import _PREVIEW_MAX_LINE_CHARS

    r = AnsiStreamRenderer(width=80)
    r.write("x" * (_PREVIEW_MAX_LINE_CHARS + 500))
    lines = r.take_preview_lines()
    assert lines
    assert len(lines[0].plain) <= _PREVIEW_MAX_LINE_CHARS


def test_short_active_line_not_windowed():
    """未超上限的活动行完整预览（内容不截断）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("短行内容")
    assert [l.plain for l in r.take_preview_lines()] == ["短行内容"]


# ═══════════════════════════════════════════════════════════
# 缩进代码块预览形态
# ═══════════════════════════════════════════════════════════


def test_preview_indented_code_becomes_code_block():
    """缩进代码活动行按代码块形态预览（无"纯文本→代码块"跳变）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("    code line")
    prev = [l.plain for l in r.take_preview_lines()]
    assert any("code line" in p for p in prev)
    assert any(p.strip().startswith("```") for p in prev)


def test_preview_indented_code_in_list_stays_paragraph():
    """列表上下文中的缩进行不误判为代码块（与提交语义一致）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("- 项\n")
    r.take_lines()
    r.write("    续行")
    prev = [l.plain for l in r.take_preview_lines()]
    assert not any(p.strip().startswith("```") for p in prev)


# ═══════════════════════════════════════════════════════════
# 超长输入安全消化（缓冲区裁剪不再丢内容）
# ═══════════════════════════════════════════════════════════


def test_oversized_single_line_not_dropped():
    """单行超过缓冲上限时按当前状态完整消化——不丢内容。"""
    p = RegexFreeBlockParser()
    big = "x" * (p._MAX_BUFFER_SIZE + 100)
    toks = list(p.feed(big)) + list(p.flush())
    text = "".join(t.content for t in toks)
    assert len(text) >= p._MAX_BUFFER_SIZE


def test_oversized_line_inside_code_fence_kept():
    """代码块内的超长单行按块内行消化（不丢内容、状态不重置）。"""
    p = RegexFreeBlockParser()
    p.feed("```python\n")
    big = "y" * (p._MAX_BUFFER_SIZE + 50)
    toks = list(p.feed(big)) + list(p.flush())
    text = "".join(t.content for t in toks)
    assert len(text) >= p._MAX_BUFFER_SIZE


# ═══════════════════════════════════════════════════════════
# 自动关闭 fence 结构多样性门槛
# ═══════════════════════════════════════════════════════════


def test_uniform_heading_lines_do_not_auto_close():
    """代码块内连续同类型 ``## 注释`` 行不触发自动关闭（内容不误截断）。"""
    lines = _render(
        ["```text\n"] + [f"## note {i}\n" for i in range(8)] + ["```\n"]
    )
    joined = "\n".join(lines)
    for i in range(8):
        assert f"## note {i}" in joined


def test_uniform_hr_lines_do_not_auto_close():
    """代码块内连续 ``---`` 行不触发自动关闭。"""
    lines = _render(["```text\n"] + ["---\n" for _ in range(8)] + ["```\n"])
    joined = "\n".join(lines)
    assert "---" in joined


def test_mixed_structures_auto_close():
    """标题 + 分隔线混合结构连续出现触发自动关闭（围栏漏闭合容错保留）。"""
    lines = _render(
        ["```text\n"]
        + ["## H0\n", "---\n", "## H1\n", "---\n", "## H2\n", "---\n"]
        + ["正文\n"]
    )
    joined = "\n".join(lines)
    # 触发自动关闭的那个 ``---`` 以块外 HR 语义渲染（长横线），而非字面 ``---``
    assert any("\u2500" * 10 in line for line in lines)
    assert "正文" in joined


# ═══════════════════════════════════════════════════════════
# UI 层预览行 styled 缓存
# ═══════════════════════════════════════════════════════════


def test_preview_styled_cache_reuses_runs():
    """``_block_styled_lines`` 对预览行复用 runs 对象（同一对象身份）。"""
    from src.tui.app.chat_view import _block_styled_lines
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="预览一行\n"))
    blk = m.blocks[m.content_block_index]
    first = _block_styled_lines(blk, blk.committed_line_count, 80)
    second = _block_styled_lines(blk, blk.committed_line_count, 80)
    assert first and second
    assert first[-1] is second[-1]


def test_preview_styled_cache_bounded():
    """预览 styled 缓存有容量上限（不随预览替换无限累积）。"""
    from src.tui.app.chat_view import (
        _block_styled_lines, _PREVIEW_STYLED_CACHE_MAX,
    )
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    blk = m.blocks[m.content_block_index] if m.blocks else None
    for i in range(_PREVIEW_STYLED_CACHE_MAX + 40):
        _do_content(m, ContentCmd(text=f"r{i}\n"))
    blk = m.blocks[m.content_block_index]
    _block_styled_lines(blk, blk.committed_line_count, 80)
    cache = getattr(blk, "_preview_styled_cache", {})
    assert len(cache) <= _PREVIEW_STYLED_CACHE_MAX + 1


# ═══════════════════════════════════════════════════════════
# 表格预览增量（列宽 / 行渲染复用）
# ═══════════════════════════════════════════════════════════


def test_table_preview_cache_reuses_stable_rows():
    """表格预览缓存：列宽不变时表头块与历史数据行对象复用。"""
    from src.renderer.ansi.table import TablePreviewCache

    cache = TablePreviewCache()
    rows = [["h1", "h2"], ["a", "b"]]
    out1 = cache.render((), rows, ["left", "left"], 80)
    rows2 = rows + [["c", "d"]]
    out2 = cache.render((), rows2, ["left", "left"], 80)
    # 表头块（上边框 + 表头 + 中边框）对象复用
    assert out1[0] is out2[0]
    assert out1[1] is out2[1]
    assert out1[2] is out2[2]
    assert len(out2) > len(out1)


def test_table_preview_cache_sliding_window():
    """表格预览缓存：头部滑窗（截断移除旧行）后输出仍正确。"""
    from src.renderer.ansi.table import TablePreviewCache

    cache = TablePreviewCache()
    rows = [["h"]] + [[str(i)] for i in range(6)]
    cache.render((), rows, ["left"], 40)
    rows2 = [["h"]] + [[str(i)] for i in range(2, 8)]
    out = cache.render((), rows2, ["left"], 40)
    plain = ["".join(r.text for r in line.runs) for line in out]
    assert plain[0].startswith("\u250c")
    assert plain[-1].startswith("\u2514")
    assert any("7" in p for p in plain)


def test_table_preview_width_computed_once_per_row(monkeypatch):
    """表格预览列宽：新增行只算新行的单元格宽度（非每帧全量重算）。"""
    from src.renderer.ansi import table as _t

    calls = {"n": 0}
    orig = _t._row_cell_widths

    def spy(row, style):
        calls["n"] += 1
        return orig(row, style)

    monkeypatch.setattr(_t, "_row_cell_widths", spy)
    r = AnsiStreamRenderer(width=80)
    r.write("| a | b |\n")
    r.write("| --- | --- |\n")
    for i in range(100):
        r.write(f"| {i} | v{i} |\n")
        r.take_lines()
        r.take_preview_lines()
    # 表头 1 次 + 每数据行 1 次 ≈ 101（全量重算为 sum(1..100)=5050）
    assert calls["n"] <= 130


# ═══════════════════════════════════════════════════════════
# 代码块预览增量 split
# ═══════════════════════════════════════════════════════════


def test_code_preview_incremental_split(monkeypatch):
    """代码块预览：分块到达时只高亮新增行（增量 split 不重复整段高亮）。"""
    from src.renderer.ansi import code as _code

    total = {"n": 0}
    orig = _code.highlight_code_lines

    def spy(lines, lang="", theme="monokai", highlight_lines=None, start_index=1):
        total["n"] += len(lines)
        return orig(lines, lang, theme, highlight_lines, start_index)

    monkeypatch.setattr(_code, "highlight_code_lines", spy)
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    text = "".join(f"v{i} = {i}\n" for i in range(200))
    for i in range(0, len(text), 7):
        r.write(text[i:i + 7])
        r.take_lines()
        r.take_preview_lines()
    # 全量每帧重渲约 166x 总行数（数万）；增量应接近「每行少量行内重渲」
    assert total["n"] < 700


# ═══════════════════════════════════════════════════════════
# ANSI 消毒行级缓存 / RGB 色号缓存
# ═══════════════════════════════════════════════════════════


def test_sanitize_lines_marks_clean_rows():
    """消毒：无转义序列的行打上已检查标记（跨帧跳过重扫）。"""
    from src.renderer.ansi.helpers import AnsiLine

    r = AnsiStreamRenderer(width=80)
    line = AnsiLine.of("clean text")
    out = r._sanitize_lines([line])
    assert out[0] is line
    assert line._esc_checked is True
    # 已标记的行再次消毒零成本（同一对象）
    assert r._sanitize_lines([line])[0] is line


def test_sanitize_lines_still_strips_escape():
    """消毒：含转义序列的行仍被正确清洗（缓存标记不误伤）。"""
    from src.renderer.ansi.helpers import AnsiLine

    r = AnsiStreamRenderer(width=80)
    line = AnsiLine.of("pre\x1b[2Jpost")
    out = r._sanitize_lines([line])
    assert "\x1b" not in out[0].plain
    assert "pre" in out[0].plain and "post" in out[0].plain


def test_sanitize_lines_invalidated_on_append():
    """消毒：行内容追加后标记失效（重新检查）。"""
    from src.renderer.ansi.helpers import AnsiLine

    line = AnsiLine.of("clean")
    line._esc_checked = True
    line.append("more")
    assert line._esc_checked is False


def test_rgb_to_256_cached():
    """RGB → 256 色号带结果缓存（避免每次线性搜索色板）。"""
    from src.renderer.ansi import style as _style

    _style._256_CACHE.clear()
    a = _style.rgb_to_256(200, 100, 50)
    b = _style.rgb_to_256(200, 100, 50)
    assert a == b
    assert (200, 100, 50) in _style._256_CACHE


def test_hex_to_256_cached():
    """hex → 256 色号带结果缓存（避免重复解析 hex）。"""
    from src.renderer.ansi import code as _code

    _code._HEX_256_CACHE.clear()
    a = _code._hex_to_256("#ff0000")
    assert "#ff0000" in _code._HEX_256_CACHE
    assert _code._hex_to_256("#ff0000") == a


def test_fg_style_cached():
    """同色号共享同一 Style 对象（避免每 token 重建 frozen dataclass）。"""
    from src.renderer.ansi import code as _code

    _code._FG_STYLE_CACHE.clear()
    a = _code._fg_style(45)
    b = _code._fg_style(45)
    assert a is b
    assert _code._fg_style(None) is None


def test_table_preview_bottom_cached():
    """表格预览缓存：列宽不变时底边框对象复用。"""
    from src.renderer.ansi.table import TablePreviewCache

    cache = TablePreviewCache()
    rows = [["h1", "h2"], ["a", "b"]]
    out1 = cache.render((), rows, ["left", "left"], 80)
    out2 = cache.render((), rows + [["c", "d"]], ["left", "left"], 80)
    assert out1[-1] is out2[-1]


def test_sanitize_lines_incremental_tail():
    """消毒：仅新增尾行被扫描，全干净时返回原 list（零构建）。"""
    from src.renderer.ansi.helpers import AnsiLine

    r = AnsiStreamRenderer(width=80)
    lines = [AnsiLine.of(f"l{i}") for i in range(5)]
    out1 = r._sanitize_lines(lines)
    assert out1 is lines
    assert all(l._esc_checked for l in lines)
    lines.append(AnsiLine.of("new"))
    out2 = r._sanitize_lines(lines)
    assert out2 is lines
    assert lines[-1]._esc_checked is True


# ═══════════════════════════════════════════════════════════
# 预览 token 携带行列表 / take_preview_lines 零复制
# ═══════════════════════════════════════════════════════════


def test_code_preview_token_carries_lines():
    """代码块预览 token 直接携带行列表（免 join/split 往返），content 为空。"""
    p = RegexFreeBlockParser()
    p.feed("```python\nx = 1\n")
    tok = next(t for t in p.peek_pending() if t.type is TokenType.CODE_BLOCK)
    assert tok.meta.get("lines") == ["x = 1"]
    assert tok.content == ""


def test_code_preview_with_tail_line_in_meta():
    """未换行的活动行并入 meta["lines"]（与提交后一致）。"""
    p = RegexFreeBlockParser()
    p.feed("```python\nx = 1\ny = 2")
    tok = next(t for t in p.peek_pending() if t.type is TokenType.CODE_BLOCK)
    assert tok.meta.get("lines") == ["x = 1", "y = 2"]


def test_take_preview_lines_no_copy():
    """``take_preview_lines`` 不再复制列表（全干净预览返回同一对象）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("- 项")
    a = r.take_preview_lines()
    b = r.take_preview_lines()
    assert a is b
