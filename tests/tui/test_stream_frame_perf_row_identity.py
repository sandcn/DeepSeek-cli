"""流式 Markdown 渲染帧性能（行标识复用 / 批量 bailout / 换行快路径）测试。

覆盖本轮的帧级优化（正确性与等价性，不做时间断言避免并行抖动）：

  1. ``AnsiLine.sid`` —— 行稳定标识：唯一、跨帧复用不变、``append`` 不变、
     ``clone`` 分配新标识；
  2. ``_OpenChildrenCache`` 按行标识复用行 TEXT Element——流式预览的尾部
     滑窗（行位置每帧变化）不再重建历史行 Element；
  3. ``wrap_runs_by_width`` 新增的「通用单行快路径」与通用算法产出等价
     （不超宽多 run → 单行；含换行/超宽仍走原路径）；
  4. 调和器纯 host 子树 bailout —— children 元组引用未变时不再逐行调和；
     含函数组件的子树照常调和（状态更新不被跳过）；
  5. 代码块预览装饰行（围栏/标题/省略提示/关闭围栏）对象跨帧复用。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import AnsiLine
from src.tui.ink import h, TEXT, BOX, StyledRun
from src.tui.ink import _runs_utils
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components
from src.tui.app.chat_view import _block_styled_lines, _build_open_children
from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui._const import ContentCmd


# ── 1. AnsiLine.sid ─────────────────────────────────────


def test_line_sid_unique_and_stable():
    a = AnsiLine.of("a")
    b = AnsiLine.of("b")
    assert a.sid != b.sid
    before = a.sid
    a.append("more")
    assert a.sid == before
    assert a.sid != b.sid


def test_line_clone_gets_new_sid():
    a = AnsiLine.of("x")
    c = a.clone()
    assert c.sid != a.sid
    assert c.plain == a.plain


# ── 2. 行标识 Element 复用（滑窗场景） ─────────────────────


def _content_model(text: str, chunk: int = 64):
    m = AppModel()
    m.width = 100
    for i in range(0, len(text), chunk):
        apply_cmd(m, ContentCmd(text=text[i:i + chunk]))
    return m


def test_open_children_reuse_elements_across_sliding_window():
    """长代码块预览滑窗：同一源码行的 Line 组跨帧复用（不重排版）。"""
    # 未闭合代码块（流式中间态：预览窗口持续存在）
    text = "```python\n" + "\n".join(
        f"v{i} = compute({i})  # 注释" for i in range(150)) + "\n"
    m = _content_model(text, chunk=64)
    blk = m.blocks[m.content_block_index]
    idx = m.content_block_index
    assert len(blk.preview_lines) > 100

    _build_open_children(blk, blk.committed_line_count, 100, idx, True, "⠋")
    cache = blk._open_children_cache
    groups = dict(cache.groups)
    assert len(groups) > 100

    # 再追加内容（预览窗口整体右移：行位置变化、行对象复用）
    apply_cmd(m, ContentCmd(text="v9999 = compute(9999)  # 注释\n"))
    _build_open_children(blk, blk.committed_line_count, 100, idx, True, "⠙")
    reused = sum(
        1 for key, group in cache.groups.items()
        if groups.get(key) is group
    )
    assert reused > 100, f"行 Line 组未跨帧复用（{reused}）"


def test_open_children_preserves_rows_and_order():
    """批量行发射不改变渲染行内容/顺序（与 _block_styled_lines 一致）。"""
    # 未闭合代码块（预览行序列与活动行区分明确）
    text = "```python\n" + "\n".join(
        f"v{i} = {i}" for i in range(120)) + "\n"
    m = _content_model(text, chunk=48)
    blk = m.blocks[m.content_block_index]
    idx = m.content_block_index
    rows = _block_styled_lines(blk, blk.committed_line_count, 100)
    children = _build_open_children(
        blk, blk.committed_line_count, 100, idx, True, "⠋")
    # children = [omitted?, LiveLines(稳定行), active TEXT]
    live_lines = []
    active_runs = []
    from src.tui.ink.widgets.livelines import LiveLines

    for child in children:
        if child.type is LiveLines:
            live_lines.extend(child.props["lines"])
        elif child.type == TEXT:
            active_runs.append(child.props.get("styled") or [])
    # 稳定行 + 活动行 = _block_styled_lines 全部行（内容与顺序一致）
    rendered_plain = ["".join(r.text for r in ln.runs) for ln in live_lines]
    expected = ["".join(r.text for r in runs) for runs in rows]
    assert rendered_plain == expected[:-1]
    assert active_runs, "live content 末行应为活动 TEXT"
    active_plain = "".join(r.text for r in active_runs[-1])
    # 活动行 = 最后一行源文本 + spinner 帧（BEAUTY-32）
    assert active_plain.startswith(expected[-1])


def test_open_children_cache_prune_bounded():
    """滑窗持续进行时行缓存有界（不随块增长无限累积）。"""
    m = AppModel()
    m.width = 100
    apply_cmd(m, ContentCmd(text="```python\n"))
    idx = m.content_block_index
    for i in range(600):
        apply_cmd(m, ContentCmd(text=f"v{i} = {i}\n"))
        blk = m.blocks[idx]
        _build_open_children(blk, blk.committed_line_count, 100, idx, True, "⠋")
    blk = m.blocks[idx]
    cache = blk._open_children_cache
    # 预览窗口上限 200 + 省略提示/活动行 → 缓存条目不应随 600 行线性增长
    assert len(cache.groups) < 400


def test_preview_rows_incremental_matches_full_rebuild():
    """预览行滑窗增量与全量重建产出一致（行内容/顺序/标识数量）。"""
    # 未闭合长表格：预览窗口达到上限后头部滑窗（每帧丢弃最旧行）
    text = "| a | b |\n| --- | --- |\n" + "\n".join(
        f"| {i} | v{i} |" for i in range(320)) + "\n"
    m = _content_model(text, chunk=48)
    blk = m.blocks[m.content_block_index]
    width = 100
    incremental, ids_inc = _block_styled_lines(
        blk, blk.committed_line_count, width, with_ids=True)

    # 全量参考：临时移除缓存后重新构建
    saved = (
        blk._preview_rows_cache, blk._preview_styled_cache,
        blk._preview_styled_cache and dict(blk._preview_styled_cache),
    )
    blk._preview_rows_cache = None
    blk._preview_styled_cache = None
    try:
        full, ids_full = _block_styled_lines(
            blk, blk.committed_line_count, width, with_ids=True)
    finally:
        blk._preview_rows_cache = saved[0]
        blk._preview_styled_cache = saved[1]

    assert ["".join(r.text for r in runs) for runs in incremental] == [
        "".join(r.text for r in runs) for runs in full]
    assert len(ids_inc) == len(ids_full) == len(incremental)


def test_preview_rows_sliding_window_cache_bounded():
    """滑窗持续时预览行缓存不无界增长（spans 与源行数对齐）。"""
    m = AppModel()
    m.width = 100
    apply_cmd(m, ContentCmd(text="| a | b |\n| --- | --- |\n"))
    idx = m.content_block_index
    for i in range(600):
        apply_cmd(m, ContentCmd(text=f"| {i} | v{i} |\n"))
        blk = m.blocks[idx]
        _block_styled_lines(blk, blk.committed_line_count, 100, with_ids=True)
    blk = m.blocks[idx]
    cache = blk._preview_rows_cache
    assert cache is not None
    lines, _w, rows, ids, spans = cache
    assert len(spans) == len(lines)
    assert len(ids) == len(rows)
    # 预览行数上限 200（+ 省略提示）→ 缓存行数有界
    assert len(rows) < 400


# ── 3. wrap_runs_by_width 单行快路径等价性 ─────────────────


def _wrap_reference(runs, width):
    """参考实现：不超宽时单行（runs 原样）；否则逐字符贪心（简化版）。"""
    total = 0
    for r in runs:
        if "\n" in r.text:
            return None
        total += r.width
    return total <= width


@pytest.mark.parametrize("width", [20, 100])
def test_wrap_single_line_fast_path_matches_expected(width):
    runs = [StyledRun("abc", None), StyledRun("def", None)]
    out = _runs_utils.wrap_runs_by_width(runs, width)
    assert len(out) == 1
    assert [r.text for r in out[0].runs] == ["abc", "def"]


def test_wrap_fast_path_overwide_splits():
    runs = [StyledRun("abc", None), StyledRun("def", None)]
    out = _runs_utils.wrap_runs_by_width(runs, 5)
    assert ["".join(r.text for r in ln.runs) for ln in out] == ["abcde", "f"]


def test_wrap_fast_path_keeps_overwide_splitting():
    runs = [StyledRun("x" * 300, None)]
    out = _runs_utils.wrap_runs_by_width(runs, 40)
    assert len(out) > 1
    assert all(ln.width <= 40 for ln in out)
    assert "".join("".join(r.text for r in ln.runs) for ln in out) == "x" * 300


def test_wrap_fast_path_respects_newline():
    runs = [StyledRun("a\nb", None)]
    out = _runs_utils.wrap_runs_by_width(runs, 40)
    assert ["".join(r.text for r in ln.runs) for ln in out] == ["a", "b"]


def test_wrap_empty_runs_returns_empty():
    assert _runs_utils.wrap_runs_by_width([], 20) == []


# ── 4. 调和器纯 host 子树 bailout ─────────────────────────


def _render(rec, root, element, width=40):
    rec.render(root, element, width, 0)
    return _components.render_frame(root, width)


def test_host_subtree_bailout_skips_reconcile(monkeypatch):
    rec = Reconciler(schedule_callback=lambda: None)
    root = Reconciler.create_root()
    children = tuple(
        h(TEXT, {"key": f"k{i}", "styled": [StyledRun("row", None)]})
        for i in range(30)
    )
    _render(rec, root, h(BOX, None, children))

    calls = []
    orig = Reconciler._reconcile_children

    def spy(self, fiber, elements):
        calls.append(fiber.type)
        return orig(self, fiber, elements)

    monkeypatch.setattr(Reconciler, "_reconcile_children", spy)
    _render(rec, root, h(BOX, None, children))
    # 仅 root 自身调和（元素列表变化检测）；BOX 的子链（30 行）被整体跳过
    assert calls.count(BOX) == 0
    assert calls.count(TEXT) == 0


def test_host_subtree_with_function_child_still_reconciles(monkeypatch):
    """含函数组件的子树不跳过（可能有 state 更新，不能 bailout）。"""
    state = {"calls": 0}

    def Child(props):
        state["calls"] += 1
        return h(TEXT, {"children": "child"})

    rec = Reconciler(schedule_callback=lambda: None)
    root = Reconciler.create_root()
    children = (h(Child, {"key": "c"}), h(TEXT, {"key": "t", "children": "x"}))
    _render(rec, root, h(BOX, None, children))
    first = state["calls"]
    _render(rec, root, h(BOX, None, children))
    assert state["calls"] == first + 1


# ── 5. 代码块预览装饰行对象复用 ───────────────────────────


def test_code_preview_decor_lines_cached():
    r = AnsiStreamRenderer(width=80)
    assert r._preview_fence_line("python") is r._preview_fence_line("python")
    assert r._preview_title_line("a.py") is r._preview_title_line("a.py")
    assert r._preview_close_fence_line() is r._preview_close_fence_line()
    assert r._omitted_line(5) is r._omitted_line(5)


def test_code_preview_rows_stable_across_frames():
    """代码块预览输出行对象跨帧稳定（滑窗仅移动，元素复用）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    for i in range(220):
        r.write(f"v{i} = {i}\n")
    first = list(r.take_preview_lines())
    r.write("v9999 = 9999\n")
    second = list(r.take_preview_lines())
    # 装饰行（围栏）同一对象
    assert first[0] is second[0]
    # 窗口内重叠的代码行对象复用（尾部滑窗：first[-1] 在 second 中）
    assert any(a is b for a in first[-20:] for b in second)


# ── 6. 流式与一次性渲染一致（回归保护） ───────────────────


def test_stream_and_batch_render_equivalent_after_optimizations():
    text = ("## 标题\n\n段落 **粗体** 与 `code`。\n\n"
            + "```python\n" + "\n".join(f"v{i} = {i}" for i in range(60))
            + "\n```\n\n| a | b |\n| --- | --- |\n"
            + "".join(f"| {i} | {i ** 2} |\n" for i in range(20)))

    def render(chunk):
        r = AnsiStreamRenderer(width=80)
        for i in range(0, len(text), chunk):
            r.write(text[i:i + chunk])
            r.take_lines()
            r.take_preview_lines()
        r.close()
        return [ln.plain for ln in r.take_lines()]

    streamed = render(16)
    assert streamed == render(len(text))
