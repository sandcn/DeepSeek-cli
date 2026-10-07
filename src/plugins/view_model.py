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


def _make_entry(name: str, kind: str, subtitle: str, fields: list,
                depends: list | None = None,
                provides: list | None = None) -> dict:
    fields = list(fields)
    return {
        "name": _text(name, 200),
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "subtitle": _text(subtitle, 120),
        "fields": fields,
        "field_levels": _field_levels(fields),
        "alerts": _entry_alerts(fields),
        # ★ 2026-10-07 第三批（依赖关系视图 / 服务交叉引用）：结构化依赖
        #   数据（fields 里的文本之外，供关系面板与跳转消费）。
        "depends": list(depends or []),
        "provides": list(provides or []),
        # dependents 由 build_plugin_entries 全局计算（谁依赖本插件/服务）
        "dependents": [],
    }


#: 状态值 → 警示级别（PENDING 等中间态 warn；FAILED/ERROR 等失败态 error）。
_STATE_LEVELS: dict[str, str] = {
    "PENDING": "warn",
    "STARTING": "warn",
    "LOADING": "warn",
    "FAILED": "error",
    "ERROR": "error",
    "DISPOSED": "error",
}

#: 字段「无内容」占位（视为正常，不警示）。
_EMPTY_VALUES = ("", "(无)", "None", "null")


def _field_levels(fields: list) -> dict:
    """字段标签 → 警示级别（``"warn"``/``"error"``；正常字段不入表）。

    规则（2026-10-07 插件详情渲染增强）：
      - ``缺失依赖`` 非空 → warn（依赖未满足）；
      - ``错误`` 非空 → error；
      - ``状态`` 命中失败/中间态表 → 对应级别。
    """
    levels: dict = {}
    for item in fields or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        label, value = item[0], item[1]
        text = str(value).strip() if value is not None else ""
        if label == "缺失依赖" and text not in _EMPTY_VALUES:
            levels[label] = "warn"
        elif label == "错误" and text not in _EMPTY_VALUES:
            levels[label] = "error"
        elif label == "状态":
            lvl = _STATE_LEVELS.get(text)
            if lvl:
                levels[label] = lvl
    return levels


def _entry_alerts(fields: list) -> list:
    """条目警示标签列表（左栏行尾/统计条提示；正常条目为空）。"""
    levels = _field_levels(fields)
    alerts: list = []
    if levels.get("错误"):
        alerts.append("错误")
    if levels.get("缺失依赖"):
        alerts.append("缺依赖")
    return alerts


def collect_plugin_stats(entries: list | None = None) -> dict:
    """插件条目统计（头部统计条数据源，纯函数）。

    Returns:
        dict：
          - ``total``：插件总数；
          - ``by_state``：{状态: 数量}（按数量降序展示）；
          - ``kinds``：{分类: 数量}；
          - ``errors``：含错误条目的数量；
          - ``missing``：缺失依赖的条目数量；
          - ``warnings``：含状态警示（PENDING 等）的条目数量。
    """
    stats = {
        "total": 0, "by_state": {}, "kinds": {}, "errors": 0,
        "missing": 0, "warnings": 0,
    }
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        stats["total"] += 1
        state = str(entry.get("subtitle", "") or "").strip()
        if state:
            stats["by_state"][state] = stats["by_state"].get(state, 0) + 1
        kind = str(entry.get("kind", "kernel") or "kernel")
        stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1
        levels = entry.get("field_levels") or _field_levels(entry.get("fields") or [])
        if levels.get("错误"):
            stats["errors"] += 1
        if levels.get("缺失依赖"):
            stats["missing"] += 1
        if levels.get("状态") == "warn":
            stats["warnings"] += 1
    return stats


def format_plugin_stats(stats: dict) -> str:
    """统计条单行文本（``·`` 分隔；调用方按栏宽截断）。"""
    total = int(stats.get("total") or 0)
    parts = [f"共 {total} 个"]
    by_state = stats.get("by_state") or {}
    for state, n in sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0])):
        if state:
            parts.append(f"{state} {n}")
    if stats.get("errors"):
        parts.append(f"错误 {stats['errors']}")
    if stats.get("missing"):
        parts.append(f"缺依赖 {stats['missing']}")
    return " \u00b7 ".join(parts)


def plugin_search_text(entry: dict) -> str:
    """插件条目搜索文本（名称 / 分类 / 状态 / 全部字段值）。"""
    if not isinstance(entry, dict):
        return ""
    parts = [
        str(entry.get("name", "") or ""),
        str(entry.get("kind_label", "") or ""),
        str(entry.get("subtitle", "") or ""),
    ]
    for item in entry.get("fields") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            parts.append(f"{item[0]}: {item[1]}")
    return "\n".join(parts)


def format_plugin_entry_text(entry: dict) -> str:
    """插件条目 → 纯文本（``y`` 复制到剪贴板的内容）。"""
    if not isinstance(entry, dict):
        return ""
    lines = [
        str(entry.get("name", "") or ""),
        f"\u5206\u7c7b: {entry.get('kind_label', '') or ''}",
        f"\u72b6\u6001: {entry.get('subtitle', '') or ''}",
    ]
    for item in entry.get("fields") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            lines.append(f"{item[0]}: {item[1]}")
    return "\n".join(lines)


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
            out.append(_make_entry(
                str(getattr(fiber, "name", "?")), "kernel", state, fields,
                depends=inject, provides=provide,
            ))
        except Exception:
            _logger.debug("构建内核插件条目失败", exc_info=True)
    return out


# ── 汇总构建 ─────────────────────────────────────────────


def build_plugin_entries(kernel=None) -> list[dict]:
    """构建已加载插件条目列表（内核运行时 Fiber，排除 DISPOSED）。

    ★ 2026-10-07 第三批（依赖关系视图 / 服务交叉引用）：构建后全局填充
    ``dependents``（谁依赖本插件——按 ``depends`` 名字匹配本插件 ``name``
    或 ``provides`` 服务名），供关系面板与「被依赖」交叉引用。

    Args:
        kernel: 插件内核（None 时读进程级当前内核）。

    Returns:
        条目 dict 列表（见模块 docstring 结构）；内核不可用或读取异常时返回
        空列表（界面显示「无已加载插件」）。
    """
    from ..kernel import get_current_kernel

    kernel = kernel if kernel is not None else get_current_kernel()
    entries = _kernel_entries(kernel)
    _compute_dependents(entries)
    return entries


def _compute_dependents(entries: list) -> None:
    """填充条目 ``dependents``（就地修改；谁依赖本插件/服务）。

    匹配规则：条目 A 的某个 ``depends`` 名等于条目 B 的 ``name`` 或出现在
    B 的 ``provides`` 中 → B.dependents 追加 A.name（去重、保持顺序）。
    """
    if not entries:
        return
    # 依赖名 → 提供方条目（name 与 provides 都作为键）
    providers: dict = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        providers.setdefault(str(entry.get("name", "")), entry)
        for svc in entry.get("provides") or []:
            providers.setdefault(str(svc), entry)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for dep in entry.get("depends") or []:
            target = providers.get(str(dep))
            if target is None or target is entry:
                continue
            deps = target.setdefault("dependents", [])
            name = str(entry.get("name", ""))
            if name and name not in deps:
                deps.append(name)


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
    "collect_plugin_stats",
    "format_plugin_stats",
    "plugin_search_text",
    "format_plugin_entry_text",
]
