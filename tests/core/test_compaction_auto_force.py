"""自动全量压缩（auto_force_compress_threshold）与字符口径上限的引擎路径测试。

背景：引擎启用（``compaction.enabled=True``，默认）时
``ContextManager.check_and_compress`` 直接走引擎并 return，回退策略链
（唯一读取 ``auto_force_compress_threshold`` 的路径）不执行——该配置在默认
配置下完全失效，「自动全量压缩没有作用」。

本文件固化修复后的语义：
- ``auto_force_compress_threshold`` 为 **token 口径**（2026-10-09 用户需求：
  单位由字符改为 token，默认 600k）；
- 引擎路径同样尊重 ``auto_force_compress_threshold``（命中 → force 全量压缩）；
- ``max_context_chars`` 字符口径参与引擎的常规压力触发判定；
- 回退策略链行为不回归。
"""

from __future__ import annotations

from src.core.adapters.config import MockConfigAdapter
from src.core.compaction.checkpoint import SUMMARY_OPEN_TAG
from src.core.compaction.config import ResolvedCompactSpec
from src.core.context_manager import ContextManager

_DEFAULT_COMPACTION = {
    "enabled": True,
    "threshold_ratio": 0.5,
    "headroom_tokens": 200,
    "retain_ratio": 0.1,
    "model_policies": [],
}


def _messages(n=10, content_len=400):
    messages = [{"role": "system", "content": "sys prompt"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}-" + "x" * content_len})
    return messages


def _config(compaction=None, **overrides):
    data = {
        "provider": "deepseek",
        "model_context_tokens": 1_000_000,
        "max_context_tokens": 1_000_000,
        "max_context_chars": 10_000_000,
        "auto_force_compress_threshold": 10_000_000,
        "compaction": dict(compaction) if compaction else dict(_DEFAULT_COMPACTION),
    }
    data.update(overrides)
    return MockConfigAdapter(data)


def _make_cm(config_port, messages=None):
    messages = messages if messages is not None else _messages()
    calls = []

    def fake_summarize(msgs, model=None, **kwargs):
        calls.append(msgs)
        return ("", "## Primary Request and Intent\n- condensed summary",
                {"input": 5, "output": 5}, [])

    cm = ContextManager(
        messages, "test-model", summarize_fn=fake_summarize,
        config_port=config_port, activate_global=False,
    )
    cm._summarize_calls = calls
    return cm


def _has_checkpoint(cm) -> bool:
    return any(SUMMARY_OPEN_TAG in str(m.get("content", "")) for m in cm.messages)


# ── 自动全量压缩阈值（引擎路径） ──────────────────────────

def test_engine_path_honors_auto_force_threshold():
    """引擎启用时超 auto_force 阈值 → 全量压缩（不保留近期尾部）。"""
    cm = _make_cm(_config(auto_force_compress_threshold=100))
    before = len(cm.messages)

    cm.check_and_compress(force=False)

    assert cm._summarize_calls, "引擎路径应发起摘要（auto_force 命中）"
    assert len(cm.messages) < before
    assert _has_checkpoint(cm)
    assert cm.messages[0]["role"] == "system"
    assert "sys prompt" in cm.messages[0]["content"]


def test_engine_path_auto_force_compacts_everything_compactable():
    """auto_force 命中为「全量」：仅保留系统提词 / 检查点 / 最后一条消息。"""
    cm = _make_cm(_config(auto_force_compress_threshold=100))

    cm.check_and_compress(force=False)

    assert cm._summarize_calls
    assert len(cm.messages) == 3
    assert _has_checkpoint(cm)


def test_engine_path_skips_below_auto_force_and_char_limit():
    """未达 auto_force 阈值且未达字符/token 上限 → 不压缩。"""
    cm = _make_cm(_config(auto_force_compress_threshold=10_000_000))
    before = len(cm.messages)

    cm.check_and_compress(force=False)

    assert cm._summarize_calls == []
    assert len(cm.messages) == before


def test_auto_force_triggered_helper_reads_config():
    """ContextManager._auto_force_triggered 按配置返回触发状态。"""
    triggered = _make_cm(_config(auto_force_compress_threshold=100))
    assert triggered._auto_force_triggered() is True

    idle = _make_cm(_config(auto_force_compress_threshold=10_000_000))
    assert idle._auto_force_triggered() is False


# ── 字符口径上限（max_context_chars）参与引擎触发 ──────────

def test_engine_path_char_limit_triggers_pressure_compaction():
    """token 未达阈值但字符超 max_context_chars → 引擎压力压缩。"""
    cm = _make_cm(_config(
        compaction={
            "enabled": True,
            "threshold_ratio": 0.9,
            "headroom_tokens": 0,
            "retain_tokens": 1,
            "model_policies": [],
        },
        max_context_chars=2000,
        auto_force_compress_threshold=10_000_000,
    ))
    before = len(cm.messages)

    cm.check_and_compress(force=False)

    assert cm._summarize_calls, "字符超上限应触发压缩"
    assert len(cm.messages) < before
    assert _has_checkpoint(cm)


def test_engine_path_char_limit_below_does_not_trigger():
    """字符未超 max_context_chars 且 token 未达阈值 → 不压缩。"""
    cm = _make_cm(_config(
        max_context_chars=10_000_000,
        auto_force_compress_threshold=10_000_000,
    ))
    before = len(cm.messages)

    cm.check_and_compress(force=False)

    assert cm._summarize_calls == []
    assert len(cm.messages) == before


def test_engine_pressure_exceeded_checks_token_and_char():
    """引擎压力判定同时看 token 阈值与字符上限。"""
    cm = _make_cm(_config(max_context_chars=2000))
    engine = cm._get_engine()
    spec = ResolvedCompactSpec(
        context_window=1_000_000, threshold_tokens=900_000, retain_tokens=1,
    )

    assert engine._pressure_exceeded(spec, 2000) is True   # 字符 4040 > 2000
    assert engine._pressure_exceeded(spec, 10_000_000) is False


# ── 回退策略链不回归 ────────────────────────────────────

def test_fallback_chain_still_honors_auto_force():
    """引擎禁用时回退策略链仍按 auto_force 阈值强制压缩。"""
    cm = _make_cm(_config(
        compaction={
            "enabled": False,
            "threshold_ratio": 0.5,
            "headroom_tokens": 200,
            "retain_ratio": 0.1,
            "model_policies": [],
        },
        auto_force_compress_threshold=100,
    ))
    before = len(cm.messages)

    cm.check_and_compress(force=False)

    assert cm._summarize_calls
    assert len(cm.messages) < before


# ── 补充：token 口径与读取失败分支 ──────────────────────

def _cjk_messages(per=300, n=30):
    """构造纯中文消息（1 中文字符 ≈ 0.6 token，token 口径判定用）。"""
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": "中" * per})
    return messages


def test_auto_force_triggered_token_threshold():
    """token 口径命中 auto_force 阈值 → 触发。"""
    cm = _make_cm(
        _config(auto_force_compress_threshold=5_000),
        messages=_cjk_messages(),
    )
    _chars, tokens = cm.measure_context()
    assert tokens > 5_000
    assert cm._auto_force_triggered() is True


def test_auto_force_threshold_is_token_based_not_chars():
    """字符数已超阈值但 token 未超 → 不触发（口径为 token，不是字符）。"""
    cm = _make_cm(
        _config(auto_force_compress_threshold=5_000),
        messages=_messages(n=10, content_len=600),
    )
    chars, tokens = cm.measure_context()
    assert chars > 5_000
    assert tokens < 5_000
    assert cm._auto_force_triggered() is False


def test_auto_force_triggered_includes_tools_tokens():
    """token 口径含工具列表开销：消息很短但工具 schema 大 → 触发。"""
    cm = _make_cm(
        _config(auto_force_compress_threshold=5_000),
        messages=_messages(n=2, content_len=10),
    )
    cm.set_tools([{
        "type": "function",
        "function": {"name": "t", "description": "x" * 20_000},
    }])
    _chars, tokens = cm.measure_context()
    assert tokens > 5_000
    assert cm._auto_force_triggered() is True


def test_engine_path_token_branch_compacts_everything_compactable():
    """引擎路径 token 口径命中 auto_force → 全量压缩（不保留近期尾部）。"""
    cm = _make_cm(
        _config(auto_force_compress_threshold=5_000),
        messages=_cjk_messages(),
    )
    before = len(cm.messages)

    cm.check_and_compress(force=False)

    assert cm._summarize_calls
    assert len(cm.messages) == 3
    assert _has_checkpoint(cm)


def test_max_context_chars_read_failure_disables_char_trigger():
    """读取 max_context_chars 失败 → 按不设字符阈值（0）处理。"""
    cm = _make_cm(_config())
    engine = cm._get_engine()

    class _BoomConfig:
        @staticmethod
        def get_max_context_chars():
            raise RuntimeError("boom")

    engine._config_port = _BoomConfig()
    assert engine._max_context_chars() == 0


def test_engine_pressure_exceeded_ignores_char_when_disabled():
    """字符上限 <= 0 时不参与判定（仅看 token 阈值）。"""
    cm = _make_cm(_config())
    engine = cm._get_engine()
    spec = ResolvedCompactSpec(
        context_window=1_000_000, threshold_tokens=900_000, retain_tokens=1,
    )

    assert engine._pressure_exceeded(spec, 0) is False


# ── 显示口径与压缩判定同源（真实基线优先） ────────────────

def test_measure_context_uses_real_baseline():
    """measure_context（压缩判定口径）采用真实基线（与 TUI 显示同源）。"""
    cm = _make_cm(_config())
    cm.set_prompt_baseline(200_000)

    _chars, tokens = cm.measure_context()

    assert tokens == 200_000


def test_measure_context_adds_tail_after_baseline():
    """真实基线生效时，measure_context 只叠加基线之后新增消息的估算。"""
    from src.core.internal.shared._message_text import message_to_text
    from src.core.tokens import estimate_tokens

    cm = _make_cm(_config())
    cm.set_prompt_baseline(100_000)
    new_msg = {"role": "user", "content": "中" * 1000}
    cm.messages.append(new_msg)

    _chars, tokens = cm.measure_context()

    assert tokens == 100_000 + estimate_tokens(message_to_text(new_msg))


def test_auto_force_triggers_on_display_baseline_though_estimate_below():
    """显示口径（真实基线）达阈值而纯估算未达 → 自动全量压缩仍触发。

    复现用户报告：「main agent 上下文达到了 auto_force_compress_threshold
    指定值没有自动全量压缩」——TUI ``main · N%`` 按真实基线显示达阈值，
    修复前判定用纯估算（偏低）未达 → 静默不压缩。修复后判定与显示同源。
    """
    cm = _make_cm(_config(auto_force_compress_threshold=400_000))
    _chars, estimate = cm.measure_context()
    assert estimate < 400_000

    cm.set_prompt_baseline(500_000)

    _chars, tokens = cm.measure_context()
    assert tokens >= 400_000
    assert cm._auto_force_triggered() is True

    before = len(cm.messages)
    cm.check_and_compress(force=False)

    assert cm._summarize_calls, "显示口径达阈值必须触发全量压缩"
    assert len(cm.messages) < before
    assert _has_checkpoint(cm)


def test_measure_context_falls_back_to_estimate_after_baseline_invalid():
    """基线因前缀消息被替换失效后，measure_context 回退全量估算。"""
    cm = _make_cm(_config(auto_force_compress_threshold=10_000_000))
    cm.set_prompt_baseline(500_000)
    cm.messages[0] = {"role": "system", "content": "sys prompt changed"}

    _chars, tokens = cm.measure_context()

    assert tokens < 500_000
    assert cm._prompt_baseline is None
