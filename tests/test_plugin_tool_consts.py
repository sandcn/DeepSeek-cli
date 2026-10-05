"""工具常量条目化测试 — 每个内置常量一条独立条目。

覆盖：
- 清单为每个内置常量声明独立条目（可 patch/overlay）；
- tool_consts bundle 引入 core；
- 默认 profile 下常量来自注册表（消费方访问器/工具生效）；
- overlay 禁用单条常量真正生效（访问器返回空）；
- 条目 config 覆盖单条常量；
- 注册表接管 / 禁用 / 扩展 API。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)
from src.plugins.manifest import TOOL_CONST_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_builtin_constant():
    import src.tools.page_fetcher  # noqa: F401 - 声明 REMOVE_TAGS
    import src.tools.read_image  # noqa: F401 - 声明 IMAGE_EXTENSIONS
    from src.tools.const_registry import builtin_constant_names

    declared = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "tool_const_entry":
            declared.append((entry.config or {}).get("name"))
            entry_ids.append(entry.id)
    assert set(declared) == set(builtin_constant_names())
    assert all(i.startswith("tool_consts::tool_const_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    import src.tools.page_fetcher  # noqa: F401
    import src.tools.read_image  # noqa: F401
    from src.tools.const_registry import builtin_constant_names

    assert {(e.get("config") or {}).get("name") for e in TOOL_CONST_ENTRIES} == set(builtin_constant_names())


def test_tree_declares_tool_consts_bundle():
    tree = build_config_tree()
    assert "tool_consts" in tree.bundles()
    assert "tool_consts" in tree.bundle("core").includes


async def test_default_profile_constants():
    from src.tools import _constants as C

    kernel = await build_kernel("cli")
    try:
        assert C.default_encoding() == "utf-8"
        assert C.max_file_size_mb() == 100
        assert "latin-1" in C.catchall_encodings()
        assert C.common_encodings()[0] == "utf-8"
        from src.tools.read_image import (
            image_ext_format,
            image_extensions,
            image_format_media,
        )
        from src.tools.page_fetcher import (
            date_meta_patterns,
            private_prefixes,
            remove_class_keywords,
            remove_tags,
        )

        assert image_extensions()[".bmp"] == "image/bmp"
        assert image_extensions()[".png"] == "image/png"
        assert image_ext_format()[".bmp"] == "BMP"
        assert image_format_media()["BMP"] == "image/bmp"
        assert "script" in remove_tags()
        assert "sidebar" in remove_class_keywords()
        assert "10." in private_prefixes()
        assert date_meta_patterns()
    finally:
        await shutdown_kernel(kernel)


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


async def test_overlay_disable_single_constant():
    from src.tools import _constants as C

    kernel = await _build_with_disable(["tool_consts::tool_const_dangerous_device_files"])
    try:
        assert C.dangerous_device_files() == frozenset()
        assert C.default_encoding() == "utf-8"
    finally:
        await kernel.dispose()


async def test_overlay_disable_page_fetcher_tables():
    from src.tools.page_fetcher import private_prefixes, remove_class_keywords, remove_tags

    kernel = await _build_with_disable([
        "tool_consts::tool_const_private_prefixes",
        "tool_consts::tool_const_remove_class_keywords",
        "tool_consts::tool_const_remove_tags",
    ])
    try:
        assert private_prefixes() == ()
        assert remove_class_keywords() == ()
        assert remove_tags() == set()
    finally:
        await kernel.dispose()


async def test_entry_config_override_constant():
    from src.plugins.config import apply as config_apply
    from src.plugins.tool_const_entries import apply_tool_const
    from src.tools import _constants as C

    kernel = Kernel(name="t")
    kernel.mount(config_apply, config={"managed_tool_consts": ["MAX_FILE_SIZE_MB"]})
    kernel.mount(apply_tool_const, config={"name": "MAX_FILE_SIZE_MB", "value": 5})
    await kernel.settle()
    try:
        assert C.max_file_size_mb() == 5
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.tools.const_registry import (
        const,
        disable_builtin_constants,
        register_constant,
        reset,
        set_managed_builtin_constants,
    )

    reset()
    try:
        assert const("DEFAULT_ENCODING") == "utf-8"
        undo = set_managed_builtin_constants(["DEFAULT_ENCODING"])
        assert const("DEFAULT_ENCODING") is None
        undo()
        assert const("DEFAULT_ENCODING") == "utf-8"

        undo2 = disable_builtin_constants(["DEFAULT_ENCODING"])
        assert const("DEFAULT_ENCODING") is None
        undo2()
        assert const("DEFAULT_ENCODING") == "utf-8"

        undo3 = register_constant("CUSTOM_LIMIT", 7)
        assert const("CUSTOM_LIMIT") == 7
        undo3()
        assert const("CUSTOM_LIMIT") is None
    finally:
        reset()
