"""压缩引擎 + ContextManager 集成单元测试（dsh 同款机制）。

覆盖：自动压力压缩（阈值判定 / 保留尾部）、手动压缩、工具结果剪枝的
「仅剪枝」路径、检查点消息落地、无安全范围与禁用开关。
"""

from __future__ import annotations

import pytest

from src.core.adapters.config import MockConfigAdapter
from src.core.compaction.checkpoint import SUMMARY_OPEN_TAG
from src.core.context_manager import ContextManager


def _messages(n=20, content_len=400):
    messages = [{"role": "system", "content": "sys prompt"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}-" + "x" * content_len})
    return messages


def _config_port(**overrides):
    compaction = {
        "threshold_ratio": 0.5,
        "headroom_tokens": 200,
        "retain_ratio": 0.1,
        "model_policies": [],
    }
    compaction.update(overrides.pop("compaction", {}))
    data = {"model_context_tokens": 2000, "provider": "deepseek",
            "compaction": compaction}
    data.update(overrides)
    return MockConfigAdapter(data)


def _make_cm(messages=None, config_port=None):
    messages = messages if messages is not None else _messages()
    calls = []

    def fake_summarize(msgs, model=None):
        calls.append(msgs)
        return ("", "## Primary Request and Intent\n- condensed summary", {"input": 5, "output": 5}, [])

    cm = ContextManager(
        messages, "test-model", summarize_fn=fake_summarize,
        config_port=config_port or _config_port(),
    )
    cm._summaries = calls
    return cm


# ── 自动压力压缩 ──────────────────────────────────────────

def test_auto_compaction_replaces_history_with_checkpoint():
    cm = _make_cm()
    messages = cm.messages
    before = len(messages)

    cm.check_and_compress()

    assert cm._summaries, "应发起摘要调用"
    assert len(messages) < before
    assert any(SUMMARY_OPEN_TAG in str(m.get("content", "")) for m in messages)
    # 系统提示词仍在首条
    assert messages[0]["role"] == "system"
    assert "sys prompt" in messages[0]["content"]


def test_auto_compaction_skips_below_threshold():
    # 小窗口但极短消息 → 未达阈值
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"}]
    cm = _make_cm(messages=messages)
    cm.check_and_compress()
    assert cm._summaries == []
    assert len(messages) == 4


def test_auto_compaction_disabled():
    cm = _make_cm(config_port=_config_port(compaction={"enabled": False}))
    before = len(cm.messages)
    cm.check_and_compress()
    # 禁用后引擎不压缩；旧策略链也可能因阈值未达而不压缩
    assert len(cm.messages) == before


def test_auto_flag_off_still_manual_works():
    cm = _make_cm(config_port=_config_port(compaction={"auto": False}))
    # maybe_compact 需要 Agent 层；此处直接验证手动压缩可用
    result = cm.compact_now()
    assert result is not None
    assert result.success is True


# ── 手动压缩 ──────────────────────────────────────────────

def test_compact_now_replaces_and_reports():
    cm = _make_cm()
    result = cm.compact_now()
    assert result is not None
    assert result.success is True
    assert result.shadowed_count > 0
    assert result.saved_tokens >= 0
    assert SUMMARY_OPEN_TAG in str(cm.messages[result.inserted_index].get("content", ""))


def test_compact_now_none_when_no_range():
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "only"},
                {"role": "assistant", "content": "recent"}]
    cm = _make_cm(messages=messages)
    assert cm.compact_now() is None


def test_compact_now_raises_when_disabled():
    from src.core.compaction import ManualCompactionError

    cm = _make_cm(config_port=_config_port(compaction={"enabled": False}))
    with pytest.raises(ManualCompactionError):
        cm.compact_now()


# ── 落地/测量接口 ────────────────────────────────────────

def test_apply_replacement_updates_messages():
    cm = _make_cm()
    checkpoint = {"role": "user", "content": f"{SUMMARY_OPEN_TAG}\nsummary\n</compacted-summary>"}
    before = len(cm.messages)
    cm.apply_replacement(1, 3, checkpoint)
    assert len(cm.messages) == before - 2
    assert cm.messages[1] is checkpoint


def test_measure_and_message_token():
    cm = _make_cm()
    chars, tokens = cm.measure_context()
    assert chars > 0
    assert tokens > 0
    m_chars, m_tokens = cm.message_token(1)
    assert m_chars > 0
    assert m_tokens > 0


# ── 工具结果剪枝 ──────────────────────────────────────────

def test_prune_only_avoids_summary_when_enough():
    messages = [{"role": "system", "content": "sys"}]
    messages.append({"role": "user", "content": "start"})
    # 一条超大工具结果撑起 token，但剪枝后即回落到阈值内
    messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]})
    messages.append({"role": "tool", "tool_call_id": "c1", "content": "Z" * 40000})
    cm = _make_cm(messages=messages, config_port=_config_port(
        compaction={
            "threshold_ratio": 0.5, "headroom_tokens": 100, "retain_ratio": 0.1,
            "prune_threshold_chars": 200, "prune_head_chars": 50, "prune_tail_chars": 50,
        },
    ))
    result = cm._get_engine().compact_if_needed(force=False)
    assert result is not None
    assert result.success is True
    assert result.pruned_count == 1
    assert result.stats.get("mode") == "prune_only"
    from src.core.compaction.pruner import PRUNE_MARKER
    assert PRUNE_MARKER in cm.messages[3]["content"]
