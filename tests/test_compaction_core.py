"""src/core/compaction 核心模块单元测试。

覆盖 dsh 同款压缩的配置解析、检查点框定、工具结果剪枝、保留范围选择、
溢出错误识别与摘要调用。
"""

from __future__ import annotations

import pytest

from src.core.compaction.checkpoint import (
    CHECKPOINT_PREAMBLE,
    COMPACTION_INSTRUCTION,
    SUMMARY_CLOSE_TAG,
    SUMMARY_OPEN_TAG,
    build_checkpoint_message,
    extract_summary,
    frame_summary,
    is_checkpoint_message,
)
from src.core.compaction.config import (
    DEFAULT_HEADROOM_TOKENS,
    DEFAULT_RETAIN_RATIO,
    DEFAULT_THRESHOLD_RATIO,
    CompactionConfig,
    TargetPressureConfigError,
    resolve_compact_spec,
    resolve_config,
    resolve_target_policy,
)
from src.core.compaction.errors import is_context_overflow_error
from src.core.compaction.pruner import (
    PRUNE_MARKER,
    PruneConfig,
    prune_message_content,
    prune_messages,
    prune_text,
)
from src.core.compaction.region import (
    first_compactable_index,
    range_is_balanced,
    select_compactable_range,
)
from src.core.compaction.summarizer import SummaryError, summarize_region


# ── config ────────────────────────────────────────────────

def test_resolve_config_defaults():
    config = resolve_config({})
    assert config.enabled is True
    assert config.auto is True
    assert config.threshold_ratio == DEFAULT_THRESHOLD_RATIO
    assert config.headroom_tokens == DEFAULT_HEADROOM_TOKENS
    assert config.retain_ratio == DEFAULT_RETAIN_RATIO
    assert config.retain_tokens is None


def test_resolve_config_rejects_unknown_key():
    with pytest.raises(ValueError):
        resolve_config({"nope": 1})


def test_resolve_config_rejects_both_retention_forms():
    with pytest.raises(ValueError):
        resolve_config({"retain_ratio": 0.1, "retain_tokens": 100})


def test_resolve_config_rejects_retain_ge_threshold():
    with pytest.raises(ValueError):
        resolve_config({"threshold_ratio": 0.5, "retain_ratio": 0.6})


def test_resolve_config_model_policies():
    config = resolve_config({
        "model_policies": [
            {"provider": "local", "model": "small", "threshold_ratio": 0.7, "retain_tokens": 2048},
        ],
    })
    assert len(config.model_policies) == 1
    policy = resolve_target_policy(config, "local", "small")
    assert policy.threshold_ratio == 0.7
    assert policy.retain_tokens == 2048
    assert policy.retain_ratio is None


def test_resolve_config_rejects_duplicate_policy():
    with pytest.raises(ValueError):
        resolve_config({"model_policies": [
            {"provider": "a", "model": "b"},
            {"provider": "a", "model": "b"},
        ]})


def test_resolve_target_policy_falls_back_to_defaults():
    config = resolve_config({})
    policy = resolve_target_policy(config, "deepseek", "deepseek-v4-pro")
    assert policy.threshold_ratio == DEFAULT_THRESHOLD_RATIO
    assert policy.retain_ratio == DEFAULT_RETAIN_RATIO
    assert policy.retain_tokens is None


def test_resolve_compact_spec_formula():
    config = resolve_config({})
    policy = resolve_target_policy(config, "p", "m")
    spec = resolve_compact_spec(policy, 1_000_000, 0)
    # min(1M * 0.8, 1M - 0 - 65536) = 800000
    assert spec.threshold_tokens == 800_000
    # floor((1M - 0) * 0.16) = 160000
    assert spec.retain_tokens == 160_000


def test_resolve_compact_spec_capacity_caps():
    config = resolve_config({})
    policy = resolve_target_policy(config, "p", "m")
    # W=200000 → min(160000, 200000-65536=134464) = 134464
    spec = resolve_compact_spec(policy, 200_000, 0)
    assert spec.threshold_tokens == 134_464


def test_resolve_compact_spec_rejects_small_window():
    config = resolve_config({})
    policy = resolve_target_policy(config, "p", "m")
    with pytest.raises(TargetPressureConfigError):
        resolve_compact_spec(policy, 10_000, 0)


def test_resolve_compact_spec_rejects_reserved_over_window():
    config = resolve_config({})
    policy = resolve_target_policy(config, "p", "m")
    with pytest.raises(TargetPressureConfigError):
        resolve_compact_spec(policy, 100_000, 200_000)


# ── checkpoint ────────────────────────────────────────────

def test_frame_summary_contains_tags_and_preamble():
    framed = frame_summary("hello summary")
    assert CHECKPOINT_PREAMBLE in framed
    assert SUMMARY_OPEN_TAG in framed
    assert SUMMARY_CLOSE_TAG in framed
    assert "hello summary" in framed


def test_build_checkpoint_message_is_user_role():
    message = build_checkpoint_message("sum")
    assert message["role"] == "user"
    assert SUMMARY_OPEN_TAG in message["content"]


def test_is_checkpoint_message_new_and_legacy():
    assert is_checkpoint_message(build_checkpoint_message("x")) is True
    assert is_checkpoint_message({"role": "system", "content": "[对话摘要] old"}) is True
    assert is_checkpoint_message({"role": "user", "content": "plain"}) is False


def test_extract_summary_roundtrip():
    framed = frame_summary("the summary body")
    assert extract_summary(framed) == "the summary body"


def test_extract_summary_legacy():
    assert extract_summary("[对话摘要] legacy body") == "legacy body"


def test_compaction_instruction_has_all_sections():
    for section in (
        "## Primary Request and Intent",
        "## Key Technical Concepts",
        "## Files and Code",
        "## Errors and Fixes",
        "## Pending Jobs",
        "## Current Work",
        "## Next Step",
        "## Critical Context",
    ):
        assert section in COMPACTION_INSTRUCTION


# ── pruner ────────────────────────────────────────────────

def test_prune_text_within_budget():
    assert prune_text("short", PruneConfig()) is None


def test_prune_text_over_budget_head_tail_marker():
    text = "A" * 5000 + "B" * 5000
    pruned = prune_text(text, PruneConfig(threshold_chars=100, head_chars=10, tail_chars=10))
    assert pruned is not None
    assert PRUNE_MARKER in pruned
    assert pruned.startswith("AAAAAAAAAA")
    assert pruned.endswith("BBBBBBBBBB")
    assert len(pruned) < len(text)


def test_prune_messages_only_tool_role():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "c1", "content": "X" * 200},
    ]
    outcome = prune_messages(messages, PruneConfig(threshold_chars=50, head_chars=10, tail_chars=10))
    assert len(outcome.pruned) == 1
    assert outcome.pruned[0].index == 1
    assert messages[0]["content"] == "hi"
    assert PRUNE_MARKER in messages[1]["content"]
    assert outcome.chars_removed > 0


def test_prune_message_content_blocks():
    content = [
        {"type": "text", "text": "Y" * 200},
        {"type": "image_url", "image_url": {"url": "x"}},
    ]
    pruned = prune_message_content(
        content, PruneConfig(threshold_chars=50, head_chars=10, tail_chars=10),
    )
    assert pruned is not None
    assert pruned[1]["type"] == "image_url"
    assert any(PRUNE_MARKER in b.get("text", "") for b in pruned if b.get("type") == "text")


# ── region ────────────────────────────────────────────────

def _tokens_of_factory(messages):
    def _tokens_of(index):
        return max(1, len(str(messages[index].get("content", ""))))
    return _tokens_of


def test_first_compactable_index_skips_system_head():
    messages = [
        {"role": "system", "content": "sys1"},
        {"role": "system", "content": "sys2"},
        {"role": "user", "content": "hi"},
    ]
    assert first_compactable_index(messages) == 2


def test_range_is_balanced_tool_pair():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
    ]
    assert range_is_balanced(messages, 0, 1) is True
    assert range_is_balanced(messages, 0, 0) is False


def test_select_compactable_range_keeps_recent_tail():
    messages = [{"role": "system", "content": "sys"}]
    messages += [{"role": "user", "content": "x" * 100} for _ in range(10)]
    tokens_of = _tokens_of_factory(messages)
    selected = select_compactable_range(messages, retain_tokens=250, tokens_of=tokens_of)
    assert selected is not None
    start, end = selected
    assert start == 1
    assert end < len(messages) - 1


def test_select_compactable_range_none_when_all_recent():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "x"}]
    tokens_of = _tokens_of_factory(messages)
    assert select_compactable_range(messages, retain_tokens=1000, tokens_of=tokens_of) is None


def test_select_compactable_range_protects_pinned():
    messages = [{"role": "system", "content": "sys"}]
    messages += [{"role": "user", "content": "x" * 100} for _ in range(6)]
    messages[2]["pinned"] = True
    tokens_of = _tokens_of_factory(messages)
    selected = select_compactable_range(messages, retain_tokens=0, tokens_of=tokens_of)
    assert selected is not None
    start, end = selected
    assert start == 1
    assert end < 2  # 不越过 pinned 消息（index 2）


def test_select_compactable_range_never_splits_tool_group():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "r1"},
    ]
    tokens_of = _tokens_of_factory(messages)
    selected = select_compactable_range(messages, retain_tokens=0, tokens_of=tokens_of)
    assert selected is not None
    start, end = selected
    assert range_is_balanced(messages, start, end) is True


# ── errors ────────────────────────────────────────────────

def test_is_context_overflow_error_text_and_exception():
    assert is_context_overflow_error("This model's maximum context length is 8192") is True
    assert is_context_overflow_error(None, ValueError("context_length_exceeded")) is True
    assert is_context_overflow_error("normal reply") is False
    assert is_context_overflow_error(None, ValueError("network down")) is False


# ── summarizer ────────────────────────────────────────────

def test_summarize_region_returns_summary():
    captured = {}

    def fake_summarize(messages, model=None):
        captured["messages"] = messages
        captured["model"] = model
        return ("", "## Primary Request and Intent\n- do it", {"input": 10, "output": 5}, [])

    result = summarize_region(
        [{"role": "system", "content": "sys"}],
        [{"role": "user", "content": "hi"}],
        fake_summarize,
        "m",
    )
    assert result.summary.startswith("## Primary Request")
    assert captured["model"] == "m"
    # 回放前缀 + 区域消息 + 压缩指令
    assert captured["messages"][-1]["content"].startswith("You are now acting as a compaction engine")
    assert captured["messages"][0]["role"] == "system"


def test_summarize_region_rejects_empty_output():
    def fake_summarize(messages, model=None):
        return ("", "   ", {}, [])

    with pytest.raises(SummaryError):
        summarize_region([], [{"role": "user", "content": "hi"}], fake_summarize, "m")


def test_summarize_region_rejects_no_region():
    with pytest.raises(SummaryError):
        summarize_region([], [], lambda *a, **k: ("", "", {}, []), "m")
