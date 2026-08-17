"""轨迹 Trace 渲染性能优化测试（O(N²) 相关，2026-08-19）。

覆盖本次 O(N²) → O(N)/O(1) 优化点：
  1. ``trace_view._rows_index`` 台账行预计算索引——分隔行编号/记录↔行映射/
     轮次数一次 O(N) 预计算、跨帧 O(1) 查表（修复前 ``_ledger_renderer`` 的
     ``sum(1 for r in rows[:idx] if r is None)`` 对每个可见分隔行每帧 O(idx)
     扫描 + 切片分配 → 大台账 O(N×视口) ≈ O(N²)；``_row_of_record`` 每帧
     O(N) 线性扫描；``turn_count`` 每帧 O(N) 全量扫描）；
  2. ``trace._tool_block_text`` 工具块返回预览/全文**增量缓存**（块回退路径
     长工具输出每帧全量过滤 + join O(行数) → 仅拼接新增行）；
  3. ``trace._live_tool_payload`` 运行中工具详情/全文**增量缓存**（消息源
     模式运行中工具输出每帧全量构建 O(行数) → 仅拼接新增行）；
  4. ``trace._merge_call_lines`` 工具调用+返回合并列表缓存（历史 tool 返回
     消息每帧重建合并列表 O(返回行数) → 内容不变零重建）；
  5. ``listview`` items 已为 list 时直接引用（修复前每帧 ``list(raw_items)``
     复制整个台账 O(N)）。
"""

from __future__ import annotations

from src.renderer.ansi.helpers import AnsiLine
from src.tui._input_parser import KeyEvent
from src.tui.app._state_types import ChatBlock
from src.tui.app.model import AppModel
from src.tui.app.trace import (
    TraceRecord,
    _live_records,
    _live_tool_payload,
    _merge_call_lines,
    _records_from_messages,
    _tool_block_text,
)
from src.tui.app.trace_view import (
    _ledger_renderer,
    _records_index_of_row,
    _row_of_record,
    _rows_index,
)
from src.tui.ink.components import render_frame
from src.tui.ink.element import TEXT, h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.widgets.listview import ListView


def _render(element, width: int = 80, height: int = 24):
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, element, width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list:
    return [ln.plain for ln in frame.lines]


def _sep_text(render_item, idx: int) -> str:
    """取分隔行 TEXT 元素的纯文本（``── 轮次 N ──``）。"""
    el = render_item(None, idx, False)
    runs = el.props.get("styled") or []
    return "".join(r.text for r in runs)


def _make_tool_block(lines, extra=None, closed=True) -> ChatBlock:
    """构造工具块（标题行 + 输出行 + 可选状态行）。"""
    b = ChatBlock("tool")
    b.lines = [AnsiLine.of(l) for l in lines]
    if extra:
        b.extra.update(extra)
    b.closed = closed
    return b


# ═══════════════════════════════════════════════════════════
# 1. _rows_index 台账行预计算索引（O(N²) → O(1) 查表）
# ═══════════════════════════════════════════════════════════

def test_rows_index_sep_nums_and_mappings():
    """分隔行编号 / 记录↔行映射 / 行→记录索引 三表正确（多轮次场景）。"""
    r1 = TraceRecord(index=1, kind="user", summary="u1")
    r2 = TraceRecord(index=2, kind="content", summary="c1")
    r3 = TraceRecord(index=3, kind="user", summary="u2")
    rows = [r1, None, r2, None, None, r3]
    sep_nums, rec_to_row, row_to_rec = _rows_index(rows)
    assert sep_nums == {1: 1, 3: 2, 4: 3}
    assert rec_to_row[id(r1)] == 0
    assert rec_to_row[id(r2)] == 2
    assert rec_to_row[id(r3)] == 5
    assert row_to_rec == [0, -1, 1, -1, -1, 2]


def test_rows_index_cache_hit_same_reference():
    """同 rows 引用（use_memo 命中）→ 命中缓存返回同一索引对象（零重建）。"""
    r = TraceRecord(index=1, kind="user", summary="u")
    rows = [r, None]
    idx1 = _rows_index(rows)
    idx2 = _rows_index(rows)
    assert idx1 is idx2


def test_rows_index_rebuilds_on_new_reference():
    """新 rows 引用（records 重建）→ 重建索引（一次性 O(N)，结果正确）。"""
    r = TraceRecord(index=1, kind="user", summary="u")
    rows1 = [r, None]
    rows2 = [r, None]  # 内容相同但新 list 引用
    idx1 = _rows_index(rows1)
    idx2 = _rows_index(rows2)
    assert idx1 is not idx2
    assert idx2[0] == {1: 1}
    assert idx2[1][id(r)] == 0
    assert idx2[2] == [0, -1]


def test_row_of_record_uses_mapping():
    """_row_of_record O(1) 查表：记录索引 → 台账行下标（分隔行跳过）。"""
    r1 = TraceRecord(index=1, kind="user", summary="u1")
    r2 = TraceRecord(index=2, kind="content", summary="c1")
    rows = [r1, None, r2]
    records = [r1, r2]
    assert _row_of_record(rows, 0, records) == 0
    assert _row_of_record(rows, 1, records) == 2
    assert _row_of_record(rows, -1, records) == 0  # 越界 → 0（防御）
    assert _row_of_record(rows, 5, records) == 0


def test_records_index_of_row_uses_mapping():
    """_records_index_of_row O(1) 查表：台账行下标 → 记录索引（分隔行 -1）。"""
    r1 = TraceRecord(index=1, kind="user", summary="u1")
    r2 = TraceRecord(index=2, kind="content", summary="c1")
    rows = [r1, None, r2]
    assert _records_index_of_row(rows, 0) == 0
    assert _records_index_of_row(rows, 1) == -1   # 分隔行
    assert _records_index_of_row(rows, 2) == 1
    assert _records_index_of_row(rows, -1) == -1  # 越界
    assert _records_index_of_row(rows, 99) == -1


def test_ledger_renderer_sep_numbering_uses_index():
    """分隔行 renderItem O(1) 查表：多轮次下轮次编号正确（修复前逐行扫描）。"""
    r1 = TraceRecord(index=1, kind="user", summary="u1")
    r2 = TraceRecord(index=2, kind="content", summary="c1")
    r3 = TraceRecord(index=3, kind="user", summary="u2")
    rows = [r1, None, r2, None, None, r3]
    render_item = _ledger_renderer(rows, 40, [r1, r2, r3], None)
    assert "轮次 1" in _sep_text(render_item, 1)
    assert "轮次 2" in _sep_text(render_item, 3)
    assert "轮次 3" in _sep_text(render_item, 4)
    # 记录行仍正常渲染（摘要文本）
    el = render_item(r1, 0, False)
    runs = el.props.get("styled") or []
    assert any("u1" in r.text for r in runs)


# ═══════════════════════════════════════════════════════════
# 2. _tool_block_text 工具块返回增量缓存（O(N²) → O(新增)）
# ═══════════════════════════════════════════════════════════

def test_tool_block_text_incremental():
    """块行追加 → 第二次调用仅拼接新增行（预览/全文正确，缓存计数推进）。"""
    b = _make_tool_block(
        ["  \u00b7 Bash \u00b7 ls -la", "out1", "out2", "  \u2714"],
        {"_status_line_index": 3},
    )
    preview, res = _tool_block_text(b)
    assert preview == "out1"
    assert res == "out1\nout2"
    assert b._trace_tool_text_cache[3] == 4  # 已处理 4 行（含标题/状态跳过）
    # 追加输出行 → 增量拼接
    b.lines.append(AnsiLine.of("out3"))
    preview2, res2 = _tool_block_text(b)
    assert preview2 == "out1"          # 预览不变（首个非空行）
    assert res2 == "out1\nout2\nout3"
    assert b._trace_tool_text_cache[3] == 5


def test_tool_block_text_rebuilds_on_status_change():
    """状态行下标变化 → 全量重建（防御非 append-only，结果正确）。"""
    b = _make_tool_block(
        ["  \u00b7 Bash \u00b7 ls", "out1", "out2", "  \u2714"],
        {"_status_line_index": 3},
    )
    preview, res = _tool_block_text(b)
    assert res == "out1\nout2"
    # 状态行下标提前到 1（行序列变化）→ ckey 变化 → 全量重建（跳过新位置，
    # 原状态行“  ✔”按普通输出行保留）
    b.extra["_status_line_index"] = 1
    preview2, res2 = _tool_block_text(b)
    assert preview2 == "out2"
    assert res2 == "out2\n  \u2714"


def test_tool_block_text_empty_lines():
    """无输出行（仅标题行）→ 空预览 + 空全文。"""
    b = _make_tool_block(["  \u00b7 Bash \u00b7 ls"], {"_status_line_index": 0})
    preview, res = _tool_block_text(b)
    assert preview == ""
    assert res == ""


# ═══════════════════════════════════════════════════════════
# 3. _live_tool_payload 运行中工具增量缓存（O(N²) → O(新增)）
# ═══════════════════════════════════════════════════════════

def test_live_tool_payload_incremental_shared():
    """box 行追加 → 增量拼接；共享列表/字符串引用（检查器 use_memo 命中）。"""
    box = ChatBlock("tool")
    box.lines.append(AnsiLine.of("  \u00b7 bash \u00b7 ls"))
    box.lines.append(AnsiLine.of("out1"))
    lines, res = _live_tool_payload(box, "bash ls")
    assert lines == ["bash ls", "out1"]
    assert res == "out1"
    # 追加输出行 → 增量（返回同一列表引用——rec.lines 跨帧 id 稳定）
    box.lines.append(AnsiLine.of("out2"))
    lines2, res2 = _live_tool_payload(box, "bash ls")
    assert lines2 is lines
    assert lines2 == ["bash ls", "out1", "out2"]
    assert res2 == "out1\nout2"


def test_live_tool_payload_rebuilds_on_call_change():
    """call 变化（工具详情更新）→ 全量重建。"""
    box = ChatBlock("tool")
    box.lines.append(AnsiLine.of("  \u00b7 bash \u00b7 ls"))
    box.lines.append(AnsiLine.of("out1"))
    lines1, _ = _live_tool_payload(box, "bash ls")
    lines2, _ = _live_tool_payload(box, "bash ls -la")
    assert lines2 is not lines1
    assert lines2 == ["bash ls -la", "out1"]


def test_live_records_uses_incremental_tool_payload():
    """_live_records 运行中工具记录：详情行/全文经增量缓存（结果正确）。"""
    m = AppModel()
    box = m.open_tool_box("t1", "bash", "ls")
    box.lines.append(AnsiLine.of("out1"))
    records, rows = [], []
    _live_records(m, [0], records, rows)
    tool = records[0]
    assert tool.kind == "tool"
    assert tool.status == "running"
    assert tool.summary == "bash ls"
    assert tool.lines == ["bash ls", "out1"]
    assert tool.tool_result == "out1"
    assert tool.result == "out1"
    assert rows == records
    # 输出增长 → 记录内容更新（增量）
    box.lines.append(AnsiLine.of("out2"))
    records2, rows2 = [], []
    _live_records(m, [0], records2, rows2)
    tool2 = records2[0]
    assert tool2.lines == ["bash ls", "out1", "out2"]
    assert tool2.tool_result == "out1\nout2"


# ═══════════════════════════════════════════════════════════
# 4. _merge_call_lines 工具调用+返回合并列表缓存（O(N²) → O(1)）
# ═══════════════════════════════════════════════════════════

def test_merge_call_lines_cache_hit():
    """同 (调用行, 返回全文) → 同一合并列表引用（每帧零重建）。"""
    lines1 = _merge_call_lines("bash ls", "a\nb", ["a", "b"])
    lines2 = _merge_call_lines("bash ls", "a\nb", ["a", "b"])
    assert lines1 is lines2
    assert lines1 == ["bash ls", "a", "b"]


def test_merge_call_lines_cache_miss_on_change():
    """返回内容变化 → 新合并列表。"""
    lines1 = _merge_call_lines("bash ls", "a\nb", ["a", "b"])
    lines2 = _merge_call_lines("bash ls", "a\nb\nc", ["a", "b", "c"])
    assert lines2 is not lines1
    assert lines2 == ["bash ls", "a", "b", "c"]


def test_records_from_messages_tool_merge_lines_cached():
    """消息源模式 tool 调用+返回合并：跨调用同内容共享合并列表引用。"""
    messages = [
        {"role": "user", "content": "请执行"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "t1", "function": {"name": "bash", "arguments": "ls"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "out_a\nout_b"},
    ]
    records1, _ = _records_from_messages(messages)
    records2, _ = _records_from_messages(messages)
    tool1 = next(r for r in records1 if r.kind == "tool")
    tool2 = next(r for r in records2 if r.kind == "tool")
    assert tool1.lines == ["bash ls", "out_a", "out_b"]
    assert tool1.lines is tool2.lines  # 内容不变 → 缓存命中共享引用
    assert tool1.result == "out_a"
    assert tool1.tool_result == "out_a\nout_b"


# ═══════════════════════════════════════════════════════════
# 5. ListView items list 直接引用（修复前每帧 list() 复制 O(N)）
# ═══════════════════════════════════════════════════════════

def test_listview_list_items_render_unchanged():
    """items 为 list 时直接引用（不复制）——渲染结果与旧路径一致。"""
    items = ["a", "b", "c"]
    el = h(ListView, {
        "items": items, "height": 5,
        "renderItem": lambda item, i, is_sel=None: h(TEXT, {"children": str(item), "height": 1}),
    })
    _, _, frame = _render(el)
    plain = _frame_plain(frame)
    assert "a" in plain and "c" in plain


def test_listview_generator_items_still_listified():
    """生成器/可迭代 items 仍 list() 化（防御路径保持）。"""
    el = h(ListView, {
        "items": (x for x in ["g1", "g2"]), "height": 5,
        "renderItem": lambda item, i, is_sel=None: h(TEXT, {"children": str(item), "height": 1}),
    })
    _, _, frame = _render(el)
    plain = _frame_plain(frame)
    assert "g1" in plain and "g2" in plain


def test_listview_tuple_items_unchanged():
    """tuple items 行为保持（list() 化后渲染）。"""
    el = h(ListView, {
        "items": ("t1", "t2"), "height": 5,
        "renderItem": lambda item, i, is_sel=None: h(TEXT, {"children": str(item), "height": 1}),
    })
    _, _, frame = _render(el)
    plain = _frame_plain(frame)
    assert "t1" in plain and "t2" in plain


def test_listview_arrow_navigation_works_with_list_items():
    """list items 直接引用后导航仍正常（↑↓ 移动光标、跳过分隔行）。"""
    items = ["a", None, "b"]
    el = h(ListView, {
        "items": items, "height": 5,
        "renderItem": lambda item, i, is_sel=None: h(TEXT, {
            "children": ("\u25b6" if is_sel else " ") + str(item), "height": 1,
        }),
    })
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    # ↓ → 光标从 0（a）跳到 2（b，跳过 None 分隔行）
    assert router(KeyEvent(kind="arrow_down")) is True
    rec.render(root, el, 80, 24)
    frame = render_frame(root, 80)
    plain = _frame_plain(frame)
    assert any(p.startswith("\u25b6") and "b" in p for p in plain)
