"""配置组装层测试 — Profile / Bundle / Patch / dump / 清单加载。"""

from __future__ import annotations

import json
import os

import pytest

from src.kernel import ConfigTree, PluginError
from src.kernel import _yaml
from src.kernel.config_tree import materialize
from src.kernel.dump import format_config_dump, summarize
from src.kernel.manifest import load_text, load_tree_from_dict
from src.kernel.plugin import plugin


@plugin("dummy")
def dummy_plugin(ctx):
    ctx.provide("dummy", 1)


def _tree():
    tree = ConfigTree()
    tree.add_bundle_dict({
        "id": "base",
        "plugins": [
            {"id": "a", "plugin": dummy_plugin, "config": {"n": 1}},
            {"id": "b", "plugin": dummy_plugin, "config": {"n": 2}},
        ],
    })
    tree.add_bundle_dict({
        "id": "extra",
        "includes": ["base"],
        "plugins": [{"id": "c", "plugin": dummy_plugin}],
    })
    tree.add_profile_dict({"name": "cli", "bundles": ["extra"]})
    tree.add_profile_dict({
        "name": "patched",
        "bundles": ["base"],
        "patches": [{"target": "base::a", "config": {"n": 9}, "disabled": True}],
    })
    return tree


def test_resolve_includes_order_and_ids():
    entries = _tree().resolve("cli")
    assert [e.id for e in entries] == ["base::a", "base::b", "extra::c"]


def test_patch_overrides_config_and_disabled():
    entries = _tree().resolve("patched")
    entry = next(e for e in entries if e.id == "base::a")
    assert entry.config == {"n": 9}
    assert entry.disabled is True


def test_patch_target_missing_raises():
    tree = ConfigTree()
    tree.add_bundle_dict({"id": "base", "plugins": [{"id": "a", "plugin": dummy_plugin}]})
    tree.add_profile_dict({"name": "p", "bundles": ["base"], "patches": [{"target": "nope"}]})
    with pytest.raises(PluginError):
        tree.resolve("p")


def test_duplicate_plugin_id_raises():
    tree = ConfigTree()
    tree.add_bundle_dict({
        "id": "base",
        "plugins": [{"id": "a", "plugin": dummy_plugin}, {"id": "a", "plugin": dummy_plugin}],
    })
    tree.add_profile_dict({"name": "p", "bundles": ["base"]})
    with pytest.raises(PluginError):
        tree.resolve("p")


def test_unknown_profile_raises():
    with pytest.raises(PluginError):
        _tree().resolve("nope")


def test_dump_shape():
    dump = _tree().dump("patched")
    assert dump["profile"] == "patched"
    assert dump["bundles"] == ["base"]
    assert dump["patches"][0]["target"] == "base::a"
    ids = [p["id"] for p in dump["plugins"]]
    assert ids == ["base::a", "base::b"]


def test_format_config_dump_contains_fields():
    text = format_config_dump(_tree().dump("patched"))
    assert "profile: patched" in text
    assert "base::a" in text
    assert "[disabled]" in text


def test_summarize_counts():
    entries = _tree().resolve("patched")
    stats = summarize(entries)
    assert stats == {"total": 2, "enabled": 1, "disabled": 1}


def test_materialize_resolves_string_refs():
    tree = ConfigTree()
    tree.add_bundle_dict({"id": "base", "plugins": [{"id": "d", "plugin": "tests.test_kernel_config_tree:dummy_plugin"}]})
    tree.add_profile_dict({"name": "p", "bundles": ["base"]})
    pairs = materialize(tree.resolve("p"))
    assert len(pairs) == 1
    assert pairs[0][1].name == "dummy"


# ── YAML 子集 ────────────────────────────────────────────────

def test_yaml_subset_parses_nested_manifest():
    text = """
# comment
bundles:
  - id: base
    plugins:
      - id: tools
        plugin: x.y
        config:
          enabled: true
          max: 3
    includes: [a, b]
profiles:
  - name: cli
    bundles: [base]
"""
    data = load_text(text, fmt="yaml")
    assert data["bundles"][0]["id"] == "base"
    assert data["bundles"][0]["plugins"][0]["config"] == {"enabled": True, "max": 3}
    assert data["bundles"][0]["includes"] == ["a", "b"]
    assert data["profiles"][0]["name"] == "cli"


def test_yaml_scalar_types():
    assert _yaml.load("a: 1\nb: 2.5\nc: true\nd: null\ne: hello") == {
        "a": 1, "b": 2.5, "c": True, "d": None, "e": "hello",
    }


def test_yaml_quoted_string_with_comment():
    data = _yaml.load('key: "value # not comment"  # real comment')
    assert data["key"] == "value # not comment"


def test_yaml_rejects_tabs():
    with pytest.raises(_yaml.YamlError):
        _yaml.load("a:\n\t- 1")


def test_manifest_from_dict_builds_tree():
    tree = load_tree_from_dict({
        "bundles": [{"id": "b", "plugins": [{"id": "p", "plugin": dummy_plugin}]}],
        "profiles": [{"name": "pf", "bundles": ["b"]}],
    })
    assert tree.profiles() == ["pf"]
    assert tree.resolve("pf")[0].id == "b::p"


def test_manifest_directory_loading(tmp_path):
    from src.kernel.manifest import load_tree_from_dir

    (tmp_path / "base.yml").write_text(
        "id: base\nplugins:\n  - id: p\n    plugin: src.plugins.config\n", encoding="utf-8"
    )
    (tmp_path / "prof.yml").write_text("name: pf\nbundles: [base]\n", encoding="utf-8")
    tree = load_tree_from_dir(str(tmp_path))
    assert tree.bundles() == ["base"]
    assert tree.profiles() == ["pf"]
