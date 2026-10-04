"""组合根 — 从 Profile 组装内核插件树并启动。

``build_kernel`` 是「一切皆插件」的应用组合根：解析 Profile/Bundle/Patch，
叠加用户 Overlay（``--patch`` / ``~/.chat_config/profiles/<profile>/`` /
``~/.chat_config/cordis.patch.yml``），把插件逐个挂载到内核，等待依赖稳定，
并登记为进程级当前内核。
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from ..kernel import Kernel, format_config_dump, materialize, set_current_kernel
from ..kernel.config_tree import ConfigTree, ResolvedPlugin
from ..kernel.loader import discover_plugin_entries
from ..kernel import manifest as kernel_manifest
from ..kernel.overlay import apply_overlay, normalize_overlay
from .manifest import DEFAULT_PROFILE, build_config_tree

_logger = logging.getLogger(__name__)

# 内置插件之外的可选发现目录
EXTERNAL_PLUGIN_DIRS = (
    os.path.join(os.getcwd(), "plugins"),
    os.path.join(os.path.expanduser("~"), ".chat_config", "plugins"),
)

# 用户 Profile 目录根（`~/.chat_config/profiles/<profile>/`）
PROFILES_HOME = os.path.join(os.path.expanduser("~"), ".chat_config", "profiles")

# 约定名（在 profile/home 目录中被识别为 overlay 而非清单）
_OVERLAY_NAMES = frozenset({
    "cordis.patch.yml", "cordis.patch.yaml", "cordis.patch.json",
    "overlay.yml", "overlay.yaml", "overlay.json",
})


# ── 清单合并 ─────────────────────────────────────────────


def _merge_manifests(tree: ConfigTree, directory: str, *, skip_names=frozenset()) -> None:
    """把目录内的 manifest 文件合并进配置树（忽略重复与非法文件）。"""
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        low = name.lower()
        if not low.endswith((".yml", ".yaml", ".json")) or low in skip_names:
            continue
        try:
            data = kernel_manifest.load_file(os.path.join(directory, name))
        except Exception:
            _logger.debug("清单文件加载失败: %s", name, exc_info=True)
            continue
        if not isinstance(data, dict):
            continue
        _merge_manifest_dict(tree, data)


def _merge_manifest_dict(tree: ConfigTree, data: dict) -> None:
    for bundle in data.get("bundles", []) or []:
        try:
            tree.add_bundle_dict(bundle)
        except Exception:
            _logger.debug("bundle 合并失败（可能重复）", exc_info=True)
    for profile in data.get("profiles", []) or []:
        try:
            tree.add_profile_dict(profile)
        except Exception:
            _logger.debug("profile 合并失败（可能重复）", exc_info=True)
    if "id" in data:
        try:
            tree.add_bundle_dict(data)
        except Exception:
            _logger.debug("bundle 合并失败（可能重复）", exc_info=True)
    elif "name" in data:
        try:
            tree.add_profile_dict(data)
        except Exception:
            _logger.debug("profile 合并失败（可能重复）", exc_info=True)


# ── Overlay 收集 ─────────────────────────────────────────


def load_overlay_file(path: str) -> dict:
    """加载单个 overlay 文件并归一化。"""
    return normalize_overlay(kernel_manifest.load_file(path))


def _collect_overlay_files(directory: str) -> dict:
    """收集目录内约定名 overlay 文件并合并（后者覆盖前者）。"""
    merged = {"insert": [], "replace": {}, "disable": set()}
    if not os.path.isdir(directory):
        return merged
    for name in sorted(os.listdir(directory)):
        if name.lower() not in _OVERLAY_NAMES:
            continue
        part = load_overlay_file(os.path.join(directory, name))
        merged["insert"].extend(part["insert"])
        merged["replace"].update(part["replace"])
        merged["disable"].update(part["disable"])
    return merged


def _merge_overlays(base: dict, extra: dict) -> dict:
    base["insert"].extend(extra["insert"])
    base["replace"].update(extra["replace"])
    base["disable"].update(extra["disable"])
    return base


def collect_overlay(profile: str, patch_paths: Optional[List[str]] = None) -> dict:
    """收集 profile 目录 + home 级 + 显式 ``--patch`` 的 overlay（按序叠加）。"""
    merged = {"insert": [], "replace": {}, "disable": set()}
    profile_dir = os.path.join(PROFILES_HOME, profile)
    _merge_overlays(merged, _collect_overlay_files(profile_dir))
    _merge_overlays(
        merged, _collect_overlay_files(os.path.join(os.path.expanduser("~"), ".chat_config"))
    )
    for path in patch_paths or []:
        _merge_overlays(merged, load_overlay_file(path))
    return merged


# ── 解析 ─────────────────────────────────────────────────


def resolve_entries(
    profile: str,
    *,
    tree: Optional[ConfigTree] = None,
    patch_paths: Optional[List[str]] = None,
    discover_external: bool = True,
    extra_dirs: Optional[List[str]] = None,
) -> List[ResolvedPlugin]:
    """解析 profile → 合并外部清单 → 应用 overlay，返回最终插件条目。"""
    tree = tree or build_config_tree()

    if discover_external:
        for directory in list(EXTERNAL_PLUGIN_DIRS) + list(extra_dirs or []):
            _merge_manifests(tree, directory)

    # 用户 profile 目录内的清单（bundle/profile 扩展）
    profile_dir = os.path.join(PROFILES_HOME, profile)
    _merge_manifests(tree, profile_dir, skip_names=_OVERLAY_NAMES)

    if profile not in tree.profiles():
        raise ValueError(f"未知 profile: {profile!r}（可用: {tree.profiles()}）")

    entries = tree.resolve(profile)
    overlay = collect_overlay(profile, patch_paths)
    if overlay["insert"] or overlay["replace"] or overlay["disable"]:
        entries = apply_overlay(entries, overlay)
    return entries


async def build_kernel(
    profile: str = DEFAULT_PROFILE,
    *,
    tree: Optional[ConfigTree] = None,
    extra_dirs: Optional[List[str]] = None,
    discover_external: bool = True,
    patch_paths: Optional[List[str]] = None,
) -> Kernel:
    """构建并启动内核插件树。"""
    entries = resolve_entries(
        profile,
        tree=tree,
        patch_paths=patch_paths,
        discover_external=discover_external,
        extra_dirs=extra_dirs,
    )

    kernel = Kernel(name="chat", profile=profile)
    for entry, plug in materialize(entries):
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)

    # 目录扫描到的独立插件（非清单声明）自动挂载
    if discover_external:
        for directory in list(EXTERNAL_PLUGIN_DIRS) + list(extra_dirs or []):
            if not os.path.isdir(directory):
                continue
            for name, plug in discover_plugin_entries(directory):
                try:
                    kernel.mount(plug)
                except Exception:
                    _logger.exception("挂载外部插件失败: %s", name)

    await kernel.settle()
    set_current_kernel(kernel)
    return kernel


async def shutdown_kernel(kernel: Optional[Kernel]) -> None:
    """卸载内核并清除进程级当前内核。"""
    if kernel is None:
        return
    await kernel.dispose()
    if kernel is not None:
        from ..kernel import get_current_kernel

        if get_current_kernel() is kernel:
            set_current_kernel(None)


def dump_profile(
    profile: str = DEFAULT_PROFILE,
    *,
    tree: Optional[ConfigTree] = None,
    patch_paths: Optional[List[str]] = None,
) -> str:
    """产出最终运行时配置文本（供 ``--dump-config``）。

    每行显示插件 id、引用、inject / provide 与 config，等价于 dsh 的
    ``--dump-config``（打印插件树中任意条目，均可被自有 patch 顶替）。
    """
    entries = resolve_entries(profile, tree=tree, patch_paths=patch_paths)
    tree = tree or build_config_tree()
    plugins = []
    for entry, plug in materialize(entries):
        item = entry.to_dict()
        item["provides"] = list(getattr(plug, "provide", ()) or ())
        item["inject"] = list(getattr(plug, "inject", ()) or ())
        plugins.append(item)
    dump = {
        "profile": profile,
        "description": tree.profile(profile).description if profile in tree.profiles() else "",
        "bundles": list(tree.profile(profile).bundles) if profile in tree.profiles() else [],
        "patches": [patch.to_dict() for patch in tree.profile(profile).patches] if profile in tree.profiles() else [],
        "plugins": plugins,
    }
    return format_config_dump(dump)


__all__ = [
    "build_kernel",
    "shutdown_kernel",
    "dump_profile",
    "resolve_entries",
    "collect_overlay",
    "load_overlay_file",
    "EXTERNAL_PLUGIN_DIRS",
    "PROFILES_HOME",
]
