"""插件视图模型 — 已加载插件（内核运行时 Fiber）列表构建。

纯逻辑模块（零 UI 依赖）：只汇总**内核当前已加载的插件**——已经挂载进
内核、且尚未卸载（非 DISPOSED）的 Fiber，统一为条目 dict，供 TUI 全屏
界面（``src/tui/app/plugin_view.py``）与文本回退
（``src/core/commands/_plugin_cmd.py``）共用：

  1. **内核运行时插件**（Fiber）——名称/状态/类型/来源/依赖/缺失依赖/
     提供服务/子插件/配置/错误。

条目结构::

    {
        "name": str,          # 左栏显示名
        "kind": str,          # "kernel"
        "kind_label": str,    # 分类中文标签
        "subtitle": str,      # 左栏行尾短标签（状态）
        "fields": [(标签, 值), ...],  # 右栏详情行
    }

``/plugin`` 只回答「此刻内核里加载了哪些插件」——数据源单一取内核 Fiber，
不再罗列清单全量条目 / 命令插件 / 外部潜在项等「声明型」数据。
"""

from __future__ import annotations

import json
import logging
from typing import Any

_logger = logging.getLogger(__name__)

#: 忽略的 Fiber 状态（已完全卸载的历史实例，不属于「当前已加载」）
_DISPOSED_STATE = "DISPOSED"

#: 分类中文标签
KIND_LABELS: dict[str, str] = {
    "kernel": "内核运行时插件",
}

#: 分类展示顺序
KIND_ORDER: tuple[str, ...] = ("kernel",)

#: 单字段值截断长度（防极端超长单行；TUI 侧另按栏宽换行）
_VALUE_MAX = 600


def _text(value: Any, max_len: int = _VALUE_MAX) -> str:
    """任意值 → 单行显示文本（换行归一为空格；超长截断加省略号）。"""
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    if max_len > 0 and len(text) > max_len:
        return text[: max_len - 1] + "\u2026"
    return text


def _json_text(value: Any) -> str:
    """复合值 → 紧凑 JSON 文本（不可序列化时回退 str）。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return _text(value)


def _make_entry(name: str, kind: str, subtitle: str, fields: list) -> dict:
    return {
        "name": _text(name, 200),
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "subtitle": _text(subtitle, 120),
        "fields": list(fields),
    }


# ── 内核运行时插件（Fiber） ──────────────────────────────


def _kernel_entries(kernel) -> list[dict]:
    """内核当前已加载插件（非 DISPOSED 的 Fiber）→ 条目列表。"""
    if kernel is None:
        return []
    try:
        fibers = list(kernel.fibers())
    except Exception:
        _logger.debug("读取内核 Fiber 失败", exc_info=True)
        return []

    out: list[dict] = []
    for fiber in fibers:
        try:
            state = getattr(getattr(fiber, "state", None), "value", "")
            if state == _DISPOSED_STATE:
                continue
            definition = getattr(fiber, "definition", None)
            is_service = getattr(definition, "service_cls", None) is not None
            source = getattr(definition, "source", "") or ""
            inject = list(getattr(fiber, "inject", ()) or ())
            try:
                missing = list(fiber.missing_dependencies())
            except Exception:
                missing = []
            provide = list(getattr(definition, "provide", ()) or ())
            children = list(getattr(fiber, "_children", ()) or ())
            error = getattr(fiber, "error", None)
            config = getattr(fiber, "config", {}) or {}
            fields = [
                ("分类", KIND_LABELS["kernel"] + "（Fiber）"),
                ("状态", state),
                ("类型", "service" if is_service else "function"),
                ("来源", source or "(未记录)"),
                ("依赖 (inject)", ", ".join(inject) or "(无)"),
                ("缺失依赖", ", ".join(missing) or "(无)"),
                ("提供服务", ", ".join(provide) or "(无)"),
                ("子插件", ", ".join(str(getattr(c, "name", "?")) for c in children) or "(无)"),
                ("配置", _json_text(config)),
                ("错误", repr(error) if error else "(无)"),
            ]
            out.append(_make_entry(str(getattr(fiber, "name", "?")), "kernel", state, fields))
        except Exception:
            _logger.debug("构建内核插件条目失败", exc_info=True)
    return out


# ── 汇总构建 ─────────────────────────────────────────────


def build_plugin_entries(kernel=None) -> list[dict]:
    """构建已加载插件条目列表（内核运行时 Fiber，排除 DISPOSED）。

    Args:
        kernel: 插件内核（None 时读进程级当前内核）。

    Returns:
        条目 dict 列表（见模块 docstring 结构）；内核不可用或读取异常时返回
        空列表（界面显示「无已加载插件」）。
    """
    from ..kernel import get_current_kernel

    kernel = kernel if kernel is not None else get_current_kernel()
    return _kernel_entries(kernel)


def format_plugin_text(entries: list[dict] | None = None) -> str:
    """已加载插件条目 → 多行文本（无 ChatUI 时的回退显示）。"""
    entries = entries if entries is not None else build_plugin_entries()
    lines: list[str] = ["已加载插件"]
    if not entries:
        lines.append("  (无已加载插件)")
        return "\n".join(lines)

    by_kind: dict[str, list[dict]] = {}
    for entry in entries:
        by_kind.setdefault(entry.get("kind", "kernel"), []).append(entry)

    seen_kinds: list[str] = []
    for kind in list(KIND_ORDER) + [k for k in by_kind if k not in KIND_ORDER]:
        if kind in seen_kinds or kind not in by_kind:
            continue
        seen_kinds.append(kind)
        items = by_kind[kind]
        lines.append(f"  ─ {KIND_LABELS.get(kind, kind)}（{len(items)}）")
        for entry in items:
            subtitle = entry.get("subtitle") or ""
            suffix = f"  {subtitle}" if subtitle else ""
            lines.append(f"    - {entry.get('name', '')}{suffix}")
    return "\n".join(lines)


__all__ = [
    "KIND_LABELS",
    "KIND_ORDER",
    "build_plugin_entries",
    "format_plugin_text",
]
