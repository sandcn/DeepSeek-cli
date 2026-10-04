"""内核配置与运行时快照的格式化输出（对应 ``--dump-config``）。"""

from __future__ import annotations

import json
from typing import Any, Iterable


def format_config_dump(dump: dict, *, indent: int = 2) -> str:
    """把 ConfigTree.dump() 的结果格式化为可读文本。"""
    lines: list[str] = []
    pad = " " * indent
    lines.append(f"profile: {dump.get('profile', '')}")
    if dump.get("description"):
        lines.append(f"{pad}description: {dump['description']}")
    bundles = dump.get("bundles", [])
    lines.append(f"{pad}bundles: [{', '.join(bundles)}]")

    patches = dump.get("patches", [])
    if patches:
        lines.append(f"{pad}patches:")
        for patch in patches:
            detail = []
            if patch.get("config") is not None:
                detail.append(f"config={json.dumps(patch['config'], ensure_ascii=False)}")
            if patch.get("disabled") is not None:
                detail.append(f"disabled={patch['disabled']}")
            lines.append(f"{pad}{pad}- {patch.get('target')}: {' '.join(detail)}")

    lines.append(f"{pad}plugins:")
    plugins = dump.get("plugins", [])
    if not plugins:
        lines.append(f"{pad}{pad}(empty)")
    for entry in plugins:
        marker = " [disabled]" if entry.get("disabled") else ""
        lines.append(
            f"{pad}{pad}- {entry.get('id')}{marker}  ({entry.get('plugin')})"
        )
        config = entry.get("config") or {}
        if config:
            serialized = json.dumps(config, ensure_ascii=False, sort_keys=True)
            lines.append(f"{pad}{pad}{pad}config: {serialized}")
    return "\n".join(lines)


def format_runtime_snapshot(kernel: Any, *, indent: int = 2) -> str:
    """格式化内核运行时快照：Fiber 状态 + 已提供服务的 key。"""
    lines: list[str] = []
    pad = " " * indent
    lines.append("fibers:")
    fibers = kernel.fibers()
    if not fibers:
        lines.append(f"{pad}(empty)")
    for fiber in fibers:
        error = f" error={fiber.error!r}" if fiber.error else ""
        lines.append(f"{pad}- {fiber.name}: {fiber.state.value}{error}")
    lines.append("services:")
    keys = sorted(kernel.service_keys())
    if not keys:
        lines.append(f"{pad}(empty)")
    for key in keys:
        value = kernel.resolve_service(key)
        lines.append(f"{pad}- {key}: {type(value).__name__}")
    return "\n".join(lines)


def summarize(entries: Iterable[Any]) -> dict:
    """统计解析后的插件条目（总数/启用数/禁用数）。"""
    entries = list(entries)
    disabled = sum(1 for entry in entries if getattr(entry, "disabled", False))
    return {
        "total": len(entries),
        "enabled": len(entries) - disabled,
        "disabled": disabled,
    }


__all__ = ["format_config_dump", "format_runtime_snapshot", "summarize"]
