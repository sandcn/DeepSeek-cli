"""插件清单条目声明与组装分离的守卫。

``src/plugins/manifest.py`` 曾把 40 余组条目内联为 900+ 行字面量，新增条目
冲突与漏改风险高。拆分后：

- ``src/plugins/manifest_entries`` 只声明 ``*_ENTRIES``（条目定义）；
- ``src/plugins/manifest`` 只做 Bundle/Profile 组装与 ``build_config_tree``，
  并按名 re-export 全部条目（旧导入路径兼容）。

本测试固化拆分后的不变量：

1. 条目名在 ``manifest_entries.__all__`` 与 ``manifest`` 的导入/``__all__``
   之间完整对齐（防漏 re-export）；
2. ``BUNDLES`` 中引用的条目变量都存在（防重命名后漏改）；
3. 所有插件条目 id 全局唯一（Bundle 内与跨 Bundle）；
4. re-export 的条目对象与 ``manifest_entries`` 中的**同一对象**（非拷贝）；
5. ``build_config_tree()`` 仍能解析出全部内置 Bundle/Profile。
"""

from __future__ import annotations

from src.plugins import manifest, manifest_entries


def _entry_names_used_in_bundles() -> set[str]:
    """从 BUNDLES 字面量里提取 ``list(XXX_ENTRIES)`` 形式引用的条目名。"""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(manifest.__file__).read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "list":
            if node.args and isinstance(node.args[0], ast.Name):
                used.add(node.args[0].id)
    return used


def test_manifest_entries_all_matches_declared_entries():
    missing = [
        name for name in manifest_entries.__all__
        if not hasattr(manifest_entries, name)
    ]
    assert not missing, f"manifest_entries.__all__ 声明了不存在的条目: {missing}"


def test_manifest_reexports_every_entry():
    missing = [
        name for name in manifest_entries.__all__
        if not hasattr(manifest, name)
    ]
    assert not missing, f"manifest 未 re-export 以下条目: {missing}"
    listed = set(manifest.__all__)
    assert set(manifest_entries.__all__) <= listed, (
        f"manifest.__all__ 缺少条目: {sorted(set(manifest_entries.__all__) - listed)}"
    )


def test_manifest_reexports_same_objects():
    for name in manifest_entries.__all__:
        assert getattr(manifest, name) is getattr(manifest_entries, name), name


def test_bundle_entry_references_exist():
    used = _entry_names_used_in_bundles()
    assert used, "未从 BUNDLES 中解析到任何条目引用（结构可能已变）"
    unknown = [name for name in used if not hasattr(manifest_entries, name)]
    assert not unknown, f"BUNDLES 引用了不存在的条目: {unknown}"


def test_all_bundle_plugin_ids_are_unique_within_bundle():
    for bundle in manifest.BUNDLES:
        ids = [plugin["id"] for plugin in bundle.get("plugins", [])]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"Bundle {bundle['id']!r} 内条目 id 重复: {sorted(duplicates)}"


def test_entry_ids_are_unique_across_bundles():
    """跨 Bundle 的条目 id 允许重复（同一插件可被多个 Bundle 声明），
    但**同一条目名**产出的 id 必须稳定且不以空串结尾。"""
    for name in manifest_entries.__all__:
        entries = getattr(manifest_entries, name)
        assert isinstance(entries, list), f"{name} 应为 list"
        for entry in entries:
            assert entry.get("id"), f"{name} 中存在缺少 id 的条目: {entry!r}"
            assert entry.get("plugin"), f"{name} 中条目 {entry['id']} 缺少 plugin"


def test_build_config_tree_resolves_all_profiles():
    tree = manifest.build_config_tree()
    for profile in manifest.PROFILES:
        resolved = tree.resolve(profile["name"])
        assert resolved, f"Profile {profile['name']!r} 解析为空"
    assert manifest.DEFAULT_PROFILE == "cli"


def test_build_config_tree_is_stable_across_calls():
    first = manifest.build_config_tree().resolve("cli")
    second = manifest.build_config_tree().resolve("cli")
    assert [r.id for r in first] == [r.id for r in second]


def test_manifest_module_stays_small():
    """组装模块不应再回涨为巨型字面量文件（条目声明已拆出）。"""
    from pathlib import Path

    lines = len(Path(manifest.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 500, f"manifest.py 回涨到 {lines} 行，条目声明应放 manifest_entries"
