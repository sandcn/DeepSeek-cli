"""提示词片段条目化测试 — 每个系统提词片段一个独立插件条目。

覆盖：
- 清单为每个内置片段声明独立条目（可 patch/overlay）；
- prompts bundle 引入片段条目；
- 默认 profile 下 ctx.prompt.sections() 覆盖内置片段且顺序稳定；
- overlay 禁用片段真正生效（提词内容/分段变化）；
- overlay 覆盖 order 调整装配顺序；
- 注册表接管 / 禁用 / 扩展 API。
"""

from __future__ import annotations

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import PROMPT_SECTION_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_section():
    from src.prompt_builder.sections import builtin_section_ids

    declared = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "prompt_section":
            declared.append((entry.config or {}).get("name"))
            entry_ids.append(entry.id)
    assert set(declared) == set(builtin_section_ids())
    assert all(i.startswith("prompts::prompt_section_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.prompt_builder.sections import builtin_section_ids

    assert {(e.get("config") or {}).get("name") for e in PROMPT_SECTION_ENTRIES} == set(builtin_section_ids())


def test_tree_declares_prompts_sections():
    tree = build_config_tree()
    assert "prompts" in tree.bundles()
    entry_ids = {entry.id for entry in tree.resolve("cli")}
    assert "prompts::prompt_section_env_info" in entry_ids
    assert "prompts::prompt_section_vcs_info" in entry_ids


async def test_default_profile_sections():
    from src.prompt_builder.sections import builtin_section_ids

    kernel = await build_kernel("cli")
    try:
        prompt = kernel.resolve_service("prompt")
        assert set(prompt.sections()) == set(builtin_section_ids())
        assert prompt.section_order() == [
            "export", "global_md", "agent_md", "env_info", "vcs_info", "skills", "mcp",
        ]
        assert set(prompt.managed_sections()) == set(builtin_section_ids())
        assert prompt.disabled_sections() == []
    finally:
        await shutdown_kernel(kernel)


async def _build_with_overlay(overlay):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), overlay)
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


async def test_overlay_disable_vcs_section():
    from src.prompt_builder.builder import build_system_prompt

    kernel = await _build_with_overlay({"disable": ["prompts::prompt_section_vcs_info"]})
    try:
        parts = build_system_prompt()
        assert "# 版本控制" not in "\n".join(parts)
        assert any(p.startswith("# 当前执行环境") for p in parts)
    finally:
        await kernel.dispose()


async def test_overlay_disable_env_info_keeps_vcs_part():
    from src.prompt_builder.builder import build_system_prompt

    kernel = await _build_with_overlay({"disable": ["prompts::prompt_section_env_info"]})
    try:
        parts = build_system_prompt()
        assert not any(p.startswith("# 当前执行环境") for p in parts)
        assert any(p.startswith("# 版本控制") for p in parts)
    finally:
        await kernel.dispose()


async def test_overlay_reorder_sections():
    from src.prompt_builder.builder import build_system_prompt

    kernel = await _build_with_overlay({"replace": {"prompts::prompt_section_vcs_info": {
        "name": "vcs_info", "order": 5, "attach_to": "",
    }}})
    try:
        parts = build_system_prompt()
        assert parts[0].startswith("# 版本控制")
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.prompt_builder.sections import (
        PromptSection,
        active_sections,
        disable_builtin_sections,
        managed_section_ids,
        register_section,
        reset,
        set_managed_builtin_sections,
    )

    reset()
    try:
        assert "env_info" in active_sections()
        undo = set_managed_builtin_sections(["env_info"])
        assert "env_info" not in active_sections()
        assert "env_info" in managed_section_ids()
        undo()
        assert "env_info" in active_sections()

        undo2 = disable_builtin_sections(["vcs_info"])
        assert "vcs_info" not in active_sections()
        undo2()
        assert "vcs_info" in active_sections()

        undo3 = register_section(PromptSection(id="custom", label="自定义", order=99))
        assert "custom" in active_sections()
        undo3()
        assert "custom" not in active_sections()
    finally:
        reset()
