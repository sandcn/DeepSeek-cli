"""插件清单导出（plugin_export）——``w``/``W`` 导出为 Markdown / JSON 文件。

2026-10-07 第三批（用户需求：插件清单导出）新增：
  - ``entries_to_markdown`` / ``entries_to_json``：插件条目列表序列化；
  - ``export_filename`` / ``write_export``：写入文件（时间戳命名）。

设计：**序列化纯函数与写盘分离**——纯函数便于单测（不触碰文件系统），
写盘函数只负责取路径 + 写文件 + 返回展示用路径。时间经参数注入（默认
``_time.time``）便于测试固定输出。结构对齐 trace_export（同族实现）。
"""

from __future__ import annotations

import json as _json
import os
import time as _time

from ...plugins.view_model import collect_plugin_stats, format_plugin_stats

__all__ = [
    "entries_to_markdown", "entries_to_json", "export_filename", "write_export",
]

#: 导出文件命名前缀。
EXPORT_PREFIX = "plugin-export"
#: 格式 → 扩展名。
_FORMAT_EXT = {"md": "md", "json": "json"}


def _entry_dict(entry: dict) -> dict:
    """插件条目 → JSON 可序列化 dict。"""
    fields = []
    for item in entry.get("fields") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            fields.append({"label": str(item[0]), "value": str(item[1])})
    return {
        "name": str(entry.get("name", "")),
        "kind": str(entry.get("kind", "")),
        "kind_label": str(entry.get("kind_label", "")),
        "state": str(entry.get("subtitle", "")),
        "alerts": list(entry.get("alerts") or []),
        "depends": list(entry.get("depends") or []),
        "provides": list(entry.get("provides") or []),
        "dependents": list(entry.get("dependents") or []),
        "fields": fields,
    }


def _meta(entries) -> dict:
    """导出元信息（统计汇总 + 导出时间）。"""
    stats = collect_plugin_stats(list(entries or []))
    return {
        "exported_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": stats.get("total", 0),
        "summary": format_plugin_stats(stats),
        "by_state": stats.get("by_state", {}),
        "errors": stats.get("errors", 0),
        "missing": stats.get("missing", 0),
    }


def entries_to_markdown(entries) -> str:
    """插件清单 → Markdown 文档（``w`` 导出内容）。"""
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    meta = _meta(entries)
    out: list = ["# 插件清单（Plugin Export）", ""]
    out.append(f"- 导出时间：{meta['exported_at']}")
    out.append(f"- 统计：{meta['summary']}")
    out.append("")
    for entry in entries:
        name = str(entry.get("name", ""))
        state = str(entry.get("subtitle", ""))
        out.append(f"## {name}" + (f" [{state}]" if state else ""))
        out.append("")
        alerts = entry.get("alerts") or []
        if alerts:
            out.append(f"**警示**：{' '.join(str(a) for a in alerts)}")
            out.append("")
        depends = entry.get("depends") or []
        if depends:
            out.append(f"- 依赖：{', '.join(str(x) for x in depends)}")
        provides = entry.get("provides") or []
        if provides:
            out.append(f"- 提供服务：{', '.join(str(x) for x in provides)}")
        dependents = entry.get("dependents") or []
        if dependents:
            out.append(f"- 被依赖：{', '.join(str(x) for x in dependents)}")
        for item in entry.get("fields") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append(f"- {item[0]}：{item[1]}")
        out.append("")
    return "\n".join(out)


def entries_to_json(entries) -> str:
    """插件清单 → JSON 文档（``W`` 导出内容；缩进 2、非 ASCII 原样）。"""
    payload = {
        "meta": _meta(entries),
        "entries": [_entry_dict(e) for e in (entries or []) if isinstance(e, dict)],
    }
    return _json.dumps(payload, ensure_ascii=False, indent=2)


def export_filename(fmt: str = "md", when: float | None = None) -> str:
    """导出文件名（``plugin-export-<YYYYmmdd-HHMMSS>.<ext>``）。"""
    ts = _time.localtime(_time.time() if when is None else when)
    stamp = _time.strftime("%Y%m%d-%H%M%S", ts)
    ext = _FORMAT_EXT.get(fmt, "md")
    return f"{EXPORT_PREFIX}-{stamp}.{ext}"


def write_export(entries, fmt: str = "md", directory: str | None = None,
                 when: float | None = None) -> str:
    """把插件清单写入文件（``w``/``W`` 动作）。

    Args:
        entries: 插件条目列表。
        fmt: ``"md"`` / ``"json"``。
        directory: 输出目录（默认当前工作目录）。
        when: 时间基准（测试注入）。

    Returns:
        写入的文件路径（相对工作目录形式，便于状态行展示）。

    Raises:
        OSError: 写文件失败（调用方捕获并提示）。
        ValueError: 未知格式。
    """
    if fmt not in _FORMAT_EXT:
        raise ValueError(f"未知导出格式: {fmt!r}")
    directory = directory or os.getcwd()
    filename = export_filename(fmt, when)
    path = os.path.join(directory, filename)
    text = entries_to_json(entries) if fmt == "json" else entries_to_markdown(entries)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    try:
        rel = os.path.relpath(path, os.getcwd())
    except ValueError:
        rel = path
    return rel
