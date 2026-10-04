"""插件管理器 — 发现 / 安装 / 卸载（对应 ``python chat.py plugin ...``）。

插件分发面向 Python 生态：
- **发现**：内置 Profile/Bundle/Patch + 外部清单目录（``./plugins``、
  ``~/.chat_config/plugins``）+ 插件文件（``*.py`` 热挂载）+ entry-points
  （``importlib.metadata`` group ``dsh.plugins``）。
- **安装**：把本地插件文件/目录复制进 ``~/.chat_config/plugins/``（记录来源
  元数据），下次启动由组合根自动发现并挂载。
- **卸载**：按名删除已安装的插件文件/目录。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any, Dict, List

from ..kernel.loader import entry_points_plugins
from ..kernel.config_tree import ConfigTree
from .bootstrap import EXTERNAL_PLUGIN_DIRS, resolve_entries
from .manifest import DEFAULT_PROFILE, build_config_tree

_logger = logging.getLogger(__name__)

_PRIMARY_DIR = os.path.join(os.path.expanduser("~"), ".chat_config", "plugins")
_REGISTRY_FILE = ".plugin-registry.json"


def plugins_dir() -> str:
    """安装目录（``~/.chat_config/plugins``），不存在时创建。"""
    os.makedirs(_PRIMARY_DIR, exist_ok=True)
    return _PRIMARY_DIR


def _load_registry(directory: str) -> Dict[str, Any]:
    path = os.path.join(directory, _REGISTRY_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(directory: str, data: Dict[str, Any]) -> None:
    path = os.path.join(directory, _REGISTRY_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def discover_plugin_files(directory: str) -> List[str]:
    """列出目录内的插件条目名（``*.py`` / 子目录）。"""
    if not os.path.isdir(directory):
        return []
    names = []
    for name in sorted(os.listdir(directory)):
        if name.startswith("."):
            continue
        full = os.path.join(directory, name)
        if os.path.isfile(full) and name.endswith(".py"):
            names.append(name)
        elif os.path.isdir(full):
            names.append(name + os.sep)
    return names


def list_plugins(profile: str = DEFAULT_PROFILE, *, tree: ConfigTree = None) -> dict:
    """汇总当前插件生态（清单条目 + 外部目录 + entry-points + 已安装）。"""
    entries = resolve_entries(profile, tree=tree)
    tree = tree or build_config_tree()
    external = []
    for directory in EXTERNAL_PLUGIN_DIRS:
        if os.path.isdir(directory):
            external.append({
                "dir": directory,
                "files": discover_plugin_files(directory),
            })
    installed = _load_registry(plugins_dir())
    eps = entry_points_plugins()
    return {
        "profile": profile,
        "profiles": list(tree.profiles()),
        "bundles": list(tree.bundles()),
        "plugins": [entry.to_dict() for entry in entries],
        "external_dirs": external,
        "installed": installed,
        "entry_points": [
            {"name": name, "plugin": getattr(plug, "name", str(plug))} for name, plug in eps
        ],
    }


def format_plugins(summary: dict) -> str:
    """把 ``list_plugins`` 结果格式化为可读文本。"""
    lines = []
    lines.append(f"profile: {summary['profile']}")
    lines.append(f"  profiles: [{', '.join(summary['profiles'])}]")
    lines.append(f"  bundles: [{', '.join(summary['bundles'])}]")
    lines.append("  plugins:")
    for entry in summary["plugins"]:
        marker = " [disabled]" if entry.get("disabled") else ""
        lines.append(f"    - {entry['id']}{marker}  ({entry.get('plugin')})")
    lines.append("  external_dirs:")
    if not summary["external_dirs"]:
        lines.append("    (none)")
    for item in summary["external_dirs"]:
        lines.append(f"    - {item['dir']}: [{', '.join(item['files']) or 'empty'}]")
    lines.append("  installed:")
    if not summary["installed"]:
        lines.append("    (none)")
    for name, meta in sorted(summary["installed"].items()):
        source = meta.get("source", "") if isinstance(meta, dict) else str(meta)
        lines.append(f"    - {name}  <- {source}")
    lines.append("  entry_points:")
    if not summary["entry_points"]:
        lines.append("    (none)")
    for item in summary["entry_points"]:
        lines.append(f"    - {item['name']} -> {item['plugin']}")
    return "\n".join(lines)


def add_plugin(source: str, *, name: str = "") -> str:
    """把一个本地插件文件/目录安装到 ``~/.chat_config/plugins/``。

    Returns:
        安装后的条目名（文件/目录名）。
    """
    if not source:
        raise ValueError("插件来源不能为空")
    directory = plugins_dir()
    if not os.path.exists(source):
        raise FileNotFoundError(f"插件来源不存在: {source}")

    base = name or os.path.basename(os.path.normpath(source))
    target = os.path.join(directory, base)
    if os.path.isdir(source):
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(source, target)
    else:
        if not source.endswith(".py"):
            raise ValueError(f"仅支持 .py 文件或目录: {source}")
        os.makedirs(directory, exist_ok=True)
        shutil.copy2(source, target)

    registry = _load_registry(directory)
    registry[base] = {"source": os.path.abspath(source)}
    _save_registry(directory, registry)
    return base


def remove_plugin(name: str) -> bool:
    """卸载已安装的插件（文件/目录）。返回是否确实存在并删除。"""
    directory = plugins_dir()
    target = os.path.join(directory, name)
    removed = False
    if os.path.isdir(target):
        shutil.rmtree(target)
        removed = True
    elif os.path.isfile(target):
        os.remove(target)
        removed = True

    registry = _load_registry(directory)
    if name in registry:
        registry.pop(name, None)
        _save_registry(directory, registry)
        removed = True
    return removed


__all__ = [
    "plugins_dir",
    "discover_plugin_files",
    "list_plugins",
    "format_plugins",
    "add_plugin",
    "remove_plugin",
]
