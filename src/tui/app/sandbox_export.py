"""sandbox_export — 文件沙盒变更导出（``w``/``W`` 导出变更报告）。

把文件变更条目（``core.commands._sandbox_cmd.build_change_entries`` 产出）
序列化为 Markdown / JSON 文档并写入工作目录：

  - :func:`entries_to_markdown` / :func:`entries_to_json`：**纯序列化函数**
    （便于单测，不触碰文件系统）；
  - :func:`export_filename` / :func:`write_export`：取路径 + 写文件 + 返回
    展示用相对路径（时间经参数注入，便于测试固定输出）。

差异文本用标准库 ``difflib.unified_diff`` 生成（对超长差异按行数截断，
避免导出文件过大）。

依赖约束：仅标准库 + tui 核心，无 core / tools 依赖。
"""

from __future__ import annotations

import difflib
import json as _json
import os
import time as _time

__all__ = [
    "EXPORT_PREFIX",
    "entries_to_markdown",
    "entries_to_json",
    "export_filename",
    "write_export",
    "unified_diff_text",
]

#: 导出文件命名前缀（工作目录下生成 ``sandbox-changes-<时间戳>.<ext>``）。
EXPORT_PREFIX = "sandbox-changes"
_FORMAT_EXT = {"md": "md", "json": "json"}
#: 单文件差异导出最大行数（超出截断）。
_MAX_DIFF_LINES = 500


def _split_lines(text) -> list:
    if not isinstance(text, str) or not text:
        return []
    return text.splitlines()


def unified_diff_text(path: str, before, after, max_lines: int = _MAX_DIFF_LINES) -> str:
    """前后内容 → unified diff 文本（超长截断；无内容返回空串）。"""
    b = _split_lines(before)
    a = _split_lines(after)
    if not b and not a:
        return ""
    lines: list = []
    for line in difflib.unified_diff(
        b, a, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    ):
        lines.append(line)
        if len(lines) >= max_lines:
            lines.append(f"... （差异超过 {max_lines} 行，已截断）")
            break
    return "\n".join(lines)


def _entry_tools(entry: dict) -> list:
    tools = entry.get("tools")
    if isinstance(tools, (list, tuple)):
        return [str(t) for t in tools]
    return []


def _export_meta(entries: list, when: float | None = None) -> dict:
    files = len(entries or [])
    changes = 0
    for e in entries or []:
        try:
            changes += int(e.get("records", 0) or 0)
        except (TypeError, ValueError):
            pass
    ts = _time.localtime(_time.time() if when is None else when)
    return {
        "exported_at": _time.strftime("%Y-%m-%d %H:%M:%S", ts),
        "files": files,
        "changes": changes,
    }


def entries_to_markdown(entries: list, when: float | None = None) -> str:
    """变更条目 → Markdown 文档。"""
    meta = _export_meta(entries, when)
    out: list = ["# 文件沙盒变更导出（Sandbox Changes）", ""]
    out.append(f"- 导出时间：{meta['exported_at']}")
    out.append(f"- 文件：{meta['files']} 个 · 变更 {meta['changes']} 次")
    out.append("")
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", ""))
        label = str(entry.get("change_label", ""))
        out.append(f"## [{label}] {path}")
        out.append("")
        out.append(
            f"- 修改次数：{entry.get('records', 0)} · 消息索引："
            f"{entry.get('message_index', '')}"
        )
        tools = _entry_tools(entry)
        if tools:
            out.append(f"- 工具：{', '.join(tools)}")
        out.append("")
        diff = unified_diff_text(path, entry.get("before"), entry.get("after"))
        if diff:
            out.append("```diff")
            out.append(diff)
            out.append("```")
            out.append("")
    return "\n".join(out)


def _entry_to_dict(entry: dict) -> dict:
    return {
        "path": str(entry.get("path", "")),
        "change_label": str(entry.get("change_label", "")),
        "records": int(entry.get("records", 0) or 0),
        "message_index": str(entry.get("message_index", "")),
        "tools": _entry_tools(entry),
        "is_dir": bool(entry.get("is_dir", False)),
        "reverted": bool(entry.get("reverted", False)),
    }


def entries_to_json(entries: list, when: float | None = None) -> str:
    """变更条目 → JSON 文档（缩进 2、非 ASCII 原样）。"""
    payload = {
        "meta": _export_meta(entries, when),
        "files": [
            _entry_to_dict(e) for e in (entries or []) if isinstance(e, dict)
        ],
    }
    return _json.dumps(payload, ensure_ascii=False, indent=2)


def export_filename(fmt: str = "md", when: float | None = None) -> str:
    """导出文件名（``sandbox-changes-<YYYYmmdd-HHMMSS>.<ext>``）。"""
    ts = _time.localtime(_time.time() if when is None else when)
    stamp = _time.strftime("%Y%m%d-%H%M%S", ts)
    ext = _FORMAT_EXT.get(fmt, "md")
    return f"{EXPORT_PREFIX}-{stamp}.{ext}"


def write_export(entries: list, fmt: str = "md",
                 directory: str | None = None,
                 when: float | None = None) -> str:
    """把变更报告写入文件，返回相对工作目录的路径。

    Raises:
        OSError: 写文件失败（调用方捕获并提示）。
        ValueError: 未知格式。
    """
    if fmt not in _FORMAT_EXT:
        raise ValueError(f"未知导出格式: {fmt!r}")
    directory = directory or os.getcwd()
    filename = export_filename(fmt, when)
    path = os.path.join(directory, filename)
    text = (
        entries_to_json(entries, when) if fmt == "json"
        else entries_to_markdown(entries, when)
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    try:
        return os.path.relpath(path, os.getcwd())
    except ValueError:
        return path
