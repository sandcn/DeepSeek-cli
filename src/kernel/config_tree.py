"""配置组装层 — Profile / Bundle / Patch。

一棵插件树如何被逐层组装出来（引用 DeepSeek Harness 的 Profile/Bundle/Patch）：

- **Bundle**：一组插件条目（清单），可包含其它 bundle；
- **Profile**：选择一组 bundle，并叠加一组 Patch；
- **Patch**：按插件 id 定位配置项，覆盖其完整 config（可另置 disabled）。

``ConfigTree.resolve(profile)`` 产出最终有序的插件清单，
``ConfigTree.dump(profile)`` 产出可直接展示的最终运行时配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

from .errors import PluginError
from .plugin import Plugin
from .refs import resolve as resolve_ref


@dataclass
class PluginSpec:
    """bundle 中的一条插件条目。"""

    id: str
    plugin: Any
    config: dict = field(default_factory=dict)
    disabled: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "plugin": _ref_repr(self.plugin),
            "config": dict(self.config),
            "disabled": self.disabled,
        }


@dataclass
class Bundle:
    """插件清单（bundle）。"""

    id: str
    plugins: List[PluginSpec] = field(default_factory=list)
    includes: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class Patch:
    """按 id 覆盖插件配置。"""

    target: str
    config: Optional[dict] = None
    disabled: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "config": dict(self.config) if self.config is not None else None,
            "disabled": self.disabled,
        }


@dataclass
class Profile:
    """运行配置（profile）。"""

    name: str
    bundles: List[str] = field(default_factory=list)
    patches: List[Patch] = field(default_factory=list)
    description: str = ""


@dataclass
class ResolvedPlugin:
    """解析后的插件条目。"""

    id: str
    plugin: Any
    config: dict
    disabled: bool
    bundle: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bundle": self.bundle,
            "plugin": _ref_repr(self.plugin),
            "disabled": self.disabled,
            "config": dict(self.config),
        }


class ConfigTree:
    """Profile / Bundle / Patch 注册表与解析器。"""

    def __init__(self) -> None:
        self._bundles: dict[str, Bundle] = {}
        self._profiles: dict[str, Profile] = {}

    # ── 注册 ─────────────────────────────────────────────

    def add_bundle(self, bundle: Bundle) -> None:
        if bundle.id in self._bundles:
            raise PluginError(f"bundle 重复: {bundle.id!r}")
        self._bundles[bundle.id] = bundle

    def add_profile(self, profile: Profile) -> None:
        if profile.name in self._profiles:
            raise PluginError(f"profile 重复: {profile.name!r}")
        self._profiles[profile.name] = profile

    def bundles(self) -> list[str]:
        return list(self._bundles)

    def profiles(self) -> list[str]:
        return list(self._profiles)

    def bundle(self, bundle_id: str) -> Bundle:
        try:
            return self._bundles[bundle_id]
        except KeyError:
            raise PluginError(f"未注册的 bundle: {bundle_id!r}") from None

    def profile(self, name: str) -> Profile:
        try:
            return self._profiles[name]
        except KeyError:
            raise PluginError(f"未注册的 profile: {name!r}") from None

    # ── 解析 ─────────────────────────────────────────────

    def resolve(self, profile_name: str) -> List[ResolvedPlugin]:
        """解析 profile，产出最终插件清单（含 patch 覆盖）。"""
        profile = self.profile(profile_name)
        specs: List[PluginSpec] = []
        seen_bundles: set[str] = set()
        for bundle_id in profile.bundles:
            specs.extend(self._collect_bundle(bundle_id, seen_bundles))

        plugins: List[ResolvedPlugin] = []
        index: dict[str, int] = {}
        for spec in specs:
            if spec.id in index:
                raise PluginError(f"插件 id 冲突: {spec.id!r}")
            index[spec.id] = len(plugins)
            plugins.append(
                ResolvedPlugin(
                    id=spec.id,
                    plugin=spec.plugin,
                    config=dict(spec.config),
                    disabled=bool(spec.disabled),
                    bundle=spec.id.split("::", 1)[0],
                )
            )

        for patch in profile.patches:
            if patch.target not in index:
                raise PluginError(
                    f"patch 目标不存在: {patch.target!r}（profile={profile_name}）"
                )
            entry = plugins[index[patch.target]]
            if patch.config is not None:
                entry.config = dict(patch.config)
            if patch.disabled is not None:
                entry.disabled = bool(patch.disabled)
        return plugins

    def _collect_bundle(self, bundle_id: str, seen: set[str]) -> List[PluginSpec]:
        if bundle_id in seen:
            return []
        seen.add(bundle_id)
        bundle = self.bundle(bundle_id)
        specs: List[PluginSpec] = []
        for included in bundle.includes:
            specs.extend(self._collect_bundle(included, seen))
        for spec in bundle.plugins:
            tagged = PluginSpec(
                id=f"{bundle_id}::{spec.id}",
                plugin=spec.plugin,
                config=dict(spec.config),
                disabled=spec.disabled,
            )
            specs.append(tagged)
        return specs

    # ── dump ─────────────────────────────────────────────

    def dump(self, profile_name: str) -> dict:
        """产出最终运行时配置（可直接打印）。"""
        profile = self.profile(profile_name)
        resolved = self.resolve(profile_name)
        return {
            "profile": profile.name,
            "description": profile.description,
            "bundles": list(profile.bundles),
            "patches": [patch.to_dict() for patch in profile.patches],
            "plugins": [entry.to_dict() for entry in resolved],
        }

    # ── 清单导入 ─────────────────────────────────────────

    def add_bundle_dict(self, data: dict) -> Bundle:
        bundle_id = data.get("id")
        if not bundle_id:
            raise PluginError("bundle 清单缺少 id")
        plugins = []
        for item in data.get("plugins", []) or []:
            plugins.append(_spec_from_dict(item))
        bundle = Bundle(
            id=bundle_id,
            plugins=plugins,
            includes=list(data.get("includes", []) or []),
            description=data.get("description", ""),
        )
        self.add_bundle(bundle)
        return bundle

    def add_profile_dict(self, data: dict) -> Profile:
        name = data.get("name")
        if not name:
            raise PluginError("profile 清单缺少 name")
        patches = []
        for item in data.get("patches", []) or []:
            patches.append(
                Patch(
                    target=item["target"],
                    config=item.get("config"),
                    disabled=item.get("disabled"),
                )
            )
        profile = Profile(
            name=name,
            bundles=list(data.get("bundles", []) or []),
            patches=patches,
            description=data.get("description", ""),
        )
        self.add_profile(profile)
        return profile


def _spec_from_dict(item: dict) -> PluginSpec:
    if "id" not in item:
        raise PluginError(f"插件条目缺少 id: {item!r}")
    if "plugin" not in item:
        raise PluginError(f"插件条目缺少 plugin: {item!r}")
    return PluginSpec(
        id=item["id"],
        plugin=item["plugin"],
        config=dict(item.get("config", {}) or {}),
        disabled=bool(item.get("disabled", False)),
    )


def _ref_repr(plugin: Any) -> str:
    if isinstance(plugin, Plugin):
        return f"{plugin.source}:{plugin.name}" if plugin.source else plugin.name
    if isinstance(plugin, str):
        return plugin
    if isinstance(plugin, type):
        return f"{plugin.__module__}.{plugin.__name__}"
    return getattr(plugin, "__name__", repr(plugin))


def materialize(entries: Iterable[ResolvedPlugin]) -> List[tuple]:
    """把解析结果中的引用字符串解析为 Plugin（返回 (entry, Plugin) 列表）。"""
    result = []
    for entry in entries:
        plugin = entry.plugin
        if isinstance(plugin, str):
            plugin = resolve_ref(plugin)
        result.append((entry, plugin))
    return result


__all__ = [
    "PluginSpec",
    "Bundle",
    "Patch",
    "Profile",
    "ResolvedPlugin",
    "ConfigTree",
    "materialize",
]
