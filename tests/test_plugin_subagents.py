"""SubAgent 运行时插件化测试 — Agent 类型独立插件条目 + ``ctx.subagents``。

覆盖：
- 清单为每个内置 Agent 类型声明独立条目（id 与内置类型一一对应）；
- 默认 profile 经独立条目注册全部内置类型（``ctx.subagents`` 可自省）；
- overlay 禁用单个类型真正生效（``subagents`` 抑制默认装配）；
- 条目 config 可覆盖类型规格（exclusions / prompt_builder / low_model ...）；
- ``ctx.subagents`` 提供 run / spawn / register_type / exclusions；
- 排除集合与 ``tool_policy.TOOL_EXCLUSION_MAP`` 同源；
- 直接 API：register/set_managed/disable/register_agent_type 与 reset。
"""

from __future__ import annotations

import asyncio

import pytest

import src.core.agent_types as at
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    at.reset()
    yield
    at.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def _agent_type_specs():
    return [entry.config.get("name") for entry, plug in _resolved() if getattr(plug, "name", "") == "agent_type"]


def test_manifest_declares_each_agent_type():
    specs = _agent_type_specs()
    assert specs == at.builtin_agent_type_ids()
    ids = [entry.id for entry, plug in _resolved() if getattr(plug, "name", "") == "agent_type"]
    assert all(i.startswith("agent_types::agent_type_") for i in ids)
    assert len(ids) == len(set(ids))


def test_tree_declares_agent_types_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "agent_types" in tree.bundles()
    assert "agent_types" in tree.bundle("runtime").includes


async def test_default_profile_registers_all_builtin(cli_kernel):
    service = cli_kernel.resolve_service("subagents")
    assert set(service.types()) == {"map", "review", "plan", "execute"}


async def test_subagents_service_exclusions_and_specs(cli_kernel):
    service = cli_kernel.resolve_service("subagents")
    assert "bash" in service.exclusions("review")
    assert "bash" not in service.exclusions("execute")
    assert service.exclusions(None) == set()
    spec = service.spec("plan")
    assert spec.path_whitelist == "plan"
    described = {item["name"] for item in service.describe()}
    assert {"map", "review", "plan", "execute"} <= described


async def test_exclusion_map_shared_with_tool_policy():
    from src.tools.tool_policy import TOOL_EXCLUSION_MAP
    from src.core.agent_types import exclusion_map

    assert TOOL_EXCLUSION_MAP is exclusion_map()
    assert "bash" in TOOL_EXCLUSION_MAP["review"]


async def _build_with_disable(ids):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), {"disable": list(ids)})
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


async def test_overlay_disable_single_agent_type():
    kernel = await _build_with_disable(["agent_types::agent_type_map"])
    try:
        service = kernel.resolve_service("subagents")
        assert "map" not in service.types()
        assert "execute" in service.types()
    finally:
        await kernel.dispose()
    assert "map" in at.agent_type_names()


async def test_entry_config_overrides_spec():
    from src.plugins.agent_type_entries import apply_agent_type

    kernel = Kernel(name="t")
    kernel.mount(
        apply_agent_type,
        config={"name": "review", "exclusions": ["bash"], "low_model": True},
    )
    await kernel.settle()
    try:
        spec = at.get_spec("review")
        assert spec.exclusions == ("bash",)
        assert spec.low_model is True
        assert at.excluded_tools("review") == {"bash"}
    finally:
        await kernel.dispose()
    assert "write_file" in at.excluded_tools("review")


async def test_custom_agent_type_registration():
    service_holder = {}

    async def build():
        from src.plugins.config import apply as config_apply
        from src.plugins.prompt import apply as prompt_apply
        from src.plugins.events import apply as events_apply
        from src.plugins.agents import apply as agents_apply
        from src.plugins.subagents import apply as subagents_apply

        kernel = Kernel(name="t")
        kernel.mount(config_apply)
        kernel.mount(events_apply)
        kernel.mount(prompt_apply)
        kernel.mount(agents_apply)
        kernel.mount(subagents_apply)
        await kernel.settle()
        service_holder["kernel"] = kernel
        return kernel

    kernel = await build()
    try:
        service = kernel.resolve_service("subagents")
        spec = at.AgentTypeSpec(
            name="audit", description="自定义审计型", prompt_builder="build_subagent_prompt",
            agent_name="sub", exclusions=("bash",), low_model=True,
        )
        service.register_type(spec)
        assert "audit" in service.types()
        assert service.exclusions("audit") == {"bash"}
        assert at.uses_low_model("audit") is True
    finally:
        await kernel.dispose()
    assert "audit" not in at.agent_type_names()


def test_build_prompt_parts_uses_registry():
    calls = []

    class _Port:
        def build_map_agent_prompt(self, cwd=None):
            calls.append("map")
            return ["map-prompt"]

        def build_subagent_prompt(self, cwd=None):
            calls.append("sub")
            return ["sub-prompt"]

    assert at.build_prompt_parts(_Port(), "map") == ["map-prompt"]
    assert at.build_prompt_parts(_Port(), "unknown-type") == ["sub-prompt"]
    assert calls == ["map", "sub"]


def test_low_model_policy_from_registry():
    from src.tools.subagent import SubagentFunc

    class _ConfigPort:
        def get_low_model(self):
            return "low-model"

    class _Agent:
        model = "parent-model"

        def get_config_port(self):
            return _ConfigPort()

    assert at.uses_low_model("map") is True
    assert at.uses_low_model("review") is False
    assert SubagentFunc._resolve_model(_Agent(), "map") == "low-model"
    assert SubagentFunc._resolve_model(_Agent(), "review") == "parent-model"


def test_register_and_reset_direct_api():
    undo = at.disable_builtin_agent_types(["map"])
    try:
        assert "map" not in at.agent_type_names()
        assert at.excluded_tools("map") == set(at.exclusion_map()["map"])
    finally:
        undo()
    assert "map" in at.agent_type_names()

    undo_manage = at.set_managed_builtin_agent_types(["review"])
    try:
        assert "review" not in at.agent_type_names()
    finally:
        undo_manage()
    assert "review" in at.agent_type_names()

    with pytest.raises(KeyError):
        at.disable_builtin_agent_types(["nope"])
    with pytest.raises(KeyError):
        at.register_builtin_agent_type("nope")


def test_invariant_subagents_types():
    from src.plugins.invariants import _subagents_types_registered

    class _Svc:
        def types(self):
            return ["map", "review", "plan", "execute"]

    class _Kernel:
        def has_service(self, key):
            return key == "subagents"

        def resolve_service(self, key):
            return _Svc()

    assert _subagents_types_registered(_Kernel()) is None

    class _BadSvc:
        def types(self):
            return ["execute"]

    class _BadKernel:
        def has_service(self, key):
            return key == "subagents"

        def resolve_service(self, key):
            return _BadSvc()

    assert "缺少" in _subagents_types_registered(_BadKernel())
