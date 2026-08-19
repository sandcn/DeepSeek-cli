"""src/core/compression — 上下文压缩策略单元测试。

覆盖：
  - CompressionResult（默认值、__post_init__ 归一化）
  - SummarizeStrategy.compress：成功路径 / 无候选 / 异常降级
  - _detect_prior_summary / _apply_summary / _report_success
  - DropStrategy.compress：force 全删 / 非 force 超额删除 / 无候选 / 无超额
  - _collect_unpinned（pinned/系统提示词保护）
所有 selector/summarizer/cache 依赖均被 mock。
"""

from __future__ import annotations

import pytest

import src.core.compression as comp
from src.core.compression import (
    CompressionResult,
    CompressionStrategy,
    DropStrategy,
    SummarizeStrategy,
)


# ── CompressionResult ────────────────────────────────────

def test_result_defaults():
    r = CompressionResult()
    assert r.success is True
    assert r.removed_indices == []
    assert r.inserted_message is None
    assert r.chars_saved == 0
    assert r.stats == {}


def test_result_normalizes_none_lists():
    r = CompressionResult(removed_indices=None, stats=None)
    assert r.removed_indices == []
    assert r.stats == {}


def test_compression_strategy_abstract():
    with pytest.raises(TypeError):
        CompressionStrategy()  # type: ignore[abstract]


# ── SummarizeStrategy ────────────────────────────────────

class _FakeCache:
    """模拟增量统计缓存。"""

    def __init__(self, total_chars=1000, total_tokens=100):
        self.total_chars = total_chars
        self.total_tokens = total_tokens
        self.is_valid = True
        self.removed = []
        self.inserted = []

    def on_remove(self, indices):
        self.removed.extend(indices)

    def on_insert(self, index, msg):
        self.inserted.append((index, msg))


def _summarize_ctx(monkeypatch):
    """mock selector/summarizer，返回可注入的控制对象。"""
    state = {
        "keep": 5,
        "to_compress": [3, 4],
        "total": 1000,
        "summary": "对话摘要内容",
        "usage": {"input": 100, "output": 50},
    }

    monkeypatch.setattr(comp.selector, "adjust_keep_for_tool_groups", lambda msgs: state["keep"])
    monkeypatch.setattr(comp.selector, "total_chars", lambda msgs: state["total"])
    monkeypatch.setattr(
        comp.selector, "select_for_compression",
        lambda msgs, keep, force, tc, tt: list(state["to_compress"]),
    )
    monkeypatch.setattr(
        comp.summarizer, "summarize",
        lambda msgs, has_prior, fn, model: (state["summary"], state["usage"]),
    )
    return state


def test_summarize_success_path(monkeypatch):
    state = _summarize_ctx(monkeypatch)
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
        {"role": "user", "content": "recent"},
    ]
    cache = _FakeCache(total_chars=1000)
    events = []

    result = SummarizeStrategy().compress(
        messages, "m", summarize_fn=lambda *a, **k: None,
        on_changed=lambda ev: events.append(ev), cache=cache, force=False,
    )
    assert result.success is True
    assert result.removed_indices == [3, 4]
    assert result.inserted_message["role"] == "system"
    assert "[对话摘要]" in result.inserted_message["content"]
    # 消息被替换为摘要（2 条删除 + 1 条插入 → 原 6 条变 5 条）
    assert len(messages) == 5
    assert any("[对话摘要]" in m.get("content", "") for m in messages)
    # 回调顺序：remove → insert
    assert events[0]["type"] == "remove"
    assert events[1]["type"] == "insert"
    # 缓存更新
    assert sorted(cache.removed) == [3, 4]
    assert cache.inserted[0][1]["content"].startswith("[对话摘要]")


def test_summarize_no_candidates(monkeypatch):
    state = _summarize_ctx(monkeypatch)
    state["to_compress"] = []
    result = SummarizeStrategy().compress(
        [{"role": "user", "content": "x"}], "m", None, None, None, False,
    )
    assert result.success is False
    assert result.stats["reason"] == "no_candidates"


def test_summarize_exception_degrades(monkeypatch):
    state = _summarize_ctx(monkeypatch)

    def boom(msgs, has_prior, fn, model):
        raise ValueError("模型返回空摘要")

    monkeypatch.setattr(comp.summarizer, "summarize", boom)
    messages = [{"role": "user", "content": f"m{i}"} for i in range(6)]
    result = SummarizeStrategy().compress(
        messages, "m", None, None, None, False,
    )
    assert result.success is False
    assert "error" in result.stats


def test_detect_prior_summary_finds_in_range():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "[对话摘要] 旧"},
        {"role": "user", "content": "x"},
    ]
    has_prior, indices = SummarizeStrategy._detect_prior_summary(messages, [1], keep_recent=10)
    assert has_prior is True
    assert indices == [1]


def test_detect_prior_summary_expands_search():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "[对话摘要] 旧"},
        {"role": "user", "content": "x"},
    ]
    has_prior, indices = SummarizeStrategy._detect_prior_summary(messages, [2], keep_recent=2)
    # boundary = max(1, 3-2)=1 → 搜索范围 range(1,1) 为空 → 不扩展
    assert has_prior is False
    assert indices == [2]


def test_detect_prior_summary_expands_when_found():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "[对话摘要] 旧"},
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
    ]
    has_prior, indices = SummarizeStrategy._detect_prior_summary(messages, [3], keep_recent=3)
    # boundary = max(1, 4-3)=1 → range(1,1) 空；keep_recent=2 → boundary=2 → range(1,2) 含 idx1
    has_prior2, indices2 = SummarizeStrategy._detect_prior_summary(messages, [3], keep_recent=2)
    assert has_prior2 is True
    assert indices2 == [1, 3]


def test_apply_summary_inserts_after_system(monkeypatch):
    messages = [
        {"role": "system", "content": "提示1"},
        {"role": "system", "content": "提示2"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    cache = _FakeCache()
    SummarizeStrategy._apply_summary(messages, [2, 3], "摘要", None, cache)
    # 2 条删除 + 1 条摘要插入在 system 之后
    assert len(messages) == 3
    assert messages[2]["content"].startswith("[对话摘要]")
    assert sorted(cache.removed) == [2, 3]


def test_report_success_on_info(monkeypatch):
    infos = []
    monkeypatch.setattr(comp.selector, "total_chars", lambda msgs: 500)
    SummarizeStrategy._report_success(
        [2, 3], 1000, _FakeCache(total_chars=500),
        {"input": 100, "output": 50}, 0.5, [{"role": "user", "content": "x"}],
        on_info=infos.append,
    )
    assert infos and "压缩 2 条" in infos[0]
    assert "100/50t" in infos[0]


def test_safe_notify_catches_exception():
    def boom(ev):
        raise RuntimeError("回调挂了")

    CompressionStrategy._safe_notify(boom, {"type": "x"})  # 不抛异常


# ── DropStrategy ─────────────────────────────────────────

def _drop_msgs():
    return [
        {"role": "system", "content": "sys"},     # 0：系统提示词（保护）
        {"role": "user", "content": "a"},         # 1
        {"role": "assistant", "content": "b"},    # 2
        {"role": "user", "content": "c", "pinned": True},  # 3：pinned（保护）
        {"role": "system", "content": "[对话摘要] old"},   # 4：旧摘要（可删）
    ]


def test_collect_unpinned():
    msgs = _drop_msgs()
    indices = DropStrategy._collect_unpinned(msgs)
    assert indices == [1, 2, 4]  # 跳过 idx0 系统提示词与 idx3 pinned


def test_drop_all_force(monkeypatch):
    msgs = _drop_msgs()
    cache = _FakeCache(total_chars=1000)
    monkeypatch.setattr(comp.selector, "total_chars", lambda msgs: 1000)
    events = []

    result = DropStrategy().compress(
        msgs, "m", None, lambda ev: events.append(ev), cache, force=True,
    )
    assert result.success is True
    assert result.stats["mode"] == "force_drop"
    assert result.removed_indices == [1, 2, 4]
    # 剩余：系统提示词 + pinned 消息 + 摘要消息被删
    remaining_roles = [m["role"] for m in msgs]
    assert remaining_roles == ["system", "user"]
    assert events and events[0]["type"] == "remove"


def test_drop_no_unpinned():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "p", "pinned": True},
    ]
    result = DropStrategy().compress(msgs, "m", None, None, None, force=True)
    assert result.success is False
    assert result.stats["reason"] == "no_unpinned"


def test_drop_excess(monkeypatch):
    msgs = _drop_msgs()
    cache = _FakeCache(total_chars=1000)
    monkeypatch.setattr(comp.selector, "total_chars", lambda msgs: 1000)
    monkeypatch.setattr(comp.selector, "calc_excess_chars_values", lambda tc, tt: 300)

    class _PerMsg:
        def get_per_msg(self, idx):
            return (400 if idx == 1 else 200, 0)

    cache.get_per_msg = _PerMsg().get_per_msg  # type: ignore[method-assign]
    result = DropStrategy().compress(msgs, "m", None, None, cache, force=False)
    assert result.success is True
    assert result.stats["mode"] == "excess_drop"
    # 从后往前删除：idx4(200) + idx2(200) = 400 >= 300 → 删 [2, 4]
    assert result.removed_indices == [2, 4]


def test_drop_excess_no_need(monkeypatch):
    msgs = _drop_msgs()
    monkeypatch.setattr(comp.selector, "calc_excess_chars_values", lambda tc, tt: 0)
    result = DropStrategy().compress(msgs, "m", None, None, None, force=False)
    assert result.success is False
    assert result.stats["reason"] == "no_excess"
