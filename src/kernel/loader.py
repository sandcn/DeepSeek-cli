"""插件发现与加载 — 目录扫描 + 模块内的 Plugin 提取。

用于「一切皆插件」的分发与热挂载：插件可以放在约定目录中，运行时由内核
扫描、导入、解析为 Plugin 并挂载到插件树上。
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import sys
from typing import List

from .errors import PluginError
from .plugin import Plugin
from .refs import resolve

_logger = logging.getLogger(__name__)

#: 文件插件模块名前缀（:func:`load_module` 生成；``kernel.watch`` / ``tools.cordis``
#: 依赖该格式从 ``fiber.definition.source`` 反推插件文件名）
MODULE_NAME_PREFIX = "_dsh_plugin_"


def module_name_for(path: str) -> str:
    """返回文件插件的稳定模块名（同一文件恒定，不同文件不冲突）。

    以绝对路径 SHA1 前 8 位 + 文件名 stem 构成：同一文件反复热重载复用同一个
    ``sys.modules`` 键（不再随 mtime 累积泄漏），不同路径互不覆盖。
    """
    abs_path = os.path.abspath(path)
    digest = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:8]
    stem = os.path.splitext(os.path.basename(path))[0]
    return f"{MODULE_NAME_PREFIX}{digest}_{stem}"


def load_module(path: str):
    """按文件路径导入模块（模块名稳定，可热重载且不泄漏 ``sys.modules``）。

    ★ 从 bytes 源码 ``compile`` + ``exec``（绕过 importlib 的 ``__pycache__``
    字节码缓存）——同一秒内重写文件（mtime 秒级、size 未变）时，仍会执行到
    最新源码，保证文件热重载确定性生效；模块名与 mtime 无关，故同一文件在
    进程生命周期内只占一个 ``sys.modules`` 键。
    """
    if not os.path.isfile(path):
        raise PluginError(f"插件文件不存在: {path}")
    abs_path = os.path.abspath(path)
    module_name = module_name_for(abs_path)
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法从路径加载插件: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    with open(abs_path, "rb") as handle:
        source = handle.read()
    try:
        code = compile(source, abs_path, "exec")
        exec(code, module.__dict__)
    except BaseException:
        # 加载失败不留半成品模块（否则同名后续加载会拿到损坏对象）
        sys.modules.pop(module_name, None)
        raise
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
    "module_name_for",
    "MODULE_NAME_PREFIX",
    "plugins_from_module",
    "discover_plugins",
    "discover_plugin_entries",
    "entry_points_plugins",
]
