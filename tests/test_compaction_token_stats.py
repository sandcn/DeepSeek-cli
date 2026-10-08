"""压缩上下文（上下文压缩摘要）的 token 统计测试。

用户需求（2026-10）：压缩上下文消耗的 token（摘要调用的生成 token）必须
统计到状态栏的「总tok」与「tok/s」；压缩摘要是**非流式**调用，其 token 在
空闲期（手动 ``/compact``）产生，因此空闲期也要能看到这两个指标。

覆盖：
  1. ``_TokenSpeedTracker.add_token_size_batch``：总 tok 累加 + 该批次的真实
     平均速率回退（避免整批算进最后一个采样间隔而虚高、或随短窗口滑走归零）；
  2. 非流式调用（``api.model_async._call_sync_async``）以「已知耗时批量」记账；
  3. ``ContextManager.compact_now``（真实压缩链路，mock HTTP）→ 摘要输出 token
     计入全局总 tok 与 tok/s；
  4. 状态栏：压缩中 / 压缩结束宽限期内也展示 tokens 与 speed 段（空闲期可见），
     无压缩的空闲态与宽限期结束后不展示。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.core.stats._token_speed import _BATCH_RATE_TTL, _TokenSpeedTracker
from src.tui._const import CompactionCmd
from src.tui.app._state_types import StatusState
from src.tui.app.apply import _do_compaction
from src.tui.app.model import AppModel
from src.tui.app.status_bar import (
    _COMPACTION_TOKEN_GRACE_SEC,
    _build_status_runs,
    _tokens_visible,
)


@pytest.fixture
def tracker():
    return _TokenSpeedTracker()


@pytest.fixture(autouse=True)
def _clean_stats():
    """清理全局统计单例与状态栏快照缓存（测试间隔离）。"""
    from src.core.stats import reset_stats, reset_token_speed
    from src.tui.app import status_bar as sb

    reset_stats()
    reset_token_speed(keep_total=False)
    sb._snapshot_cache.clear()
    yield
    reset_stats()
    reset_token_speed(keep_total=False)
    sb._snapshot_cache.clear()


def _text(runs) -> str:
    return "".join(r.text for r in runs)


# ═══════════════════════════════════════════════════════════════
# 1. 批量生成记账（add_token_size_batch）
# ═══════════════════════════════════════════════════════════════


def test_batch_accumulates_total_and_reports_real_rate(tracker):
    """总 tok 一次性累加；tok/s 为该批次真实平均速率（不是一次性突刺）。"""
    tracker.add_token_size_batch(500, 10.0)
    assert tracker.total_tokens == 500
    assert tracker.per_second_speed == 50.0
    assert tracker.stats_snapshot()["per_second_speed"] == 50.0
    assert tracker.stats_snapshot()["total_tokens"] == 500


def test_batch_rate_visible_across_sampling_windows(tracker):
    """多次取快照不改变速率（窗口差值法会把整批算进最后间隔而虚高）。"""
    tracker.add_token_size_batch(3000, 30.0)
    for _ in range(5):
        tracker.stats_snapshot()
    assert tracker.per_second_speed == 100.0


def test_batch_rate_expires_after_ttl(tracker):
    """保鲜期过后不再回退批次速率（速率自然归零，不长期显示陈旧值）。"""
    tracker.add_token_size_batch(500, 10.0)
    assert tracker.per_second_speed == 50.0
    end_ts, size, seconds, total_after = tracker._last_batch
    tracker._last_batch = (end_ts - (_BATCH_RATE_TTL + 1.0), size, seconds, total_after)
    assert tracker.per_second_speed == 0.0


def test_batch_rate_dropped_when_new_tokens_arrive(tracker):
    """批次之后又有新 token（如新一轮流式生成）→ 不再回退旧批次速率。"""
    tracker.add_token_size_batch(500, 10.0)
    assert tracker.per_second_speed == 50.0
    tracker.add_token_size(20)
    assert tracker.per_second_speed != 50.0


def test_batch_ignores_non_positive_and_invalid_size(tracker):
    tracker.add_token_size_batch(0, 5.0)
    tracker.add_token_size_batch(-5, 5.0)
    tracker.add_token_size_batch("x", 5.0)
    tracker.add_token_size_batch(None, 5.0)
    assert tracker.total_tokens == 0


def test_batch_without_elapsed_degrades_to_plain_add(tracker):
    """耗时缺失/非正/非有限 → 退化为一次性计入（无速率回退）。"""
    tracker.add_token_size_batch(120, 0.0)
    tracker.add_token_size_batch(30, -3.0)
    tracker.add_token_size_batch(10, None)
    tracker.add_token_size_batch(10, "abc")
    tracker.add_token_size_batch(10, float("inf"))
    assert tracker.total_tokens == 180
    assert tracker._batch_rate_locked(time.time()) == 0.0


def test_reset_clears_batch_state(tracker):
    tracker.add_token_size_batch(500, 10.0)
    tracker.reset(keep_total=True)
    assert tracker.total_tokens == 500
    assert tracker.per_second_speed == 0.0
    assert tracker._last_batch is None


# ═══════════════════════════════════════════════════════════════
# 2. 非流式模型调用以「已知耗时批量」计入（压缩摘要走此路径）
# ═══════════════════════════════════════════════════════════════


def _fake_response():
    return {
        "choices": [{"message": {"role": "assistant", "content": "## Primary Request\n- summary body", "reasoning_content": ""}}],
        "usage": {
            "prompt_tokens": 4321, "completion_tokens": 66,
            "prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 4221,
        },
    }


@pytest.fixture
def _mock_http(monkeypatch):
    """mock 掉 model_async 的 HTTP 层（不产生真实请求）。"""
    import src.api.model_async as ma

    async def fake_chat(*args, **kwargs):
        return _fake_response()

    monkeypatch.setattr(ma, "chat_completions_async", fake_chat)
    monkeypatch.setattr(ma, "chat_completions_async_anthropic", fake_chat)
    return ma


def test_non_stream_call_counts_output_and_rate(_mock_http):
    from src.core.stats import get_per_second_speed, get_total_tokens
    from src.core.stats._token_speed import _token_speed

    ma = _mock_http
    _reasoning, _content, usage, _tools = ma.call_model_sync(
        [{"role": "user", "content": "hi"}], model="deepseek-flash", label="summarize",
    )
    assert usage["output"] == 66
    # 状态栏「总tok」计入该次生成的输出 token
    assert get_total_tokens() == 66
    # 记录为「已知耗时批量」→ tok/s 反映真实平均速率
    batch = _token_speed._last_batch
    assert batch is not None
    _end_ts, size, seconds, total_after = batch
    assert size == 66 and seconds > 0 and total_after == 66
    assert get_per_second_speed() == round(66 / seconds, 2)


# ═══════════════════════════════════════════════════════════════
# 3. 压缩链路端到端：摘要输出 token 计入状态栏统计
# ═══════════════════════════════════════════════════════════════


def _compaction_messages(n=20, content_len=400):
    messages = [{"role": "system", "content": "sys prompt"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}-" + "x" * content_len})
    return messages


def test_compact_now_counts_summary_tokens(_mock_http):
    """``/compact`` 摘要调用（真实链路 + mock HTTP）的 token 进入全局统计。"""
    from src.core.adapters.config import MockConfigAdapter
    from src.core.context_manager import ContextManager
    from src.core.stats import get_per_second_speed, get_token_stats, get_total_tokens

    cfg = MockConfigAdapter({
        "model_context_tokens": 2000, "provider": "deepseek",
        "compaction": {"threshold_ratio": 0.5, "headroom_tokens": 200,
                       "retain_ratio": 0.1, "model_policies": []},
    })
    cm = ContextManager(_compaction_messages(), "deepseek-flash", config_port=cfg)
    result = cm.compact_now()

    assert result is not None and result.success
    # 摘要输出 token 计入状态栏「总tok」与会话统计（input 只进 /cost 口径）
    assert get_total_tokens() == 66
    assert get_token_stats()["output"] == 66
    assert get_token_stats()["calls"] == 1
    # tok/s 为该次摘要生成的真实平均速率（保鲜期内可见，非 0）
    assert get_per_second_speed() > 0


# ═══════════════════════════════════════════════════════════════
# 4. 状态栏可见性（活跃期 / 压缩中 / 压缩结束宽限期）
# ═══════════════════════════════════════════════════════════════


def test_tokens_visible_when_active():
    st = StatusState(status_active=True)
    assert _tokens_visible(True, st, time.monotonic()) is True


def test_tokens_visible_while_compacting_at_idle():
    st = StatusState(status_active=False, compaction_active=1)
    assert _tokens_visible(False, st, time.monotonic()) is True


def test_tokens_visible_within_compaction_grace():
    now = time.monotonic()
    st = StatusState(status_active=False, compaction_last_end_ts=now - 1.0)
    assert _tokens_visible(False, st, now) is True


def test_tokens_hidden_after_grace_expires():
    now = time.monotonic()
    st = StatusState(
        status_active=False,
        compaction_last_end_ts=now - (_COMPACTION_TOKEN_GRACE_SEC + 0.5),
    )
    assert _tokens_visible(False, st, now) is False


def test_tokens_hidden_idle_without_compaction():
    st = StatusState(status_active=False)
    assert _tokens_visible(False, st, time.monotonic()) is False


def test_tokens_visible_defends_missing_fields():
    """测试桩状态对象缺字段时不抛异常（按未压缩处理）。"""
    assert _tokens_visible(False, SimpleNamespace(), time.monotonic()) is False


def test_do_compaction_records_end_timestamp():
    model = SimpleNamespace(status=StatusState())
    _do_compaction(model, CompactionCmd(active=2))
    assert model.status.compaction_active == 2
    assert model.status.compaction_last_end_ts == 0.0

    _do_compaction(model, CompactionCmd(active=0))
    assert model.status.compaction_active == 0
    assert model.status.compaction_last_end_ts > 0.0


def test_build_status_runs_shows_total_tokens_during_idle_compaction():
    """空闲期压缩中：状态栏渲染「总tok」（此前仅活跃期渲染 → 看不到）。"""
    from src.core.stats import add_token_size

    add_token_size(1234)
    m = AppModel()
    m.status.status_active = False
    m.status.compaction_active = 1
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "\u25c6" in text          # ◆ token 图标
    assert "1.2kt" in text


def test_build_status_runs_shows_speed_during_compaction_grace():
    """压缩结束宽限期内：状态栏渲染本次压缩的 tok/s（批次真实平均速率）。"""
    from src.core.stats import add_token_size_batch

    add_token_size_batch(500, 10.0)   # 50.0 tok/s
    m = AppModel()
    m.status.status_active = False
    m.status.compaction_active = 0
    m.status.compaction_last_end_ts = time.monotonic()
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "\u00bb" in text           # » 速度图标
    assert "50.0t/s" in text


def test_build_status_runs_hides_tokens_when_idle_without_compaction():
    from src.core.stats import add_token_size

    add_token_size(1234)
    m = AppModel()
    m.status.status_active = False
    m.status.compaction_active = 0
    m.status.compaction_last_end_ts = 0.0
    text = _text(_build_status_runs(m, 0.0, "\u00b7", ""))
    assert "\u25c6" not in text
    assert "\u00bb" not in text


def test_snapshot_ttl_shortens_during_compaction_display():
    """压缩驱动展示期用更短的快照 TTL（压缩刚结束即可见，不被旧快照拖慢）。"""
    from src.tui.app.status_bar import (
        _COMPACTION_SNAPSHOT_TTL,
        _SNAPSHOT_TTL,
    )

    assert _COMPACTION_SNAPSHOT_TTL < _SNAPSHOT_TTL


def test_snapshot_ttl_semantics_refetch_on_expiry():
    """快照 TTL 语义：TTL 内复用缓存，TTL 到期（含 ttl=0）立即重取。"""
    from src.core.stats import add_token_size
    from src.tui.app.status_bar import _SNAPSHOT_TTL, _snapshot

    m = AppModel()
    assert _snapshot(m, _SNAPSHOT_TTL)["total_tokens"] == 0
    add_token_size(42)
    # 长 TTL 内：仍是缓存的旧值（压缩期缓存的「总 tok=0」快照即此情形）
    assert _snapshot(m, _SNAPSHOT_TTL)["total_tokens"] == 0
    # 短 TTL（到期/0）：立即重取到新值
    assert _snapshot(m, 0.0)["total_tokens"] == 42


def test_compaction_finished_event_carries_saved_tokens():
    """压缩完成事件携带 saved_tokens（TUI 通知「节省 ~Xt」不再恒为 0）。"""
    from src.core.adapters.config import MockConfigAdapter
    from src.core.compaction.engine import CompactionEngine
    from src.core.context_manager import ContextManager

    events: list = []

    class _EventPort:
        def publish_event(self, event):
            events.append(event)

    def fake_summarize(msgs, model=None):
        return ("", "## Primary Request\n- condensed summary", {"input": 5, "output": 5}, [])

    cfg = MockConfigAdapter({
        "model_context_tokens": 2000, "provider": "deepseek",
        "compaction": {"threshold_ratio": 0.5, "headroom_tokens": 200,
                       "retain_ratio": 0.1, "model_policies": []},
    })
    cm = ContextManager(_compaction_messages(), "test-model",
                        summarize_fn=fake_summarize, config_port=cfg)
    engine = CompactionEngine(cm, cm._summarize_fn, cfg, event_port=_EventPort())
    result = engine.compact_now()

    assert result.success
    finished = [e for e in events if getattr(e, "phase", "") == "finished"]
    assert finished, "应发布 finished 事件"
    assert finished[-1].saved_tokens == result.saved_tokens > 0
