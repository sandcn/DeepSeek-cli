"""流式 Markdown 渲染性能优化（第十一批）测试。

覆盖本轮优化项的正确性与等价性（不做时间断言，避免并行下的抖动）：

  1. ``_line_match.common_prefix_len`` 的二分实现与朴素前缀扫描等价；
  2. ``AnsiStreamRenderer._note_paragraph_triggers`` 的增量触发位置维护
     （含「预览尾部滑窗」形态）与「对尾部窗口全量重扫」判定等价
     —— 即 ``_plain_active_window`` / ``_window_has_core_markup`` 的
     纯文本快路径判定不因优化而改变；
  3. 长段落的流式增量渲染（预览滑窗 + 行级缓存）与一次性渲染产出一致；
  4. 表格预览缓存（含列宽变化时的整表重建）与直接渲染产出一致；
  5. 预览截断提示行按 ``dropped`` 值复用（对象级缓存）且内容正确。
"""

from __future__ import annotations

import random

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi import _last_core_trigger_pos, _last_url_prefix_pos
from src.renderer.ansi._line_match import common_prefix_len
from src.renderer.ansi.inline import _last_typo_pos
from src.renderer.ansi.table import TablePreviewCache, render_table
from src.renderer.types import Token, TokenType

_LINE = "这是一行包含 **粗体** 与 `code` 与 https://example.com/x 的内容描述文字。"
_PLAIN = "这是一行完全没有任何行内标记的普通中文文本，用于纯文本快路径对比验证。"


def _naive_common_prefix(a, b) -> int:
    n = len(a) if len(a) < len(b) else len(b)
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _plain_ref(content: str) -> bool:
    """参考实现：对尾部窗口全量重扫，判定是否为「纯文本」活动行窗口。"""
    from src.renderer.ansi import _PREVIEW_MAX_LINE_CHARS

    n = len(content)
    start = n - _PREVIEW_MAX_LINE_CHARS
    if start < 0:
        start = 0
    win = content[start:]
    if _last_core_trigger_pos(win) >= 0 or _last_url_prefix_pos(win) >= 0:
        return False
    return _last_typo_pos(win) < 0


def _plain(rows) -> list[str]:
    return [ln.plain for ln in rows]


# ── 1. common_prefix_len ─────────────────────────────────


def test_common_prefix_len_matches_naive() -> None:
    rnd = random.Random(20261010)
    for _ in range(4000):
        a = [rnd.randrange(4) for _ in range(rnd.randrange(0, 12))]
        b = [rnd.randrange(4) for _ in range(rnd.randrange(0, 12))]
        assert common_prefix_len(a, b) == _naive_common_prefix(a, b)


def test_common_prefix_len_sliding_window() -> None:
    """头部滑窗（首元素不同）返回 0；纯追加返回较短长度。"""
    shared = [f"line{i}" for i in range(20)]
    assert common_prefix_len(shared + ["x"], shared[:-1] + ["y"]) == 19
    assert common_prefix_len(shared[1:], shared) == 0
    assert common_prefix_len([], shared) == 0


# ── 2. 触发位置增量维护（含滑窗） ─────────────────────────


def test_plain_active_window_matches_full_scan_append() -> None:
    """逐字符追加（前缀增长）时增量维护与全量扫描判定一致。"""
    r = AnsiStreamRenderer(width=80)
    text = ""
    for ch in _LINE:
        text += ch
        r._note_paragraph_triggers(text)
        assert r._plain_active_window(text) == _plain_ref(text)


def test_plain_active_window_matches_full_scan_sliding() -> None:
    """预览尾部滑窗（头部丢弃 + 尾部追加）时判定一致。

    模拟解析器 ``_preview_tail`` 的截断语义：每轮丢弃最旧若干字符、追加新
    内容——此时全文与上一帧既非前缀关系，正是修复前触发「整段全量重扫」的
    形态。优化后只重扫活动行窗口，判定仍须与全量扫描一致。
    """
    r = AnsiStreamRenderer(width=80)
    base = (_LINE + "\n") * 6 + "还在继续追加的"
    text = base
    r._note_paragraph_triggers(text)
    assert r._plain_active_window(text) == _plain_ref(text)
    rnd = random.Random(7)
    for _ in range(400):
        drop = rnd.randrange(1, 6)
        add = rnd.choice([_PLAIN[:12], _LINE[:17], "https://gith", "**", "plain text "])
        text = text[drop:] + add
        r._note_paragraph_triggers(text)
        assert r._plain_active_window(text) == _plain_ref(text)


def test_window_has_core_markup_matches_full_scan() -> None:
    r = AnsiStreamRenderer(width=80)
    text = ""
    for ch in _LINE:
        text += ch
        r._note_paragraph_triggers(text)
        n = len(text)
        start = n - 4096
        if start < 0:
            start = 0
        ref = (_last_core_trigger_pos(text[start:]) >= 0
               or _last_url_prefix_pos(text[start:]) >= 0)
        assert r._window_has_core_markup(text) == ref


# ── 3. 长段落流式渲染一致性 ───────────────────────────────


def _stream_render(text: str, chunk: int, width: int = 90) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(text), chunk):
        r.write(text[i : i + chunk])
    r.close()
    return _plain(r.take_lines())


def _batch_render(text: str, width: int = 90) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(text)
    r.close()
    return _plain(r.take_lines())


def test_long_paragraph_stream_matches_batch() -> None:
    """超长段落（超过预览行数上限，触发预览滑窗）流式渲染与一次性渲染一致。"""
    text = "\n".join(f"第 {i} 行内容含 **粗体** 与 `code` 文本。" for i in range(320))
    assert _stream_render(text, 13) == _batch_render(text)


def test_long_paragraph_soft_wrap_stream_matches_batch() -> None:
    """长段落中未闭合行内标记（跨软换行）流式与一次性渲染一致。"""
    text = "**跨行粗体开始\n" + "\n".join(
        f"中间第 {i} 行普通内容 https://example.com/{i}" for i in range(260)
    ) + "\n结束**"
    assert _stream_render(text, 11) == _batch_render(text)


# ── 4. 表格预览缓存一致性 ─────────────────────────────────


def _table_token(rows, aligns):
    return Token(TokenType.TABLE, "", {"rows": rows, "alignments": aligns})


def test_table_preview_cache_matches_direct_render() -> None:
    rows = [["名称", "说明", "数值"]]
    rows += [[f"row{i}", f"说明 {i}", str(i)] for i in range(60)]
    aligns = ["left", "left", "right"]
    cache = TablePreviewCache()
    out = cache.render(("t",), rows, aligns, 100)
    assert _plain(out) == _plain(render_table(_table_token(rows, aligns), 100))


def test_table_preview_cache_matches_direct_on_width_change() -> None:
    """列宽变化（新增更宽数据行）触发整表重建，产出仍与直接渲染一致。"""
    rows = [["名称", "说明", "数值"]]
    rows += [[f"row{i}", f"说明 {i}", str(i)] for i in range(60)]
    aligns = ["left", "left", "right"]
    cache = TablePreviewCache()
    cache.render(("t",), rows, aligns, 100)
    wider = rows + [["超宽单元格内容 xxxxxxxxxxxx", "短", "999999"]]
    out = cache.render(("t",), wider, aligns, 100)
    assert _plain(out) == _plain(render_table(_table_token(wider, aligns), 100))


def test_table_preview_cache_sliding_tail_matches_direct() -> None:
    """数据区头部滑窗（预览截断移除旧行）后产出与直接渲染一致。"""
    aligns = ["left", "left"]
    cache = TablePreviewCache()
    rows = [["A", "B"]] + [[f"r{i}", f"v{i}"] for i in range(50)]
    cache.render(("t",), rows, aligns, 80)
    slid = [["A", "B"]] + [[f"r{i}", f"v{i}"] for i in range(10, 60)]
    out = cache.render(("t",), slid, aligns, 80)
    assert _plain(out) == _plain(render_table(_table_token(slid, aligns), 80))


# ── 5. 预览截断提示行缓存 ─────────────────────────────────


def test_omitted_line_reused_by_value() -> None:
    r = AnsiStreamRenderer(width=80)
    a = r._omitted_line(5)
    assert r._omitted_line(5) is a
    assert "5" in a.plain
    b = r._omitted_line(6)
    assert b is not a
    assert "6" in b.plain


# ── 6. 消毒的未检查行定位（首行 / 尾部区间） ──────────────


def test_sanitize_lines_head_tail_and_interval() -> None:
    from src.renderer.ansi.helpers import AnsiLine

    r = AnsiStreamRenderer(width=80)
    a, b, c = (AnsiLine.of(x) for x in "abc")
    out = r._sanitize_lines([a, b, c])
    assert out == [a, b, c]
    assert all(x._esc_checked for x in (a, b, c))

    # 尾部新增未检查行 → 只扫尾部（对象身份保持，返回原 list）
    d = AnsiLine.of("d")
    assert r._sanitize_lines([a, b, c, d]) is not None
    assert d._esc_checked is True

    # 末尾为已检查复用行（表格底边框形态）：中间的新行仍须被扫描标记
    e = AnsiLine.of("e")
    f = AnsiLine.of("f")
    f._esc_checked = True
    r._sanitize_lines([a, b, c, d, e, f])
    assert e._esc_checked is True and f._esc_checked is True

    # 含原始转义序列的行（不论位置）仍被正确清洗
    g = AnsiLine.of("x\x1b[31my")
    out = r._sanitize_lines([a, b, c, d, e, f, g])
    assert "\x1b" not in out[-1].plain
    assert "x" in out[-1].plain and "y" in out[-1].plain


def test_sanitize_lines_head_unchecked_rescans_all() -> None:
    """首行未检查（整段重建，如容器头每帧新建）→ 全部行都被处理。"""
    from src.renderer.ansi.helpers import AnsiLine

    r = AnsiStreamRenderer(width=80)
    head = AnsiLine.of("容器头")
    clean = AnsiLine.of("已检查行")
    clean._esc_checked = True
    dirty = AnsiLine.of("bad\x1b[2Jend")
    out = r._sanitize_lines([head, clean, dirty])
    assert head._esc_checked is True
    assert "\x1b" not in out[2].plain
    assert "bad" in out[2].plain and "end" in out[2].plain

