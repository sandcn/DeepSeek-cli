"""所有 Agent 的压缩能力测试。

覆盖：
  - ``BaseAgent.maybe_compact`` / ``compact_context`` / ``recover_context_overflow``
  - ``SubAgent`` 创建独立 ContextManager（所有 SubAgent 均可压缩）
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.adapters.config import MockConfigAdapter
from src.core.base_agent import BaseAgent
from src.core.context_manager import ContextManager
from src.core.subagent import SubAgent


def _messages(n=20, content_len=400):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(n):
        messages.append({"role": "user", "content": f"m{i}-" + "x" * content_len})
    return messages


def _config_port(**compaction):
    data = {"model_context_tokens": 2000, "provider": "deepseek",
            "compaction": {"threshold_ratio": 0.5, "headroom_tokens": 100,
                           "retain_ratio": 0.1, **compaction}}
    return MockConfigAdapter(data)


def _make_cm(config_port=None):
    return ContextManager(
        _messages(), "test-model",
        summarize_fn=lambda msgs, model=None: ("", "## condensed summary", {}, []),
        config_port=config_port or _config_port(),
    )


# ── BaseAgent ─────────────────────────────────────────────

def test_maybe_compact_without_manager():
    agent = BaseAgent()
    assert agent.maybe_compact() is False


def test_maybe_compact_enabled():
    agent = BaseAgent()
    agent.context_manager = _make_cm()
    assert agent.maybe_compact() is True


def test_maybe_compact_respects_auto_off():
    agent = BaseAgent()
    agent.context_manager = _make_cm(config_port=_config_port(auto=False))
    assert agent.maybe_compact() is False


def test_compact_context_manual():
    agent = BaseAgent()
    messages = _messages()
    agent.context_manager = ContextManager(
        messages, "m",
        summarize_fn=lambda msgs, model=None: ("", "## condensed summary", {}, []),
        config_port=_config_port(),
    )
    assert agent.compact_context(force=True) is None  # check_and_compress 返回 None
    assert any("<compacted-summary>" in str(m.get("content", "")) for m in messages)


def test_recover_context_overflow():
    agent = BaseAgent()
    agent.context_manager = _make_cm()
    assert agent.recover_context_overflow() is True


def test_recover_context_overflow_without_manager():
    agent = BaseAgent()
    assert agent.recover_context_overflow() is False


# ── SubAgent ──────────────────────────────────────────────

class _FakeRegistry:
    def get_schemas(self):
        return []


def _make_subagent(monkeypatch, label="agent-1"):
    monkeypatch.setattr(
        "src.core.agent_types.build_prompt_parts",
        lambda port, agent_type: ["sys prompt"],
    )
    parent = SimpleNamespace(
        get_tool_registry=lambda: _FakeRegistry(),
        get_prompt_builder_port=lambda: object(),
        get_config_port=lambda: MockConfigAdapter({}),
        model="m",
        _event_port=None,
        _async_model_port=None,
    )
    return SubAgent(label, "desc", "prompt", parent, agent_type="map")


def test_subagent_owns_context_manager(monkeypatch):
    sub = _make_subagent(monkeypatch)
    assert isinstance(sub.context_manager, ContextManager)
    assert sub.context_manager.label == "agent-1"


def test_subagent_maybe_compact_available(monkeypatch):
    sub = _make_subagent(monkeypatch)
    # 消息量远低于阈值 → 不压缩但检查可用
    assert sub.maybe_compact() is True


def test_subagent_context_manager_not_global(monkeypatch):
    """SubAgent 的 ContextManager 不注册为全局活跃实例（不污染主 Agent）。"""
    from src.core import context_manager as cm_module

    before = cm_module._active_context_manager
    sub = _make_subagent(monkeypatch, label="agent-global")
    assert cm_module._active_context_manager is before
    assert cm_module._active_context_manager is not sub.context_manager


def test_subagent_refresh_usage_skips_global_snapshot(monkeypatch):
    """SubAgent 的 refresh_usage 不写全局上下文使用率快照。"""
    from src.core import context_manager as cm_module

    sub = _make_subagent(monkeypatch, label="agent-snapshot")
    cm_module.set_context_usage_percent(12.5)
    sub.context_manager.refresh_usage(force=True)
    assert cm_module.get_context_usage_percent() == 12.5


def test_main_context_manager_registers_global():
    """主 Agent（默认 activate_global=True）注册为全局活跃实例。"""
    from src.core import context_manager as cm_module

    cm = _make_cm()
    assert cm_module._active_context_manager is cm
