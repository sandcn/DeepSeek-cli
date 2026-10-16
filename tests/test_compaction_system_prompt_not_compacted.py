"""压缩时系统提词不参与压缩（用户需求：「全量上下文压缩时系统提词不要压缩」）。

两道保证：
  1. 压缩摘要请求不把系统提词回放/发送给摘要模型（不拿系统提词去压缩）；
  2. 压缩检查点不复述/包含系统提词内容。

同时固化：系统提词本身在压缩后仍逐字保留在消息列表头部（压缩范围从第一条
非系统消息开始，多 parts 系统提词全部保留）。
"""

from __future__ import annotations

from src.core.adapters.config import MockConfigAdapter
from src.core.compaction.checkpoint import COMPACTION_INSTRUCTION, SUMMARY_OPEN_TAG
from src.core.compaction.summarizer import build_summarization_messages
from src.core.context_manager import ContextManager

#: 系统提词片段（多 parts → 多条 system 消息），带唯一标记便于断言「未被压缩」。
SYSTEM_PARTS = ["SYS-RULE-ALPHA 系统提词片段甲", "SYS-RULE-BETA 系统提词片段乙"]


def _messages(n=20, content_len=400):
    messages = [{"role": "system", "content": part} for part in SYSTEM_PARTS]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}-" + "x" * content_len})
    return messages


def _config_port(compaction=None, **overrides):
    data = {
        "provider": "deepseek",
        "model_context_tokens": 1_000_000,
        "max_context_tokens": 1_000_000,
        "max_context_chars": 10_000_000,
        # 命中自动全量压缩阈值（token 口径）→ 不保留近期尾部的全量压缩
        "auto_force_compress_threshold": 100,
        "compaction": dict(compaction) if compaction else {
            "enabled": True,
            "threshold_ratio": 0.5,
            "headroom_tokens": 200,
            "retain_ratio": 0.1,
            "model_policies": [],
        },
    }
    data.update(overrides)
    return MockConfigAdapter(data)


def _make_cm(config_port=None, messages=None):
    messages = messages if messages is not None else _messages()
    calls = []

    def fake_summarize(msgs, model=None, **kwargs):
        calls.append(msgs)
        return ("", "## Primary Request and Intent\n- condensed summary",
                {"input": 5, "output": 5}, [])

    cm = ContextManager(
        messages, "test-model", summarize_fn=fake_summarize,
        config_port=config_port or _config_port(), activate_global=False,
    )
    cm._summary_requests = calls
    return cm


def _joined(request) -> str:
    return "\n".join(str(m.get("content", "")) for m in request)


# ── 引擎路径（全量压缩） ──────────────────────────────────

def test_summary_request_excludes_system_prompt_messages():
    """摘要请求里没有任何 system 提词消息（不回放）。"""
    cm = _make_cm()

    cm.check_and_compress(force=False)

    assert cm._summary_requests, "应发起摘要调用"
    assert all(
        m.get("role") != "system" for m in cm._summary_requests[0]
    ), "摘要请求不得回放系统提词"


def test_summary_request_excludes_system_prompt_text():
    """摘要请求文本里不出现系统提词内容（不拿系统提词去压缩）。"""
    cm = _make_cm()

    cm.check_and_compress(force=False)

    text = _joined(cm._summary_requests[0])
    for part in SYSTEM_PARTS:
        assert part not in text


def test_full_compaction_keeps_system_prompt_verbatim():
    """全量压缩后系统提词逐字保留，且检查点不含系统提词内容。"""
    cm = _make_cm()

    cm.check_and_compress(force=False)

    system_msgs = [m for m in cm.messages if m.get("role") == "system"]
    assert [m["content"] for m in system_msgs] == SYSTEM_PARTS
    assert cm.messages[0]["content"] == SYSTEM_PARTS[0]

    checkpoints = [
        m for m in cm.messages
        if isinstance(m.get("content"), str) and SUMMARY_OPEN_TAG in m["content"]
    ]
    assert checkpoints, "应落地检查点"
    for part in SYSTEM_PARTS:
        assert part not in checkpoints[0]["content"]


def test_manual_compaction_request_excludes_system_prompt():
    """手动 /compact（compact_now）的摘要请求同样不含系统提词。"""
    cm = _make_cm()

    result = cm.compact_now()

    assert result is not None and result.success is True
    assert cm._summary_requests
    for part in SYSTEM_PARTS:
        assert part not in _joined(cm._summary_requests[0])


# ── 摘要请求构建（纯函数） ────────────────────────────────

def test_build_summarization_messages_only_region_and_instruction():
    """摘要请求 = 被遮蔽区域消息 + 压缩指令（无系统提词前缀）。"""
    region = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}]

    messages = build_summarization_messages(region)

    assert messages[:2] == region
    assert len(messages) == 3
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].startswith("You are now acting as a compaction engine")


def test_compaction_instruction_forbids_restating_system_prompt():
    """压缩指令明确：系统提词不属于被压缩内容，检查点不得复述。"""
    lowered = COMPACTION_INSTRUCTION.lower()
    assert "system prompt" in lowered
    assert "never" in lowered
    assert "not being condensed" in lowered or "not part of the conversation" in lowered


# ── 回退策略链（引擎禁用） ────────────────────────────────

def test_fallback_chain_request_excludes_system_prompt():
    """回退策略链的摘要请求输入同样不含主系统提词内容。"""
    from src.core import context_summarizer as cs

    cm = _make_cm(config_port=_config_port(compaction={
        "enabled": False,
        "threshold_ratio": 0.5,
        "headroom_tokens": 200,
        "retain_ratio": 0.1,
        "model_policies": [],
    }))

    cm.check_and_compress(force=False)

    assert cm._summary_requests, "回退链应发起摘要调用"
    text = _joined(cm._summary_requests[0])
    for part in SYSTEM_PARTS:
        assert part not in text
    assert "系统提词" in cs._SUMMARY_SYSTEM
