"""插件 Overlay（--patch / cordis.patch）与 Profile 目录测试。"""

from __future__ import annotations

import json

import pytest

from src.kernel.config_tree import ResolvedPlugin
from src.kernel.overlay import apply_overlay, normalize_overlay
from src.plugins.bootstrap import build_kernel, dump_profile, shutdown_kernel
from src.kernel import get_current_kernel


def _entries():
    return [
        ResolvedPlugin(id="core::a", plugin="x.a", config={"v": 1}, disabled=False, bundle="core"),
        ResolvedPlugin(id="core::b", plugin="x.b", config={}, disabled=False, bundle="core"),
    ]


def test_normalize_overlay_list_form():
    data = [
        {"insert": [{"id": "extra::c", "name": "x.c"}]},
        {"id": "core::a", "config": {"v": 2}},
        {"id": "core::b", "disabled": True},
    ]
    norm = normalize_overlay(data)
    assert norm["insert"][0]["id"] == "extra::c"
    assert norm["replace"]["core::a"] == {"v": 2}
    assert "core::b" in norm["disable"]


def test_normalize_overlay_dict_form():
    norm = normalize_overlay(
        {"insert": [{"id": "z", "plugin": "x.z"}], "replace": [{"id": "a", "config": {}}], "disable": ["b"]}
    )
    assert norm["insert"][0]["id"] == "z"
    assert "a" in norm["replace"]
    assert "b" in norm["disable"]


def test_apply_overlay_insert_replace_disable():
    entries = apply_overlay(
        _entries(),
        {
            "insert": [{"id": "extra::c", "plugin": "x.c", "config": {"v": 3}}],
            "replace": [{"id": "core::a", "config": {"v": 9}}],
            "disable": ["core::b"],
        },
    )
    by_id = {e.id: e for e in entries}
    assert by_id["core::a"].config == {"v": 9}
    assert by_id["core::b"].disabled is True
    assert by_id["extra::c"].plugin == "x.c"


def test_apply_overlay_replace_missing_raises():
    from src.kernel import PluginError

    with pytest.raises(PluginError):
        apply_overlay(_entries(), {"replace": [{"id": "ghost", "config": {}}]})


async def test_build_kernel_with_patch_overlay(tmp_path):
    plugin_file = tmp_path / "extra_plugin.py"
    plugin_file.write_text(
        "from src.kernel import plugin as _plugin\n"
        "@_plugin('extra_svc')\n"
        "def apply(ctx):\n"
        "    ctx.provide('extra_service', 'patched')\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "cordis.patch.yml"
    overlay.write_text(
        json.dumps({"insert": [{"id": "extra::extra_svc", "plugin": str(plugin_file)}]}),
        encoding="utf-8",
    )

    kernel = await build_kernel("minimal", patch_paths=[str(overlay)], discover_external=False)
    try:
        assert kernel.resolve_service("extra_service") == "patched"
    finally:
        await shutdown_kernel(kernel)
    assert get_current_kernel() is None


def test_dump_profile_with_patch(tmp_path):
    overlay = tmp_path / "p.json"
    overlay.write_text(json.dumps({"disable": ["core::skills"]}), encoding="utf-8")
    text = dump_profile("cli", patch_paths=[str(overlay)])
    assert "core::skills [disabled]" in text


def test_dump_profile_without_patch_ok():
    text = dump_profile("minimal")
    assert "profile: minimal" in text
