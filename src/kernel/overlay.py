"""Overlay — Profile 之上的用户补丁叠加层（dsh 的 ``--patch`` / cordis.patch.yml）。

在 ``ConfigTree.resolve(profile)`` 产出的插件清单之上，Overlay 可以：

- **insert**  追加插件条目（``{"insert": [{"id","plugin","config"}]}``）或
  在列表形态里出现未匹配 id 的条目时自动追加；
- **replace** 按 id 整体替换某条目的 config（``{"id": X, "config": {...}}``）；
- **disable** 按 id 禁用某条目（``{"id": X, "disabled": true}``）或
  ``{"disable": ["X"]}``。

两种输入形态都支持：

1. 列表（dsh overlay 风格）::

       - insert:
           - {id: X, name: my.pkg.plugin}
       - {id: core::tools, config: {...}}
       - {id: core::ui, disabled: true}

2. 字典::

       insert: [...]
       replace: [{id: ..., config: ...}]
       disable: [...]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .config_tree import ResolvedPlugin
from .errors import PluginError


def normalize_overlay(data: Any) -> dict:
    """把任意形态的 overlay 数据归一化为 ``{insert, replace, disable}``。"""
    insert: List[dict] = []
    replace: Dict[str, dict] = {}
    disable: set = set()

    if data is None:
        return {"insert": insert, "replace": replace, "disable": disable}

    if isinstance(data, dict):
        for item in data.get("insert", []) or []:
            insert.append(_entry_dict(item))
        for key in ("replace", "patch"):
            value = data.get(key) or []
            if isinstance(value, dict):
                # 已归一化形态（id → config 映射）
                for entry_id, config in value.items():
                    if config is not None:
                        replace[entry_id] = dict(config)
                continue
            for item in value:
                _record_replace(item, replace, disable)
        for item in data.get("disable", []) or []:
            disable.add(_target_of(item))
        # 字典本身也可能是单条 patch
        if "id" in data and ("config" in data or "disabled" in data):
            _record_replace(data, replace, disable)
        return {"insert": insert, "replace": replace, "disable": disable}

    if isinstance(data, (list, tuple)):
        for item in data:
            if not isinstance(item, dict):
                raise PluginError(f"overlay 条目必须是字典: {item!r}")
            if "insert" in item:
                for inserted in item.get("insert", []) or []:
                    insert.append(_entry_dict(inserted))
                continue
            if "id" in item and ("config" in item or "disabled" in item):
                _record_replace(item, replace, disable)
                continue
            if "name" in item or "plugin" in item:
                insert.append(_entry_dict(item))
                continue
            raise PluginError(f"无法识别的 overlay 条目: {item!r}")
        return {"insert": insert, "replace": replace, "disable": disable}

    raise PluginError(f"overlay 必须是列表或字典: {type(data).__name__}")


def apply_overlay(
    entries: Sequence[ResolvedPlugin], overlay: dict
) -> List[ResolvedPlugin]:
    """在解析后的插件清单上应用 overlay，返回新清单。"""
    overlay = normalize_overlay(overlay)
    result: List[ResolvedPlugin] = [
        ResolvedPlugin(
            id=entry.id,
            plugin=entry.plugin,
            config=dict(entry.config),
            disabled=bool(entry.disabled),
            bundle=entry.bundle,
        )
        for entry in entries
    ]
    index = {entry.id: entry for entry in result}

    for entry_id, config in overlay["replace"].items():
        target = index.get(entry_id)
        if target is None:
            raise PluginError(f"overlay replace 目标不存在: {entry_id!r}")
        target.config = dict(config)

    for entry_id in overlay["disable"]:
        target = index.get(entry_id)
        if target is None:
            raise PluginError(f"overlay disable 目标不存在: {entry_id!r}")
        target.disabled = True

    for item in overlay["insert"]:
        entry_id = item["id"]
        if entry_id in index:
            # 已存在时视为 replace（整体替换 config / disabled）
            target = index[entry_id]
            if item.get("config"):
                target.config = dict(item["config"])
            continue
        entry = ResolvedPlugin(
            id=entry_id,
            plugin=item.get("plugin") or item.get("name"),
            config=dict(item.get("config", {}) or {}),
            disabled=bool(item.get("disabled", False)),
            bundle=item.get("bundle") or entry_id.split("::", 1)[0],
        )
        if entry.plugin is None:
            raise PluginError(f"overlay insert 缺少 plugin/name: {item!r}")
        result.append(entry)
        index[entry_id] = entry

    return result


def _entry_dict(item: Any) -> dict:
    if not isinstance(item, dict):
        raise PluginError(f"插件条目必须是字典: {item!r}")
    if "id" not in item:
        raise PluginError(f"插件条目缺少 id: {item!r}")
    plugin = item.get("plugin") or item.get("name")
    if plugin is None:
        raise PluginError(f"插件条目缺少 plugin/name: {item!r}")
    return {
        "id": item["id"],
        "plugin": plugin,
        "config": dict(item.get("config", {}) or {}),
        "disabled": bool(item.get("disabled", False)),
        "bundle": item.get("bundle", ""),
    }


def _record_replace(item: Any, replace: Dict[str, dict], disable: set) -> None:
    if not isinstance(item, dict) or "id" not in item:
        raise PluginError(f"overlay patch 条目缺少 id: {item!r}")
    entry_id = item["id"]
    if "config" in item and item["config"] is not None:
        replace[entry_id] = item["config"]
    if item.get("disabled"):
        disable.add(entry_id)


def _target_of(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and "id" in item:
        return item["id"]
    raise PluginError(f"overlay disable 目标非法: {item!r}")


__all__ = ["normalize_overlay", "apply_overlay"]
