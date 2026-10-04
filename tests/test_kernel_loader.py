"""插件发现与引用解析测试 — 目录扫描、模块提取、refs 解析。"""

from __future__ import annotations

import os

import pytest

from src.kernel import PluginError
from src.kernel.loader import (
    discover_plugin_entries,
    discover_plugins,
    load_module,
    plugins_from_module,
)
from src.kernel.plugin import Plugin, plugin
from src.kernel.refs import resolve


@plugin("alpha")
def alpha(ctx):
    ctx.provide("alpha", 1)


@plugin("beta")
def beta(ctx):
    ctx.provide("beta", 2)


def test_resolve_plugin_passthrough():
    assert resolve(alpha) is alpha


def test_resolve_module_attr():
    resolved = resolve("tests.test_kernel_loader:alpha")
    assert resolved.name == "alpha"


def test_resolve_dotted_attr():
    resolved = resolve("tests.test_kernel_loader.alpha")
    assert resolved.name == "alpha"


def test_resolve_module_prefers_single_plugin():
    # 本模块有两个 Plugin，模块级解析应报错要求显式指定
    with pytest.raises(PluginError):
        resolve("tests.test_kernel_loader")


def test_resolve_missing_module_raises():
    with pytest.raises(PluginError):
        resolve("no_such_module_xyz")


def test_resolve_missing_attr_raises():
    with pytest.raises(PluginError):
        resolve("tests.test_kernel_loader:missing_attr")


def test_discover_plugins_from_directory(tmp_path):
    plugin_file = tmp_path / "ext_plugin.py"
    plugin_file.write_text(
        "from src.kernel import plugin\n"
        "@plugin('ext')\n"
        "def apply(ctx):\n"
        "    ctx.provide('ext', 1)\n",
        encoding="utf-8",
    )
    found = discover_plugins(str(tmp_path))
    assert [p.name for p in found] == ["ext"]

    entries = discover_plugin_entries(str(tmp_path))
    assert entries[0][0] == "ext"


def test_discover_plugins_ignores_underscore(tmp_path):
    (tmp_path / "_private.py").write_text("@plugin = None\n", encoding="utf-8")
    assert discover_plugins(str(tmp_path)) == []


def test_discover_plugins_missing_dir():
    assert discover_plugins("/nonexistent/dir/xyz") == []


def test_load_module_by_path(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("VALUE = 42\n", encoding="utf-8")
    module = load_module(str(path))
    assert module.VALUE == 42


def test_load_module_missing_file_raises():
    with pytest.raises(PluginError):
        load_module("/nonexistent/file.py")


def test_plugins_from_module_with_apply(tmp_path):
    path = tmp_path / "p.py"
    path.write_text(
        "from src.kernel import plugin\n"
        "@plugin('p1')\n"
        "def apply(ctx):\n"
        "    pass\n",
        encoding="utf-8",
    )
    module = load_module(str(path))
    found = plugins_from_module(module, "p")
    assert len(found) == 1
    assert isinstance(found[0], Plugin)


def test_entry_points_plugins_returns_list():
    from src.kernel.loader import entry_points_plugins

    assert isinstance(entry_points_plugins("nonexistent.group"), list)
