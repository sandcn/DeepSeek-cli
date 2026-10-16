"""语法高亮语言条目化测试 — 每个内置语言是清单中的独立插件条目。

覆盖：
- 清单为每个内置语言声明独立条目；
- presentation bundle 引入 syntax_languages bundle；
- 默认 profile 下 CodeBlock 高亮经注册表查询；
- overlay 禁用单个语言条目真正生效；
- 条目 config 覆盖关键字/别名/行注释；
- 注册表接管 / 禁用 / 扩展 API（新增自定义语言）。
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
from src.plugins.manifest import SYNTAX_LANGUAGE_ENTRIES, build_config_tree


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_language():
    from src.tui.ink.widgets._syntax_registry import builtin_language_ids

    ids = []
    entry_ids = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "syntax_language":
            ids.append((entry.config or {}).get("id"))
            entry_ids.append(entry.id)
    assert set(ids) == set(builtin_language_ids())
    assert all(i.startswith("syntax_languages::syntax_language_") for i in entry_ids)


def test_manifest_entries_match_registry_declaration():
    from src.tui.ink.widgets._syntax_registry import builtin_language_ids

    assert {(e.get("config") or {}).get("id") for e in SYNTAX_LANGUAGE_ENTRIES} == set(builtin_language_ids())


def test_tree_declares_syntax_bundle():
    tree = build_config_tree()
    assert "syntax_languages" in tree.bundles()
    assert "syntax_languages" in tree.bundle("presentation").includes


async def test_default_profile_highlight():
    from src.tui.ink.widgets import _syntax

    kernel = await build_kernel("cli")
    try:
        assert _syntax.is_supported("python")
        assert _syntax.normalize_language("py") == "python"
        assert _syntax.normalize_language("JS") == "javascript"
        runs = _syntax.tokenize_line('def f(): return "x"', "python")
        assert any(r.text == "def" and r.style is not None for r in runs)
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


async def test_overlay_disable_language():
    from src.tui.ink.widgets import _syntax

    kernel = await _build_with_disable(["syntax_languages::syntax_language_python"])
    try:
        assert not _syntax.is_supported("python")
        assert _syntax.normalize_language("py") == "py"
        assert _syntax.is_supported("javascript")
    finally:
        await kernel.dispose()


def test_registry_api_roundtrip():
    from src.tui.ink.widgets import _syntax_registry as reg
    from src.tui.ink.widgets._syntax_registry import SyntaxLanguage

    reg.reset()
    try:
        undo = reg.set_managed_builtin_languages(["python"])
        assert "python" not in reg.supported_language_ids()
        undo()
        assert "python" in reg.supported_language_ids()

        undo2 = reg.disable_builtin_languages(["json"])
        assert "json" not in reg.supported_language_ids()
        undo2()
        assert "json" in reg.supported_language_ids()

        undo3 = reg.register_language(
            SyntaxLanguage(id="mylang", keywords=("alpha", "beta"), aliases=("ml",), line_comment="!")
        )
        assert reg.normalize_language("ml") == "mylang"
        assert reg.language_keywords("mylang") == {"alpha", "beta"}
        assert reg.language_line_comment("mylang") == "!"
        undo3()
        assert reg.normalize_language("ml") == "ml"
    finally:
        reg.reset()


async def test_entry_config_override():
    from src.plugins.config import apply as config_apply
    from src.plugins.syntax_entries import apply_syntax_language

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(
        apply_syntax_language,
        config={"id": "python", "keywords": ["alpha", "beta"], "line_comment": "!!"},
    )
    await kernel.settle()
    try:
        from src.tui.ink.widgets import _syntax

        runs = _syntax.tokenize_line("alpha beta def", "python")
        ai_keywords = [r.text for r in runs if r.style is not None and r.text.strip()]
        assert "alpha" in ai_keywords and "beta" in ai_keywords
        assert "def" not in ai_keywords
        # 自定义行注释
        runs2 = _syntax.tokenize_line("!! note", "python")
        assert any(r.text == "!! note" and r.style is not None for r in runs2)
    finally:
        await kernel.dispose()
