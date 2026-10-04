"""插件清单加载 — 从文件/字典构建 ConfigTree。

支持两种清单形态：

1. 单文件聚合：``{ "bundles": [...], "profiles": [...] }``；
2. 目录分散：目录内每个 ``*.yml`` / ``*.yaml`` / ``*.json`` 文件声明一个
   bundle 或 profile（由顶层键 ``id``/``name`` 区分）。

启发式：可用 PyYAML 时优先，否则使用内核自带的 YAML 子集解析器。
"""

from __future__ import annotations

import json
import os
from typing import Any, List

from .config_tree import Bundle, ConfigTree, Profile
from .errors import PluginError


def load_text(text: str, *, fmt: str = "") -> Any:
    """按格式解析清单文本。

    即使扩展名为 ``.yml``，以 ``{`` / ``[`` 开头的内容也按 JSON 解析
    （JSON 是 YAML 子集，且内置 YAML 解析器不支持流式语法，这样用户用 JSON
    写 overlay/清单也能工作）。
    """
    if fmt == "json":
        return json.loads(text)
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return _load_yaml(text)


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        from . import _yaml

        return _yaml.load(text)


def load_file(path: str) -> Any:
    """加载单个清单文件。"""
    if not os.path.isfile(path):
        raise PluginError(f"清单文件不存在: {path}")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    fmt = "yaml" if ext in ("yaml", "yml") else "json" if ext == "json" else ""
    return load_text(text, fmt=fmt)


def load_tree_from_dict(data: dict) -> ConfigTree:
    """从聚合字典构建 ConfigTree。"""
    tree = ConfigTree()
    for bundle in data.get("bundles", []) or []:
        tree.add_bundle_dict(bundle)
    for profile in data.get("profiles", []) or []:
        tree.add_profile_dict(profile)
    return tree


def load_tree_from_file(path: str) -> ConfigTree:
    return load_tree_from_dict(load_file(path))


def load_tree_from_dir(directory: str) -> ConfigTree:
    """扫描目录内的清单文件并构建 ConfigTree。"""
    if not os.path.isdir(directory):
        raise PluginError(f"清单目录不存在: {directory}")
    tree = ConfigTree()
    entries: List[str] = []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".yml", ".yaml", ".json")):
            continue
        entries.append(os.path.join(directory, name))
    for path in entries:
        data = load_file(path)
        if not isinstance(data, dict):
            continue
        if "id" in data:
            tree.add_bundle_dict(data)
            continue
        if "name" in data:
            tree.add_profile_dict(data)
            continue
        if "bundles" in data or "profiles" in data:
            for bundle in data.get("bundles", []) or []:
                tree.add_bundle_dict(bundle)
            for profile in data.get("profiles", []) or []:
                tree.add_profile_dict(profile)
            continue
        raise PluginError(f"无法识别清单文件 {path}（缺少 id/name/bundles/profiles）")
    return tree


__all__ = [
    "load_text",
    "load_file",
    "load_tree_from_dict",
    "load_tree_from_file",
    "load_tree_from_dir",
]
