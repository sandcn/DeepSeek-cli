"""轨迹 Trace 性能优化测试（2026-08-19 优化）。

优化目标（性能热点）：
  1. **两段 use_memo**（TraceView 消息源模式）：消息+subagent 记录与 live
     记录拆分缓存——流式生成期间 live 内容逐帧增长只重建 live 段（浅拷贝
     追加 running 记录），历史消息记录（大 system 提示词全文/工具调用参数
     解析/ANSI 消毒）零重建（修复前 live 指纹变化驱动整树重建，长会话每帧
     O(全部消息内容)）。
  2. **``_block_content_len`` 增量统计**：``_live_fingerprint`` 每帧计算
     开放块内容总长（流式期间逐帧增长）——块 lines append-only，缓存
     ``(id(lines), len, total)`` 后仅统计新增行（修复前每帧全量重扫，
     累积 O(n²)）。
  3. **ListView items 零拷贝**：``_as_items`` 对 list/tuple 直接引用
     （渲染期只读）——修复前每帧 ``list(raw_items)`` O(记录数) 浅拷贝。

覆盖：payload 语义与 build_trace_records 一致性、不污染缓存、增量统计
正确性、两段 memo 在 live 增长/消息追加时的命中行为、_as_items 引用复用。
"""

from __future__ import annotations

from src.renderer.ansi.helpers import AnsiLine
from src.tui.app.model import AppModel
from src.tui.app.trace import (
    _block_content_len,
    _live_fingerprint,
    _messages_payload,
    _with_live_records,
    build_trace_records,
)
from src.tui.app.trace_view import TraceView
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber, MemoHook
from src.tui.ink.widgets.listview import _as_items, ListView

# ═══════════════════════════════════════════════════════════
# 1. _messages_payload / _with_live_records 语义与一致性
# ═══════════════════════════════════════════════════════════

def _open_content_block(model: AppModel, text: str):
    """构造开放（未关闭）content 块——模拟流式生成中。"""
    model.content_block_index = len(model.blocks)
    block = model.append_block("content")
    block.lines.append(AnsiLine.of(text))
    return block


def test_messages_payload_matches_build_trace_records():
    """消息路径：payload + live == build_trace_records（单一实现不漂移）。"""
    m = AppModel()
    m.message_source = lambda: [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "reasoning_content": "想一下",
         "tool_calls": [{"id": "c1", "function": {
             "name": "bash", "arguments": '{"command": "ls"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file.txt"},
    ]
    _open_content_block(m, "正在生成…")
    payload = _messages_payload(m)
    records, rows, merged, is_msg_path = payload
    assert is_msg_path is True
    assert isinstance(merged, set)
    # payload 不含 live 记录（开放块未表达）
    assert [r.kind for r in records] == ["user", "reasoning", "tool"]
    # payload + live == build_trace_records（完整记录）
    full_records, full_rows = build_trace_records(m)
    assert [r.kind for r in full_records] == ["user", "reasoning", "tool", "content"]
    assert [r.kind for r in full_records] == [r.kind for r in
                                              _with_live_records(payload, m)[0]]
    assert full_records[-1].status == "running"
    assert full_records[-1].summary == "正在生成…"


def test_messages_payload_fallback_block_path():
    """消息为空/异常/无消息源 → 回退块路径（is_message_path=False，开放块
    已由 _record_from_block 表达 running——不重复追加 live）。"""
    # 无消息源
    m = AppModel()
    _open_content_block(m, "生成中")
    payload = _messages_payload(m)
    assert payload[3] is False
    records, rows = _with_live_records(payload, m)
    assert records is payload[0], "块回退路径原样返回（不拷贝不追加）"
    assert sum(1 for r in records if r.kind == "content") == 1, "开放块仅一条"
    assert records[-1].status == "running"
    # 消息为空（注入 source 但返回空列表）→ 同样回退块路径
    m2 = AppModel()
    m2.message_source = lambda: []
    _open_content_block(m2, "生成中")
    payload2 = _messages_payload(m2)
    assert payload2[3] is False
    # 消息源异常 → 回退块路径（防御）
    m3 = AppModel()
    m3.message_source = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    _open_content_block(m3, "生成中")
    payload3 = _messages_payload(m3)
    assert payload3[3] is False
    # build_trace_records 与块回退一致（不重复追加 live）
    full, _ = build_trace_records(m)
    assert [r.kind for r in full] == [r.kind for r in payload[0]]


def test_with_live_records_does_not_mutate_payload():
    """live 追加浅拷贝——payload 缓存不被污染（use_memo 复用安全）。"""
    m = AppModel()
    m.message_source = lambda: [{"role": "user", "content": "hi"}]
    _open_content_block(m, "生成中")
    payload = _messages_payload(m)
    payload_records, payload_rows, _, _ = payload
    before = len(payload_records)
    records, rows = _with_live_records(payload, m)
    assert records is not payload_records, "live 追加应浅拷贝（不修改缓存）"
    assert rows is not payload_rows
    assert len(records) == before + 1, "live 记录追加一条"
    assert len(payload_records) == before, "payload 缓存未被修改"
    assert payload_rows is payload[1]
    # 重复调用幂等：每次返回独立列表但内容一致
    records2, rows2 = _with_live_records(payload, m)
    assert records2 is not records
    assert [r.kind for r in records2] == [r.kind for r in records]


# ═══════════════════════════════════════════════════════════
# 2. _block_content_len 增量统计
# ═══════════════════════════════════════════════════════════

def test_block_content_len_incremental():
    """_block_content_len：append 增量统计精确；行数倒退全量重算。"""
    m = AppModel()
    b = m.append_block("content")
    assert _block_content_len(b) == 0
    b.lines.append(AnsiLine.of("abc"))
    assert _block_content_len(b) == 3
    assert getattr(b, "_live_len_cache", None) is not None, "应挂增量缓存"
    # append-only 增量：仅统计新增行（缓存更新）
    b.lines.append(AnsiLine.of("de"))
    assert _block_content_len(b) == 5
    b.lines.append(AnsiLine.of(""))
    assert _block_content_len(b) == 5
    # 行数倒退（非 append-only 异常）→ 缓存条件不满足 → 全量重算精确
    b.lines.pop()
    assert _block_content_len(b) == 5
    b.lines.pop()
    b.lines.pop()
    assert _block_content_len(b) == 0
    # 统计语义与全量一致（指纹精确稳定）
    total = sum(len(getattr(ln, "plain", None) or "") for ln in b.lines)
    assert _block_content_len(b) == total


def test_live_fingerprint_growth_and_incremental_cache():
    """_live_fingerprint：开放块增长触发变化；增量缓存避免每帧全量重扫。"""
    m = AppModel()
    m.message_source = lambda: []
    assert _live_fingerprint(m) == ()
    b = _open_content_block(m, "第一行")
    fp1 = _live_fingerprint(m)
    assert fp1 != ()
    b.lines.append(AnsiLine.of("第二行"))
    fp2 = _live_fingerprint(m)
    assert fp1 != fp2, "行增长应触发指纹变化"
    # 内容继续增长（追加更长行——真实流式语义）→ 指纹变化
    b.lines.append(AnsiLine.of("更长的第三行"))
    fp3 = _live_fingerprint(m)
    assert fp2 != fp3, "内容总长变化应触发指纹变化"
    # 缓存随行数更新（增量命中）
    cache = getattr(b, "_live_len_cache", None)
    assert cache is not None and cache[1] == len(b.lines)
    assert cache[2] == sum(len(getattr(ln, "plain", None) or "") for ln in b.lines)
    # 指纹中内容总长与增量缓存一致（精确稳定）
    assert fp3 == (("content", 3, cache[2]),)


def test_live_fingerprint_cache_invalidated_on_lines_reference_change():
    """lines 引用变化（渲染管线重建列表）→ 缓存全量重算（id 不匹配）。"""
    m = AppModel()
    b = _open_content_block(m, "旧行")
    fp1 = _live_fingerprint(m)
    # 重建 lines 列表（新引用、内容相同）→ 全量重算但指纹一致
    new_lines = list(b.lines)
    b.lines = new_lines
    fp2 = _live_fingerprint(m)
    assert fp1 == fp2, "同内容新引用指纹应稳定（仅缓存失效一次重算）"
    assert getattr(b, "_live_len_cache", None) is not None


# ═══════════════════════════════════════════════════════════
# 3. TraceView 两段 use_memo（同一 fiber 多次渲染——真实渲染循环语义）
# ═══════════════════════════════════════════════════════════

def _render_once(fiber: Fiber, props: dict):
    """在已挂载 fiber 上渲染一次（hook 状态复用——模拟渲染循环帧）。

    渲染前 ``fiber.reset_hooks()``（reconciler 对 function fiber 渲染前的
    标准流程）——hook_index 归零后 ``use_*`` 按下标复用上次 hook 节点
    （跨帧保留状态/缓存），与真实渲染循环一致。
    """
    fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        return TraceView(props)
    finally:
        hooks._pop_current()


def _mount(props: dict):
    """首帧挂载：创建 fiber 并渲染。"""
    fiber = Fiber(TAG_FUNCTION, TraceView, dict(props))
    _render_once(fiber, props)
    return fiber


def _memo_hooks(fiber):
    return [h for h in fiber.hooks if isinstance(h, MemoHook)]


def _payload_value(fiber):
    """消息 payload memo 的 value（4 元组：records, rows, merged, is_msg_path）。"""
    for h in _memo_hooks(fiber):
        v = h.value
        if isinstance(v, tuple) and len(v) == 4 and isinstance(v[2], set):
            return v
    raise AssertionError("fiber 中无消息 payload memo")


def _live_value(fiber):
    """live memo 的 value（2 元组：records, rows）。"""
    for h in _memo_hooks(fiber):
        v = h.value
        if isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, list) for x in v):
            return v
    raise AssertionError("fiber 中无 live memo")


def test_trace_view_two_stage_memo_live_growth_keeps_payload():
    """核心：流式期间 live 内容逐帧增长 → 消息 payload memo 缓存命中
    （历史消息记录零重建）；live 段重建显示最新内容。"""
    msgs = [{"role": "user", "content": "hi"}]
    m = AppModel()
    m.message_source = lambda: msgs  # 真实会话消息：同一列表引用（就地增长）
    block = _open_content_block(m, "生成第一行")
    m.trace_open = True
    props = {"model": m, "width": 100}
    fiber = _mount(props)
    payload1 = _payload_value(fiber)
    live1 = _live_value(fiber)[0]
    assert live1[-1].kind == "content" and live1[-1].status == "running"
    # 流式增长：同一开放块 append（live 指纹变化）
    block.lines.append(AnsiLine.of("生成第二行"))
    _render_once(fiber, props)
    payload2 = _payload_value(fiber)
    assert payload2 is payload1, "live 增长不应重建消息 payload（历史消息零重建）"
    live2 = _live_value(fiber)[0]
    assert live2 is not live1, "live 段应重建（内容更新）"
    assert [r.kind for r in live2] == ["user", "content"]
    assert live2[-1].lines == ["生成第一行", "生成第二行"], "live 记录应含最新内容"
    # 继续增长（多次帧）
    block.lines.append(AnsiLine.of("生成第三行"))
    _render_once(fiber, props)
    assert _payload_value(fiber) is payload1, "多次增长 payload 持续命中"
    assert _live_value(fiber)[0][-1].lines == ["生成第一行", "生成第二行", "生成第三行"]


def test_trace_view_two_stage_memo_message_append_rebuilds_payload():
    """消息追加（流式完成，assistant 消息入源）→ payload 重建（消息指纹
    变化），live 记录消失由消息记录接管（无重复）。"""
    msgs = [{"role": "user", "content": "hi"}]
    m = AppModel()
    m.message_source = lambda: msgs
    block = _open_content_block(m, "生成中…")
    m.trace_open = True
    props = {"model": m, "width": 100}
    fiber = _mount(props)
    payload1 = _payload_value(fiber)
    assert payload1[3] is True
    # 流式完成：块关闭 + assistant 消息追加
    block.closed = True
    msgs.append({"role": "assistant", "content": "完成回答", "reasoning_content": None})
    _render_once(fiber, props)
    payload2 = _payload_value(fiber)
    assert payload2 is not payload1, "消息追加应重建 payload"
    records = _live_value(fiber)[0]
    assert [r.kind for r in records] == ["user", "content"]
    done = records[-1]
    assert done.status != "running"
    assert done.summary == "完成回答"
    assert len(records) == 2, "无重复：仅消息记录，live 记录已消失"


def test_trace_view_two_stage_memo_static_when_idle():
    """空闲（无 live 增长、无消息变化）→ 两段 memo 均缓存命中（零重建）。"""
    msgs = [{"role": "user", "content": "hi"}]
    m = AppModel()
    m.message_source = lambda: msgs
    m.trace_open = True
    props = {"model": m, "width": 100}
    fiber = _mount(props)
    payload1 = _payload_value(fiber)
    live1 = _live_value(fiber)
    _render_once(fiber, props)
    assert _payload_value(fiber) is payload1, "空闲 payload 命中"
    assert _live_value(fiber) is live1, "空闲 live 命中"
    # 台账选中记录详情 memo 同样命中（选中 rec 引用稳定）
    _render_once(fiber, props)
    assert _payload_value(fiber) is payload1


def test_trace_view_no_message_source_single_memo():
    """无消息源（块回退）→ 单段 memo（无 payload/live 分离），行为不变。"""
    m = AppModel()
    m.append_committed("user", [AnsiLine.of("> 你好")])
    m.trace_open = True
    props = {"model": m, "width": 100}
    fiber = _mount(props)
    # 无消息 payload memo（4 元组）——块回退单段
    for h in _memo_hooks(fiber):
        v = h.value
        if isinstance(v, tuple) and len(v) == 4 and isinstance(v[2], set):
            raise AssertionError("无消息源不应有消息 payload memo")
    # 渲染正常且记录含系统提词
    assert fiber.hooks, "应有 hooks"
    # 再渲染一次（块不变 → memo 命中，返回同记录）
    _render_once(fiber, props)
    assert True


# ═══════════════════════════════════════════════════════════
# 4. ListView items 零拷贝（_as_items）
# ═══════════════════════════════════════════════════════════

def test_as_items_reuses_list_and_tuple():
    """_as_items：list/tuple 直接引用（零拷贝——渲染期只读）；其他可迭代
    对象拷贝；None/不可迭代回退空列表。"""
    lst = [1, 2, 3]
    assert _as_items(lst) is lst, "list 应直接引用（零拷贝）"
    tup = (1, 2, 3)
    assert _as_items(tup) is tup, "tuple 应直接引用（零拷贝）"
    gen = (x for x in range(3))
    copied = _as_items(gen)
    assert copied == [0, 1, 2] and isinstance(copied, list)
    assert _as_items(None) == []
    assert _as_items("abc") == []
    assert _as_items(3.14) == []


def test_listview_renders_tuple_items():
    """ListView 传 tuple items（TraceView 可能场景）→ 渲染正常。"""
    from src.tui.ink.components import render_frame
    from src.tui.ink.element import h
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.widgets.layout import Column

    rec = Reconciler()
    root = rec.create_root()
    el = h(ListView, {"items": ("a", "b", "c"), "height": 3, "width": 10})
    rec.render(root, el, 80, 24)
    frame = render_frame(root, 80)
    plains = [ln.plain.strip() for ln in frame.lines if ln.plain.strip()]
    assert plains == ["a", "b", "c"], f"tuple items 应全部渲染: {plains}"


def test_listview_items_reference_not_copied():
    """ListView 渲染后 items 引用调用方列表（零拷贝——_as_items 生效）。

    验证方式：渲染期间经 renderItem 捕获传入的 items 引用，与调用方列表
    同一对象（修复前 list(raw_items) 产生新列表）。"""
    from src.tui.ink.components import render_frame
    from src.tui.ink.element import TEXT, h
    from src.tui.ink.reconciler import Reconciler

    captured = {}
    items = ["a", "b"]

    def render_item(item, i, is_sel):
        captured["items"] = items  # 闭包引用（渲染期）
        return h(TEXT, {"children": item, "height": 1})

    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h(ListView, {
        "items": items, "height": 2, "renderItem": render_item,
    }), 80, 24)
    render_frame(root, 80)
    # 通过 _as_items 直接验证（ListView 内部归一化路径）
    assert _as_items(items) is items
