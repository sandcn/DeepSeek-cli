"""组合根 — 从 Profile 组装内核插件树并启动。

``build_kernel`` 是「一切皆插件」的应用组合根：解析 Profile/Bundle/Patch，
叠加用户 Overlay（``--patch`` / ``~/.chat_config/profiles/<profile>/`` /
``~/.chat_config/cordis.patch.yml``），把插件逐个挂载到内核，等待依赖稳定，
并登记为进程级当前内核。

除清单声明的插件外，组合根还负责两类「分发渠道」的挂载：

- **外部目录插件**：``./plugins``、``~/.chat_config/plugins`` 内的 ``*.py``；
- **entry-points**：已安装 Python 包声明的 ``dsh.plugins`` 组。

组合根同时把「清单已接管的工具名 / 命令名」注入对应聚合插件
（``tools_builtin`` / ``commands``），使单个工具或命令的 overlay 禁用不会被
兜底注册重新引入。
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from ..kernel import Kernel, format_config_dump, materialize, set_current_kernel
from ..kernel.config_tree import ConfigTree, ResolvedPlugin
from ..kernel.loader import discover_plugin_entries, entry_points_plugins
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

#: 单条目声明插件名 → 其 config 中作为「名」的键
#: （LLM provider 是每 provider 一个独立插件名，故逐项列出）
_SINGLE_DECL_PLUGINS = {
    "invariant": "name",
    "llm_provider_deepseek": "name",
    "llm_provider_anthropic": "name",
    "llm_provider_ollama": "name",
    "llm_provider_openai_compat": "name",
    "prompt_section": "name",
    "tool": "name",
    "tool_metadata_entry": "name",
    "tool_const_entry": "name",
    "event_type": "name",
    "named_style": "name",
    "global_disabled_tool": "name",
    "command": "name",
    "renderer_handler": "id",
    "renderer_filter": "id",
    "middleware": "id",
    "agent_type": "name",
    "stream_handler": "id",
    "notification_backend": "id",
    "context_strategy": "name",
    "mcp_transport": "name",
    "tool_engine": "id",
    "consumer": "id",
    "ui_view": "id",
    "web_search_provider": "id",
    "web_fetch_provider": "id",
    "theme": "name",
    "preset": "name",
    "prompt_mode": "name",
    "prompt_source": "name",
    "clawbot_command": "name",
    "subcommand": "name",
    "skill_source": "id",
    "session_projection": "name",
    "renderer_target": "id",
    "keybinding": "id",
    "special_key": "id",
    "tool_style": "id",
    "syntax_language": "id",
    "presentation_data": "id",
    "presentation_data_table": "id",
    "host": "id",
    "completion_provider": "id",
    "status_segment": "id",
}

#: 单条目声明插件名 → 汇入的托管分组
_MANAGED_GROUP = {
    "invariant": "managed_invariants",
    "llm_provider_deepseek": "managed_llm_providers",
    "llm_provider_anthropic": "managed_llm_providers",
    "llm_provider_ollama": "managed_llm_providers",
    "llm_provider_openai_compat": "managed_llm_providers",
    "prompt_section": "managed_prompt_sections",
    "tool": "managed_tools",
    "tool_metadata_entry": "managed_tool_metadata",
    "tool_const_entry": "managed_tool_consts",
    "event_type": "managed_event_types",
    "named_style": "managed_named_styles",
    "global_disabled_tool": "managed_global_disabled_tools",
    "command": "managed_commands",
    "renderer_handler": "managed_handlers",
    "renderer_filter": "managed_filters",
    "middleware": "managed_middlewares",
    "agent_type": "managed_agent_types",
    "stream_handler": "managed_stream_handlers",
    "notification_backend": "managed_notification_backends",
    "context_strategy": "managed_strategies",
    "mcp_transport": "managed_mcp_transports",
    "tool_engine": "managed_tool_engines",
    "consumer": "managed_consumers",
    "ui_view": "managed_ui_views",
    "web_search_provider": "managed_search_providers",
    "web_fetch_provider": "managed_fetch_providers",
    "theme": "managed_themes",
    "preset": "managed_presets",
    "prompt_mode": "managed_prompt_modes",
    "prompt_source": "managed_prompt_sources",
    "clawbot_command": "managed_clawbot_commands",
    "subcommand": "managed_subcommands",
    "skill_source": "managed_skill_sources",
    "session_projection": "managed_session_projections",
    "renderer_target": "managed_render_targets",
    "keybinding": "managed_keybindings",
    "special_key": "managed_special_keys",
    "tool_style": "managed_tool_styles",
    "syntax_language": "managed_syntax_languages",
    "presentation_data": "managed_presentation_data",
    "presentation_data_table": "managed_presentation_data",
    "host": "managed_hosts",
    "completion_provider": "managed_completion_providers",
    "status_segment": "managed_status_segments",
}

#: 只统计「启用」条目的托管分组（命令沿用旧语义；工具/渲染/中间件把禁用项也
#: 计入，使聚合插件抑制其默认装配，从而 overlay disable 真正生效）
_ENABLED_ONLY_GROUPS = frozenset({"managed_commands"})

#: 聚合插件的插件名 → 注入的 config 键（收集自清单中同类的单条目声明）
_AGGREGATE_MANAGED_KEYS = {
    "invariants": ("managed_invariants",),
    "llm": ("managed_llm_providers",),
    "tools_builtin": ("managed_tools",),
    "tool_metadata": ("managed_tool_metadata",),
    "tool_consts": ("managed_tool_consts",),
    "event_types": ("managed_event_types",),
    "named_styles": ("managed_named_styles",),
    "policy": ("managed_global_disabled_tools",),
    "commands": ("managed_commands",),
    "renderer_builtin": ("managed_handlers", "managed_filters"),
    "agent_middleware": ("managed_middlewares",),
    "subagents": ("managed_agent_types",),
    "stream": ("managed_stream_handlers",),
    "notifications": ("managed_notification_backends",),
    "context": ("managed_strategies",),
    "mcp": ("managed_mcp_transports",),
    "tool_scheduler": ("managed_tool_engines",),
    "consumers": ("managed_consumers",),
    "ui": ("managed_ui_views",),
    "web_search": ("managed_search_providers",),
    "web_fetch": ("managed_fetch_providers",),
    "themes": ("managed_themes",),
    "skill_sources": ("managed_skill_sources",),
    "session_projections": ("managed_session_projections",),
    "renderer": ("managed_render_targets",),
    "presets": ("managed_presets",),
    "prompt": ("managed_prompt_modes", "managed_prompt_sources", "managed_prompt_sections"),
    "clawbot": ("managed_clawbot_commands",),
    "app": ("managed_subcommands",),
    "keybindings": ("managed_keybindings",),
    "special_keys": ("managed_special_keys",),
    "tool_styles": ("managed_tool_styles",),
    "syntax": ("managed_syntax_languages",),
    "presentation_data": ("managed_presentation_data",),
    "hosts": ("managed_hosts",),
    "completion_providers": ("managed_completion_providers",),
    "status_segments": ("managed_status_segments",),
}


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


# ── 清单接管的工具/命令名收集（供聚合插件兜底跳过） ─────────


def collect_managed_names(resolved) -> dict:
    """从解析后的插件条目收集「清单已接管的工具/命令/渲染/中间件名」。

    Args:
        resolved: ``materialize(entries)`` 的结果（``(entry, Plugin)`` 列表）。

    Returns:
        形如 ``{"managed_tools": [...], "managed_commands": [...],
        "managed_handlers": [...], "managed_filters": [...],
        "managed_middlewares": [...]}``。

    语义差异（由各自的聚合插件兜底方式决定）：

    - ``managed_tools`` / ``managed_handlers`` / ``managed_filters`` /
      ``managed_middlewares`` 含**被禁用**的项——聚合插件据此抑制默认装配，
      使 overlay 禁用单项真正生效；
    - ``managed_commands`` 只含**启用**的命令名——``ctx.commands`` 只注册清单
      接管的命令，被禁用的命令不会被兜底注册（命令无自动发现兜底路径）。
    """
    managed = {group: set() for group in set(_MANAGED_GROUP.values())}
    for entry, plug in resolved:
        name = getattr(plug, "name", "")
        group = _MANAGED_GROUP.get(name)
        if group is None:
            continue
        value = (entry.config or {}).get(_SINGLE_DECL_PLUGINS[name])
        if not value:
            continue
        if group in _ENABLED_ONLY_GROUPS and entry.disabled:
            continue
        managed[group].add(str(value))
    return {key: sorted(value) for key, value in managed.items()}


def inject_managed_config(resolved) -> None:
    """把清单接管的工具/命令/渲染/中间件名注入聚合插件条目（原地修改 entry.config）。"""
    managed = collect_managed_names(resolved)
    for entry, plug in resolved:
        managed_keys = _AGGREGATE_MANAGED_KEYS.get(getattr(plug, "name", ""))
        if not managed_keys:
            continue
        config = dict(entry.config or {})
        for key in managed_keys:
            config[key] = list(managed[key])
        entry.config = config


# ── 构建 ─────────────────────────────────────────────────


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
    resolved = materialize(entries)
    inject_managed_config(resolved)

    kernel = Kernel(name="chat", profile=profile)
    for entry, plug in resolved:
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

    # entry-points（Python 包声明的 ``dsh.plugins`` 组）自动发现并挂载
    if discover_external:
        for name, plug in entry_points_plugins():
            try:
                kernel.mount(plug)
            except Exception:
                _logger.exception("挂载 entry-point 插件失败: %s", name)

    await kernel.settle()
    # 严格模式（生产路径）：凡由 Profile 插件树 ``provide`` 声明的服务，运行期
    # 缺失即显式失败（不再静默回退直接 import / 进程级单例）。
    from ..kernel.runtime import activate_strict

    activate_strict(declared_provide_keys(resolved), kernel=kernel)
    set_current_kernel(kernel)
    return kernel


def declared_provide_keys(resolved) -> list:
    """从解析后的插件条目收集所有已声明提供的服务 key（含被禁用条目？否）。"""
    keys = set()
    for entry, plug in resolved:
        if getattr(entry, "disabled", False):
            continue
        for key in getattr(plug, "provide", ()) or ():
            keys.add(str(key))
    return sorted(keys)


async def shutdown_kernel(kernel: Optional[Kernel]) -> None:
    """卸载内核并清除进程级当前内核。"""
    if kernel is None:
        return
    await kernel.dispose()
    from ..kernel import get_current_kernel

    if get_current_kernel() is kernel:
        set_current_kernel(None)
    from ..kernel.runtime import deactivate_strict

    deactivate_strict()


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
    "collect_managed_names",
    "inject_managed_config",
    "load_overlay_file",
    "EXTERNAL_PLUGIN_DIRS",
    "PROFILES_HOME",
]
