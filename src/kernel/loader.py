"""插件发现与加载 — 目录扫描 + 模块内的 Plugin 提取。

用于「一切皆插件」的分发与热挂载：插件可以放在约定目录中，运行时由内核
扫描、导入、解析为 Plugin 并挂载到插件树上。
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import List

from .errors import PluginError
from .plugin import Plugin
from .refs import resolve

_logger = logging.getLogger(__name__)


def load_module(path: str):
    """按文件路径导入模块（每次调用以唯一模块名隔离，便于热重载）。"""
    if not os.path.isfile(path):
        raise PluginError(f"插件文件不存在: {path}")
    abs_path = os.path.abspath(path)
    stamp = f"{int(os.path.getmtime(path))}"
    module_name = f"_dsh_plugin_{os.path.splitext(os.path.basename(path))[0]}_{stamp}"
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法从路径加载插件: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def plugins_from_module(module, module_name: str = "") -> List[Plugin]:
    """提取模块内定义的 Plugin（按定义顺序去重）。"""
    found: List[Plugin] = []
    seen: set[int] = set()
    for obj in vars(module).values():
        if isinstance(obj, Plugin):
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            found.append(obj)
    if found:
        return found
    apply_fn = getattr(module, "apply", None)
    if callable(apply_fn):
        return [resolve(apply_fn)]
    return []


def discover_plugins(directory: str) -> List[Plugin]:
    """扫描目录内所有 ``*.py``，返回提取到的 Plugin 列表。"""
    if not os.path.isdir(directory):
        return []
    plugins: List[Plugin] = []
    for name in sorted(os.listdir(directory)):
        if name.startswith("_") or not name.endswith(".py"):
            continue
        path = os.path.join(directory, name)
        try:
            module = load_module(path)
        except Exception:
            _logger.exception("加载插件文件失败: %s", path)
            continue
        plugins.extend(plugins_from_module(module, name[:-3]))
    return plugins


def discover_plugin_entries(directory: str) -> List[tuple]:
    """扫描目录，返回 (插件名, Plugin) 的稳定有序列表。"""
    entries = [(plugin.name, plugin) for plugin in discover_plugins(directory)]
    entries.sort(key=lambda item: item[0])
    return entries


def entry_points_plugins(group: str = "dsh.plugins") -> List[tuple]:
    """从 Python entry-points 收集插件（无 entry-points 时返回空）。"""
    result: List[tuple] = []
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python 3.9 始终可用
        return result
    try:
        eps = metadata.entry_points()
        if hasattr(eps, "select"):
            selected = eps.select(group=group)
        else:  # pragma: no cover - 兼容旧接口
            selected = eps.get(group, [])
    except Exception:
        return result
    for ep in selected:
        try:
            result.append((ep.name, resolve(ep.value)))
        except Exception:
            _logger.exception("entry-point 插件加载失败: %s", ep)
    return result


__all__ = [
    "load_module",
    "plugins_from_module",
    "discover_plugins",
    "discover_plugin_entries",
    "entry_points_plugins",
]
