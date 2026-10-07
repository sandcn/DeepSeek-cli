"""配置导出 / 导入（config_export）——``e`` 导出、``i`` 从 JSON 导入。

2026-10-07 第三批（用户需求：配置导出/导入）新增：
  - ``entries_to_map`` / ``entries_to_json``：配置项列表 → 可导入 JSON；
  - ``write_export`` / ``export_filename``：写盘（时间戳命名）；
  - ``parse_import``：导入文本 → 配置映射（带校验）；
  - ``apply_import``：映射逐项写回（``update_config``，未知键/异常计入错误）。

设计：**序列化/解析纯函数与写盘/写回分离**——纯函数便于单测。敏感项
（``sensitive=True``，如 api_key）导出时**排除**（避免密钥落盘）。
"""

from __future__ import annotations

import json as _json
import os
import time as _time

__all__ = [
    "entries_to_map", "entries_to_json", "export_filename", "write_export",
    "parse_import", "apply_import",
]

#: 导出文件命名前缀。
EXPORT_PREFIX = "config-export"


def entries_to_map(entries) -> dict:
    """配置项列表 → ``{key: value}`` 映射（排除敏感项）。"""
    out: dict = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("sensitive"):
            continue
        key = str(entry.get("key", "") or "")
        if key:
            out[key] = entry.get("value")
    return out


def entries_to_json(entries, when: float | None = None) -> str:
    """配置项列表 → JSON 文档（含 meta + config 映射；缩进 2）。"""
    data = entries_to_map(entries)
    payload = {
        "meta": {
            "exported_at": _time.strftime(
                "%Y-%m-%d %H:%M:%S",
                _time.localtime(_time.time() if when is None else when),
            ),
            "count": len(data),
        },
        "config": data,
    }
    return _json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def export_filename(when: float | None = None) -> str:
    """导出文件名（``config-export-<YYYYmmdd-HHMMSS>.json``）。"""
    ts = _time.localtime(_time.time() if when is None else when)
    stamp = _time.strftime("%Y%m%d-%H%M%S", ts)
    return f"{EXPORT_PREFIX}-{stamp}.json"


def write_export(entries, directory: str | None = None,
                 when: float | None = None) -> str:
    """把配置导出为 JSON 文件；返回写入路径（相对工作目录）。"""
    directory = directory or os.getcwd()
    path = os.path.join(directory, export_filename(when))
    text = entries_to_json(entries, when)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    try:
        return os.path.relpath(path, os.getcwd())
    except ValueError:
        return path


def parse_import(text: str) -> tuple[dict, str]:
    """导入文本 → ``(配置映射, 错误消息)``。

    支持两种格式：``{"meta": ..., "config": {...}}``（本模块导出格式）或
    直接的 ``{key: value, ...}``。
    """
    try:
        obj = _json.loads(text)
    except Exception as exc:
        return {}, f"JSON 解析失败: {exc}"
    if not isinstance(obj, dict):
        return {}, "顶层应为 JSON 对象"
    data = obj.get("config", obj)
    if not isinstance(data, dict):
        return {}, "config 字段应为 JSON 对象"
    return data, ""


def apply_import(mapping: dict) -> tuple[int, list]:
    """配置映射逐项写回（``update_config``）。

    Returns:
        ``(applied, errors)``：成功写回条数 + 错误消息列表（未知键/写入异常）。
    """
    from ...config.loader import update_config
    from ...config.view_model import resolve_config_key

    applied = 0
    errors: list = []
    for key, value in (mapping or {}).items():
        resolved = resolve_config_key(str(key))
        if resolved is None:
            errors.append(f"未知键 {key}")
            continue
        try:
            update_config(resolved, value)
            applied += 1
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    return applied, errors
