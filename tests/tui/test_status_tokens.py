"""状态行 token 段 — 全 Agent 输入总量与快照合并测试。"""

from __future__ import annotations

from types import SimpleNamespace

import src.core.stats as stats
import src.tui._snapshot as snapshot_mod
from src.tui.app.status_bar import StatusContext, _build_status_items, _tokens_segment


def _ctx(snapshot: dict, active: bool = False) -> StatusContext:
    return StatusContext(
        model=SimpleNamespace(),
        status=SimpleNamespace(),
        status_active=active,
        dot_elapsed=0.0,
        spinner_char="\u00b7",
        reasoning_effort="",
        snapshot=snapshot,
        tokens_visible=False,
    )


def test_tokens_segment_shows_input_and_output_together():
    runs = _tokens_segment(_ctx({
        "total_input_tokens": 1_234_567,
        "total_tokens": 3400,
    }))
    text = "".join(run.text for run in runs)
    assert text == "\u25c6 \u5165 1.2Mt \u00b7 \u51fa 3.4kt"


def test_tokens_segment_is_persistent_when_idle():
    """tokens_visible=False（空闲）时仍显示累计输入/输出。"""
    runs = _tokens_segment(_ctx({
        "total_input_tokens": 1234,
        "total_tokens": 0,
    }))
    assert "".join(run.text for run in runs) == "\u25c6 \u5165 1.2kt"


def test_tokens_segment_omits_unavailable_input():
    """输入统计不可用（0）时只显示出，不显示误导性的「入 0t」。"""
    runs = _tokens_segment(_ctx({"total_input_tokens": 0, "total_tokens": 3400}))
    assert "".join(run.text for run in runs) == "\u25c6 \u51fa 3.4kt"


def test_tokens_segment_hidden_without_any_tokens():
    assert _tokens_segment(_ctx({})) == []


def test_build_snapshot_merges_all_agent_input_tokens(monkeypatch):
    monkeypatch.setattr(stats, "get_token_speed_snapshot", lambda: {
        "total_tokens": 3400,
        "per_second_speed": 9.5,
    })
    monkeypatch.setattr(stats, "get_total_agent_input_tokens", lambda: 1_234_567)
    # 计费口径 raw input 不应被展示快照使用（费用/缓存明细另走 stats 快照）。
    monkeypatch.setattr(stats, "get_total_input_tokens", lambda: 9_999_999)

    data = snapshot_mod._build_snapshot()
    assert data["total_tokens"] == 3400
    assert data["per_second_speed"] == 9.5
    assert data["total_input_tokens"] == 1_234_567


def test_build_snapshot_falls_back_when_input_stats_unavailable(monkeypatch):
    monkeypatch.setattr(stats, "get_token_speed_snapshot", lambda: {"total_tokens": 10})

    def _boom():
        raise RuntimeError("stats unavailable")

    monkeypatch.setattr(stats, "get_total_agent_input_tokens", _boom)

    data = snapshot_mod._build_snapshot()
    assert data["total_tokens"] == 10
    assert data["total_input_tokens"] == 0


def test_build_status_items_fetches_snapshot_when_idle(monkeypatch):
    """空闲状态也要查询快照，保证输入 tok 常驻。"""
    import src.tui.app._status_segments as segments
    import src.tui.app.status_bar as status_bar

    calls: list[float] = []

    def _snapshot(_model, ttl):
        calls.append(ttl)
        return {"total_input_tokens": 1234, "total_tokens": 0}

    monkeypatch.setattr(status_bar, "_snapshot", _snapshot)
    monkeypatch.setattr(segments, "active_segment_ids", lambda: ["tokens"])
    monkeypatch.setattr(segments, "resolve_segment", lambda _sid: status_bar._tokens_segment)

    built = _build_status_items(SimpleNamespace(status=SimpleNamespace(status_active=False)))
    text = "".join(run.text for item in built.items for run in item)
    assert "\u5165 1.2kt" in text
    assert calls  # 空闲也查询了快照
