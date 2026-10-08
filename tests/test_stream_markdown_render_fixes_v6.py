"""TUI 流式 Markdown 渲染性能与缺陷修复回归（第六批）。

覆盖本轮修复：

  - **引用块长行预览性能**：``_split_blockquote`` 只扫描行首 ``>``/空格前缀
    （O(前缀)），修复前逐字符累积整行副本（O(行长)）——40k 字符单行引用
    流式渲染实测 22s（每帧 O(行长)，累计 O(n²)）；
  - **代码块超长活动行窗口化**：未换行的活动行超过 ``_PREVIEW_MAX_LINE_CHARS``
    时只词法高亮尾部窗口，修复前每帧对整行 pygments 高亮（80k 字符单行实测
    5.8s）；
  - **数学块预览节流**：源码增长不足步长时复用上次二维排版结果；超过
    ``_MATH_PREVIEW_MAX_SRC`` 的超长公式降级为提示框——8k 字符公式逐 8 字符
    写入实测 8s；短公式（< 48 字符）仍逐帧实时刷新；
  - **Mermaid 块预览节流**：图形布局同样按源码增长节流（60 节点实测
    321ms → 22ms）；
  - **表格预览列宽增量维护**：``TablePreviewCache`` 各列最大宽度改为增量
    更新（追加行只比较新行），结构未变的帧复用结果行列表——修复前每帧对
    全部数据行重算 max（O(行数×列数)）+ 重建整表行列表；
  - **待定 Front Matter 定界符预览**：下一行（活动行）内容一并预览，修复前
    只显示分隔线、正在输入的下一行整行突发。

同时固化「修复后产出与一次性渲染一致」（提交路径不受预览优化影响）。
"""
from __future__ import annotations

import random
import time

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.table import TablePreviewCache, render_table
from src.renderer.types import Token, TokenType


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════


def _one_shot(src: str, width: int = 100):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _stream(src: str, chunk: int = 4, width: int = 100, preview: bool = True):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
        if preview:
            r.take_preview_lines()
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _elapsed(src: str, chunk: int = 4, width: int = 100) -> float:
    r = AnsiStreamRenderer(width=width)
    t0 = time.perf_counter()
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
        r.take_preview_lines()
    dt = time.perf_counter() - t0
    r.close()
    r.take_lines()
    return dt


# ══════════════════════════════════════════════════════════
# 引用块：_split_blockquote 语义等价（只扫前缀）
# ══════════════════════════════════════════════════════════


def _legacy_split_blockquote(stripped: str):
    """修复前的逐字符实现（用于语义等价比对）。"""
    depth = 0
    in_gt = True
    gt_text = ''
    for ch in stripped:
        if ch == '>':
            if in_gt:
                depth += 1
            else:
                gt_text += ch
        elif ch == ' ' and in_gt:
            continue
        else:
            in_gt = False
            gt_text += ch if ch != ' ' or gt_text else ' '
    return depth, gt_text.strip()


def test_split_blockquote_equivalent_to_legacy():
    from src.renderer._block_helpers import _split_blockquote

    cases = [
        "> a", ">a", ">", ">>", "> >", ">>> x", "> > a", ">   b  ",
        "> a > b", ">| table", ">  ", ">>foo", "> > > deep",
        "> 多行 内容 ", "| not quote", "> - list", "> # h", "> [x]: y",
        "", " ", "> >", ">_", ">a>b",
    ]
    for s in cases:
        assert _split_blockquote(s) == _legacy_split_blockquote(s), repr(s)

    rnd = random.Random(20261009)
    for _ in range(3000):
        n = rnd.randint(0, 24)
        s = "".join(rnd.choice("> >a b|-#[]() x") for _ in range(n))
        assert _split_blockquote(s) == _legacy_split_blockquote(s), repr(s)


def test_split_blockquote_scans_prefix_only():
    """前缀扫描成本与行长无关（长行 → 微秒级）。"""
    from src.renderer._block_helpers import _split_blockquote

    line = "> " + "内容" * 20000
    t0 = time.perf_counter()
    for _ in range(200):
        assert _split_blockquote(line) == (1, "内容" * 20000)
    dt = (time.perf_counter() - t0) / 200
    assert dt < 0.0005, f"单次拆解耗时 {dt * 1e6:.1f}us"


# ══════════════════════════════════════════════════════════
# 长块流式性能（修复前 O(n²)）
# ══════════════════════════════════════════════════════════


def test_long_blockquote_stream_budget():
    """40k 字符单行引用流式渲染耗时有界（修复前 ~22s）。"""
    src = "> " + "字" * 40000 + "\n\n"
    _elapsed(src, chunk=64)          # 预热
    elapsed = _elapsed(src, chunk=4)
    assert elapsed < 3.0, f"长引用流式耗时 {elapsed:.3f}s"


def test_long_blockquote_output_complete():
    """长引用流式渲染产出完整（预览优化不影响提交内容）。"""
    src = "> " + "字" * 2000 + "\n\n"
    assert _stream(src) == _one_shot(src)


def test_long_code_line_stream_budget():
    """超长单行代码块流式渲染耗时有界（修复前 O(n²)。

    直接的时间断言受并行测试的 CPU 竞争影响，故用「后段单帧成本相对前段
    的增长比」判定复杂度（窗口化后每帧只高亮固定窗口，比值应在常数级；
    修复前每帧高亮整行、后段成本随行长线性上升）。
    """
    src = "```python\n" + "a" * 60000
    r = AnsiStreamRenderer(width=100)
    n = len(src)
    worst_early = 0.0
    worst_late = 0.0
    for i in range(0, n, 8):
        t0 = time.perf_counter()
        r.write(src[i:i + 8])
        r.take_preview_lines()
        dt = time.perf_counter() - t0
        if i < n // 4:
            worst_early = max(worst_early, dt)
        elif i > n * 3 // 4:
            worst_late = max(worst_late, dt)
    r.close()
    limit = max(worst_early * 20, 0.5)
    assert worst_late < limit, (worst_early, worst_late)
    # 完整性：提交内容不因预览窗口化而丢失
    _stream("```python\n" + "a" * 9000 + "\n```\n\n", chunk=64)  # 不抛异常


def test_long_math_block_stream_budget():
    """超长公式流式渲染耗时有界（修复前 8k 字符 ~8s）。"""
    src = "$$\n" + "x + " * 1000 + "1\n$$\n\n"
    _elapsed(src, chunk=32)
    elapsed = _elapsed(src, chunk=8)
    assert elapsed < 2.0, f"长公式流式耗时 {elapsed:.3f}s"


def test_mermaid_stream_budget():
    """多节点 Mermaid 流式渲染耗时有界（修复前 60 节点 ~0.32s）。"""
    body = "".join(f"  A{i}[节点{i}]-->B{i}[节点{i}]\n" for i in range(60))
    src = "```mermaid\ngraph TD\n" + body + "```\n\n"
    _elapsed(src, chunk=32)
    elapsed = _elapsed(src, chunk=8)
    assert elapsed < 0.5, f"mermaid 流式耗时 {elapsed:.3f}s"


def test_wide_table_stream_budget():
    """多行表格流式渲染耗时有界（列宽增量维护）。"""
    head = "| " + " | ".join(f"c{i}" for i in range(6)) + " |"
    sep = "|" + "|".join("---" for _ in range(6)) + "|"
    body = "\n".join(
        "| " + " | ".join(f"r{r}c{c}" for c in range(6)) + " |"
        for r in range(500)
    )
    src = "\n".join([head, sep, body]) + "\n\n"
    _elapsed(src, chunk=64)
    elapsed = _elapsed(src, chunk=4)
    assert elapsed < 3.0, f"宽表格流式耗时 {elapsed:.3f}s"


def test_long_front_matter_stream_budget():
    """超长 Front Matter 流式渲染耗时有界（预览行数上限 + 节流）。"""
    src = "---\n" + "".join(f"key{i}: 值{i}\n" for i in range(3000))   # ~60k
    _elapsed(src, chunk=64)
    elapsed = _elapsed(src, chunk=8)
    assert elapsed < 3.0, f"长元信息块流式耗时 {elapsed:.3f}s"


def test_long_front_matter_preview_limited_and_commit_complete():
    """超长 Front Matter 预览行数有界（含省略提示），提交内容完整。"""
    n = 300
    src = "---\n" + "".join(f"k{i}: v{i}\n" for i in range(n))
    r = AnsiStreamRenderer(width=70)
    r.write(src)
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert any("省略" in ln for ln in preview)
    assert len(preview) <= 210
    r.close()
    committed = [ln.plain for ln in r.take_lines()]
    assert any(f"k{n - 1}: v{n - 1}" in ln for ln in committed)


def test_long_html_block_line_stream_budget():
    """超长 HTML 块活动行流式渲染耗时有界（活动行窗口化）。"""
    src = "<div>\n" + "字" * 40000 + "\n</div>\n\n"
    _elapsed(src, chunk=64)
    elapsed = _elapsed(src, chunk=8)
    assert elapsed < 3.0, f"长 HTML 块流式耗时 {elapsed:.3f}s"


def test_long_html_block_line_preview_visible_and_complete():
    """超长 HTML 活动行预览显示最新内容，提交内容完整。"""
    src = "<div>\n" + "x" * 8000 + "TAILMARK"
    r = AnsiStreamRenderer(width=60)
    r.write(src)
    assert "TAILMARK" in "".join(ln.plain for ln in r.take_preview_lines())
    r.close()
    assert "TAILMARK" in "".join(ln.plain for ln in r.take_lines())


# ══════════════════════════════════════════════════════════
# 预览实时性（优化不得牺牲可见性）
# ══════════════════════════════════════════════════════════


def _first_visible(src: str, marker: str, width: int = 70):
    """逐字符写入，返回 marker 首次出现在（committed + preview）时的位置。"""
    r = AnsiStreamRenderer(width=width)
    for i in range(len(src)):
        r.write(src[i:i + 1])
        text = "".join(ln.plain for ln in r.take_preview_lines())
        text += "".join(ln.plain for ln in r.take_lines())
        if marker in text:
            return i
    return None


def test_math_preview_visible_for_short_formula():
    """短公式（< 节流阈值）逐字符可见（节流不得造成滞后）。"""
    src = "$$\nx = MARKER\n$$\n\n"
    pos = _first_visible(src, "MARKER")
    assert pos is not None, "短公式的活动行内容不可见"
    # 完整标记写入后应立即可见（不得滞后到公式闭合）
    assert pos <= src.index("MARKER") + len("MARKER")


def test_mermaid_preview_visible_for_small_graph():
    """小图的活动行内容逐字符可见。"""
    src = "```mermaid\ngraph TD\nA[MARKER]-->B\n```\n\n"
    assert _first_visible(src, "MARKER") is not None


def test_code_preview_visible_for_long_active_line():
    """超长活动行窗口化后仍可见最新内容（尾部窗口含最新字符）。"""
    src = "```\n" + "x" * 8000 + "TAILMARKER"
    pos = _first_visible(src, "TAILMARKER")
    assert pos is not None


def test_front_matter_pending_delimiter_previews_next_line():
    """待定 Front Matter 定界符：下一行（活动行）内容同样进入预览。"""
    r = AnsiStreamRenderer(width=60)
    r.write("---\n")
    r.write("title: MARKER")
    joined = "\n".join(ln.plain for ln in r.take_preview_lines())
    assert "MARKER" in joined, joined


def test_front_matter_confirmed_renders_card():
    """下一行换行后确认 Front Matter → 元信息卡片（内容不丢）。"""
    lines = _stream("---\ntitle: X\n---\n\n正文\n\n")
    assert any("元信息" in ln for ln in lines)
    assert any("title" in ln and "X" in ln for ln in lines)
    assert any("正文" in ln for ln in lines)


# ══════════════════════════════════════════════════════════
# 数学块：超长降级提示 + 提交完整
# ══════════════════════════════════════════════════════════


def test_math_overflow_preview_shows_notice():
    """超长公式预览降级为提示框（不再逐帧重排整段）。"""
    src = "$$\n" + "x + " * 600 + "1"            # 未闭合；源码 > 上限
    r = AnsiStreamRenderer(width=100)
    r.write(src)
    preview = "\n".join(ln.plain for ln in r.take_preview_lines())
    assert "公式过长" in preview, preview
    assert "完整渲染" in preview


def test_math_overflow_commit_is_complete():
    """超长公式提交仍完整排版（预览降级不影响提交）。"""
    src = "$$\n" + "x + " * 600 + "1\n$$\n\n"
    lines = _stream(src)
    assert any("数学公式" in ln for ln in lines)
    assert any("公式过长" in ln for ln in lines) is False
    assert not any(ln == "" for ln in lines[:1])


def test_math_short_formula_preview_matches_commit():
    """短公式预览与提交语义一致（节流不改变内容）。"""
    src = "$$\n\\frac{a}{b} + \\sum_{i=1}^{n} x_i\n$$\n\n"
    assert _stream(src) == _one_shot(src)


# ══════════════════════════════════════════════════════════
# 表格预览缓存：增量列宽与一次性渲染一致
# ══════════════════════════════════════════════════════════


def _table_token(rows):
    return Token(TokenType.TABLE, "", {
        "rows": rows,
        "alignments": ["left"] * max(len(r) for r in rows),
    })


def test_table_preview_cache_matches_oneshot_incremental():
    """逐行追加的表格预览结果与一次性渲染一致（列宽增量维护正确）。"""
    header = ["列一", "列二", "列三"]
    rows = [header]
    cache = TablePreviewCache()
    rnd = random.Random(7)
    for i in range(40):
        rows.append([f"r{i}c0", "x" * rnd.randint(1, 12), f"值{i}"])
        got = [ln.plain for ln in cache.render(
            tuple(header), [list(r) for r in rows], ["left"] * 3, 80)]
        assert got == [ln.plain for ln in render_table(
            _table_token([list(r) for r in rows]), 80)], f"row {i}"


def test_table_preview_cache_shrink_reused_result_matches():
    """列宽收缩 + 结果复用路径同样与一次性渲染一致。"""
    header = ["a", "b"]
    rows = [header, ["1", "2"], ["很长很长的内容测试列宽收缩", "3"]]
    cache = TablePreviewCache()
    for _ in range(3):
        got = [ln.plain for ln in cache.render(
            tuple(header), [list(r) for r in rows], ["left"] * 2, 30)]
        assert got == [ln.plain for ln in render_table(
            _table_token([list(r) for r in rows]), 30)]


def test_table_preview_cache_sliding_window_matches():
    """数据行滑窗（截断移除头部行）后列宽收缩仍正确。"""
    header = ["a", "b"]
    wide = ["X" * 30, "y"]
    narrow = ["x", "y"]
    rows = [header, wide] + [list(narrow) for _ in range(5)]
    cache = TablePreviewCache()
    cache.render(tuple(header), [list(r) for r in rows], ["left"] * 2, 60)
    # 移除最长的那一行（滑窗）→ 列宽应回缩
    rows2 = [header] + [list(narrow) for _ in range(5)]
    got = [ln.plain for ln in cache.render(
        tuple(header), [list(r) for r in rows2], ["left"] * 2, 60)]
    assert got == [ln.plain for ln in render_table(
        _table_token([list(r) for r in rows2]), 60)]


# ══════════════════════════════════════════════════════════
# 代码块活动行窗口：提交仍完整
# ══════════════════════════════════════════════════════════


def test_long_code_line_commit_is_complete():
    """超长单行代码提交内容完整（窗口化只影响预览）。"""
    src = "```python\n" + "a" * 9000 + "\n```\n\n"
    lines = _stream(src)
    assert any(len(ln) >= 9000 for ln in lines), max(len(ln) for ln in lines)


def test_code_preview_window_keeps_tail():
    """超长活动行预览只保留尾部窗口（成本封顶）且显示最新内容。"""
    src = "```\n" + "a" * 9000
    r = AnsiStreamRenderer(width=100)
    r.write(src)
    preview = r.take_preview_lines()
    assert preview
    joined = "".join(ln.plain for ln in preview)
    assert "a" * 100 in joined
