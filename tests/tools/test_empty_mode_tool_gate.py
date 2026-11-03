"""空模式工具门禁 — `subagent` / `subagent_opt` 在空模式下必须**可用**。

用户需求（2026-10-25，方案 A）：空模式提词允许「用户明确点名要求时派发
子代理」，工具层不再从 schema 移除这两项；`subagent_opt(op="release")` 还是
主 Agent 文件沙盒锁的唯一主动释放通道，移除会让持锁方无法主动释放。

本测试锁定三点：
1. `EMPTY_MODE_EXCLUDED_TOOLS` 为空集（保留为后续收紧的扩展位）；
2. `filter_schemas_for_empty_mode` 当前不剔除任何工具（含 subagent / subagent_opt）；
3. `Agent._get_active_tools` 在空/简单/标准三种模式下都保留这两项（Ctrl+B
   切换模式不改变其可见性）；
4. **执行层同样不屏蔽**：主 Agent（`BaseAgent.agent_type == "main"`）不得因
   「未知类型回退 execute」策略被排除表拒绝（schema 可见但执行被拒 = 实际
   不可用），同时不得放宽子代理类型的既有边界。
"""

from __future__ import annotations

from src.core.agent import Agent
from src.prompt_builder import builder
from src.tools.tool_policy import (
    EMPTY_MODE_EXCLUDED_TOOLS,
    filter_schemas_for_empty_mode,
)


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": ""}}


def test_empty_mode_exclusion_set_is_empty() -> None:
    """空模式排除集合为空——收紧门禁须显式向集合填回工具名。"""
    assert EMPTY_MODE_EXCLUDED_TOOLS == frozenset()


def test_filter_schemas_for_empty_mode_keeps_delegation_tools() -> None:
    schemas = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
        _schema("user_select"),
    ]

    names = [s["function"]["name"] for s in filter_schemas_for_empty_mode(schemas)]

    assert names == ["read", "subagent", "subagent_opt", "user_select"]


def test_filter_schemas_for_empty_mode_keeps_empty_input() -> None:
    assert filter_schemas_for_empty_mode(None) == []
    assert filter_schemas_for_empty_mode([]) == []


def test_agent_active_tools_keep_delegation_tools_in_empty_mode() -> None:
    """空模式下 subagent / subagent_opt 可见；切到标准模式仍可见。"""
    agent = object.__new__(Agent)
    agent.tools = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
    ]
    agent.model = None

    original = builder.get_mode()
    try:
        builder.set_mode("empty")
        names = {s["function"]["name"] for s in agent._get_active_tools()}
        assert {"read", "subagent", "subagent_opt"} <= names

        builder.set_mode("standard")
        names = {s["function"]["name"] for s in agent._get_active_tools()}
        assert {"read", "subagent", "subagent_opt"} <= names
    finally:
        builder.set_mode(original)


# ── 执行层：主 Agent 不被子代理排除表误拒 ──────────────────────


def test_main_agent_type_is_not_subject_to_subagent_exclusion() -> None:
    """`"main"` 是主 Agent 标识，排除表不适用；子代理类型边界不变。"""
    from src.core.agent_types import (
        MAIN_AGENT_TYPE,
        excluded_tools,
        is_main_agent_type,
    )
    from src.tools.tool_policy import get_excluded_tools

    assert MAIN_AGENT_TYPE == "main"
    assert is_main_agent_type(None)
    assert is_main_agent_type("")
    assert is_main_agent_type("main")
    assert not is_main_agent_type("execute")

    assert excluded_tools("main") == set()
    assert get_excluded_tools("main") == set()

    # 子代理类型的排除边界不得因本修复放宽
    for excluded in (excluded_tools("execute"), get_excluded_tools("execute")):
        assert {"subagent", "subagent_opt"} <= excluded


def test_main_agent_can_use_delegation_tools() -> None:
    """主 Agent（`"main"` / None）可执行 subagent / subagent_opt。"""
    from src.tools.base import Func

    for name in ("subagent", "subagent_opt"):
        assert Func.can_use(name, "main") == (True, None)
        assert Func.can_use(name, None) == (True, None)
        allowed, reason = Func.can_use(name, "execute")
        assert not allowed and reason


def test_policy_service_allows_delegation_tools_for_main() -> None:
    """策略服务（工具执行管线 pre-execute 的裁决真源）放行主 Agent。"""
    from src.plugins.policy import PolicyService

    service = object.__new__(PolicyService)
    assert service.check("subagent", "main") == (True, None)
    assert service.check("subagent_opt", "main") == (True, None)
    assert service.check("subagent", None) == (True, None)
    assert service.excluded_tools("main") == set()
    assert {"subagent", "subagent_opt"} <= service.excluded_tools("execute")


def test_empty_mode_main_agent_execution_gate_open() -> None:
    """空模式下主 Agent 实际持有 `"main"` 类型时，执行层仍放行委派工具。"""
    from src.tools.base import Func

    agent = object.__new__(Agent)
    agent.tools = [_schema("subagent"), _schema("subagent_opt")]
    agent.model = None
    agent.agent_type = "main"  # BaseAgent.__init__ 对主 Agent 的赋值

    original = builder.get_mode()
    try:
        builder.set_mode("empty")
        names = {s["function"]["name"] for s in agent._get_active_tools()}
        assert {"subagent", "subagent_opt"} <= names
        for name in ("subagent", "subagent_opt"):
            assert Func.can_use(name, agent.agent_type) == (True, None)
    finally:
        builder.set_mode(original)


# ── 全部运行模式（空 / 简单 / 标准）────────────────────────────


def test_agent_active_tools_keep_delegation_tools_in_all_modes() -> None:
    """空 / 简单 / 标准三种运行模式下 subagent / subagent_opt 均可见。"""
    from src.prompt_builder.modes import mode_order

    agent = object.__new__(Agent)
    agent.tools = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
    ]
    agent.model = None

    original = builder.get_mode()
    try:
        modes = mode_order()
        assert {"empty", "simple", "standard"} <= set(modes)
        for mode in modes:
            builder.set_mode(mode)
            assert builder.get_mode() == mode
            names = {s["function"]["name"] for s in agent._get_active_tools()}
            assert {"subagent", "subagent_opt"} <= names, mode
    finally:
        builder.set_mode(original)


def test_empty_mode_filter_never_drops_delegation_tools(monkeypatch) -> None:
    """即使排除集合被填回委派工具名，过滤仍强制保留它们。"""
    import src.tools.tool_policy as tool_policy

    monkeypatch.setattr(
        tool_policy,
        "EMPTY_MODE_EXCLUDED_TOOLS",
        frozenset({"subagent", "subagent_opt", "read"}),
    )
    schemas = [
        _schema("read"),
        _schema("subagent"),
        _schema("subagent_opt"),
        _schema("write"),
    ]

    names = [
        s["function"]["name"]
        for s in tool_policy.filter_schemas_for_empty_mode(schemas)
    ]

    assert names == ["subagent", "subagent_opt", "write"]


# ── 全部 preset（standard / minimal / code）────────────────────


def test_builtin_presets_do_not_exclude_delegation_tools() -> None:
    """内置 preset 声明不得排除 subagent / subagent_opt。"""
    from src.core.agent_types import DELEGATION_TOOLS
    from src.core.presets import active_presets

    presets = active_presets()
    assert {"standard", "minimal", "code"} <= set(presets)
    for preset in presets.values():
        assert not (DELEGATION_TOOLS & set(preset.tool_excludes)), preset.name
        for name in DELEGATION_TOOLS:
            assert preset.allows(name), (preset.name, name)


def test_preset_apply_keeps_delegation_tools_for_main_agent_only() -> None:
    """主 Agent 在任意 preset 下保留委派工具；SubAgent 边界不变。"""
    from src.core.presets import Preset, register_preset, unregister_preset
    from src.plugins.presets import PresetsService

    register_preset(Preset(
        "delegation_guard_test",
        tool_excludes=("subagent", "subagent_opt", "write"),
    ))
    try:
        service = object.__new__(PresetsService)

        class _Agent:
            def __init__(self, agent_type: str) -> None:
                self.agent_type = agent_type
                self.tools = [
                    _schema("write"),
                    _schema("subagent"),
                    _schema("subagent_opt"),
                    _schema("read"),
                ]

        main_agent = _Agent("main")
        service.apply_to_agent("delegation_guard_test", main_agent)
        names = {s["function"]["name"] for s in main_agent.tools}
        assert names == {"subagent", "subagent_opt", "read"}

        sub_agent = _Agent("execute")
        service.apply_to_agent("delegation_guard_test", sub_agent)
        names = {s["function"]["name"] for s in sub_agent.tools}
        assert names == {"read"}
    finally:
        unregister_preset("delegation_guard_test")


def test_all_builtin_presets_keep_delegation_tools_for_main_agent() -> None:
    """全部内置 preset 应用到主 Agent 后，两项委派工具仍在工具集中。"""
    from src.core.presets import active_presets
    from src.plugins.presets import PresetsService

    service = object.__new__(PresetsService)

    class _Agent:
        agent_type = "main"

        def __init__(self) -> None:
            self.tools = [
                _schema("read"),
                _schema("write"),
                _schema("bash"),
                _schema("subagent"),
                _schema("subagent_opt"),
            ]

    for preset_name in active_presets():
        agent = _Agent()
        service.apply_to_agent(preset_name, agent)
        names = {s["function"]["name"] for s in agent.tools}
        assert {"subagent", "subagent_opt"} <= names, preset_name
        if preset_name == "minimal":
            # 其余排除项语义不变
            assert "write" not in names and "bash" not in names

