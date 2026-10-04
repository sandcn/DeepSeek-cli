"""插件视图模型 — 插件总览列表构建（/plugin 界面与文本回退共用）。

纯逻辑模块（零 UI 依赖）：汇总四类「插件」数据源，统一为条目 dict，
供 TUI 全屏界面（``src/tui/app/plugin_view.py``）与文本回退
（``src/core/commands/_plugin_cmd.py``）共用：

  1. **内核运行时插件**（Fiber）——名称/状态/类型/来源/依赖/缺失依赖/
     提供服务/子插件/配置/错误；
  2. **内置清单插件条目**（Profile/Bundle）——id/bundle/plugin 引用/
     config/disabled；
  3. **命令插件**（CommandPlugin）——命令/分组/描述/别名/用法/实现类；
  4. **外部与已安装插件**——外部目录文件 + 已安装 registry + entry-points。

条目结构::

    {
        "name": str,          # 左栏显示名
        "kind": str,          # "kernel"|"manifest"|"command"|"external"
        "kind_label": str,    # 分类中文标签
        "subtitle": str,      # 左栏行尾短标签（状态/分组/引用等）
        "fields": [(标签, 值), ...],  # 右栏详情行
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

_logger = logging.getLogger(__name__)

#: 分类中文标签
KIND_LABELS: dict[str, str] = {
    "kernel": "内核运行时插件",
    "manifest": "内置清单插件",
    "command": "命令插件",
    "external": "外部/已安装插件",
}

#: 分类展示顺序
KIND_ORDER: tuple[str, ...] = ("kernel", "manifest", "command", "external")

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


# ── 1. 内核运行时插件（Fiber） ────────────────────────────


def _kernel_entries(kernel) -> list[dict]:
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
            state = getattr(getattr(fiber, "state", None), "value", "")
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


# ── 2. 内置清单插件条目 ──────────────────────────────────


def _manifest_entries(plugin_dicts) -> list[dict]:
    out: list[dict] = []
    for item in plugin_dicts or []:
        try:
            plugin_ref = item.get("plugin")
            fields = [
                ("分类", KIND_LABELS["manifest"] + "（Profile/Bundle）"),
                ("插件 id", _text(item.get("id"))),
                ("所属 bundle", _text(item.get("bundle"))),
                ("插件引用", _text(plugin_ref)),
                ("是否禁用", "是" if item.get("disabled") else "否"),
                ("配置", _json_text(item.get("config") or {})),
            ]
            out.append(_make_entry(_text(item.get("id")), "manifest", _text(plugin_ref), fields))
        except Exception:
            _logger.debug("构建清单插件条目失败", exc_info=True)
    return out


# ── 3. 命令插件（CommandPlugin） ─────────────────────────


def _command_entries() -> list[dict]:
    try:
        # 命令服务卸载会注销内置命令（可逆副作用）——自省前按需补注册，
        # 保证命令插件信息在无活跃内核/内核反复构建后仍可见。
        from .commands import ensure_builtin_commands

        ensure_builtin_commands()
    except Exception:
        _logger.debug("补注册内置命令插件失败", exc_info=True)
    try:
        from ..core.commands.base import get_plugin_registry

        plugins = list(get_plugin_registry().list())
    except Exception:
        _logger.debug("读取命令插件注册表失败", exc_info=True)
        return []

    out: list[dict] = []
    for plugin in plugins:
        try:
            meta = getattr(plugin, "meta", None)
            if meta is None:
                continue
            aliases = list(getattr(meta, "aliases", ()) or ())
            group = getattr(meta, "group", "") or "general"
            fields = [
                ("分类", KIND_LABELS["command"] + "（CommandPlugin）"),
                ("命令", "/" + str(meta.name)),
                ("分组", str(group)),
                ("描述", _text(getattr(meta, "description", "")) or "(无)"),
                ("别名", ", ".join("/" + str(a) for a in aliases) or "(无)"),
                ("用法", _text(getattr(meta, "usage", "")) or "(无)"),
                ("实现类", type(plugin).__name__),
            ]
            out.append(_make_entry("/" + str(meta.name), "command", str(group), fields))
        except Exception:
            _logger.debug("构建命令插件条目失败", exc_info=True)
    return out


# ── 4. 外部与已安装插件 ──────────────────────────────────


def _external_entries(summary: dict, install_dir: str) -> list[dict]:
    out: list[dict] = []

    for item in summary.get("external_dirs") or []:
        directory = item.get("dir", "")
        for fname in item.get("files") or []:
            is_dir = str(fname).endswith("/") or str(fname).endswith("\\")
            clean = str(fname).rstrip("/\\")
            fields = [
                ("分类", KIND_LABELS["external"] + "（外部目录）"),
                ("类型", "目录" if is_dir else "文件"),
                ("所在目录", directory),
                ("名称", clean),
                ("说明", "启动时自动发现并热挂载"),
            ]
            out.append(_make_entry(clean, "external", "外部目录", fields))

    for name, meta in sorted((summary.get("installed") or {}).items()):
        source = meta.get("source", "") if isinstance(meta, dict) else str(meta)
        fields = [
            ("分类", KIND_LABELS["external"] + "（已安装）"),
            ("名称", _text(name)),
            ("来源", _text(source) or "(未知)"),
            ("安装目录", install_dir or "(未知)"),
            ("说明", "下次启动自动发现并挂载"),
        ]
        out.append(_make_entry(_text(name), "external", "已安装", fields))

    for item in summary.get("entry_points") or []:
        fields = [
            ("分类", KIND_LABELS["external"] + "（entry-point）"),
            ("名称", _text(item.get("name"))),
            ("入口", _text(item.get("plugin"))),
            ("说明", "Python 包 dsh.plugins entry-point"),
        ]
        out.append(_make_entry(_text(item.get("name")), "external", "entry-point", fields))

    return out


# ── 汇总构建 ─────────────────────────────────────────────


def build_plugin_entries(kernel=None, profile: str = "") -> list[dict]:
    """构建插件总览条目列表（四类来源，按 ``KIND_ORDER`` 分组）。

    Args:
        kernel: 插件内核（None 时读进程级当前内核）。
        profile: Profile 名（空时取 ``kernel.profile``，再回退内置默认）。

    Returns:
        条目 dict 列表（见模块 docstring 结构）；任一数据源异常均降级为空，
        不影响其他来源。
    """
    from ..kernel import get_current_kernel
    from .manifest import DEFAULT_PROFILE

    kernel = kernel if kernel is not None else get_current_kernel()
    prof = profile or getattr(kernel, "profile", "") or DEFAULT_PROFILE

    entries: list[dict] = []
    entries.extend(_kernel_entries(kernel))

    summary: dict = {}
    try:
        from .manager import list_plugins

        summary = list_plugins(prof)
    except Exception:
        _logger.debug("list_plugins 失败（profile=%s）", prof, exc_info=True)
        try:
            from .bootstrap import resolve_entries

            summary = {"plugins": [e.to_dict() for e in resolve_entries(prof)]}
        except Exception:
            _logger.debug("resolve_entries 失败（profile=%s）", prof, exc_info=True)
            summary = {}

    entries.extend(_manifest_entries(summary.get("plugins")))
    entries.extend(_command_entries())

    install_dir = ""
    try:
        from .manager import plugins_dir

        install_dir = plugins_dir()
    except Exception:
        _logger.debug("读取插件安装目录失败", exc_info=True)
    entries.extend(_external_entries(summary, install_dir))
    return entries


def format_plugin_text(entries: list[dict] | None = None) -> str:
    """插件条目 → 多行文本（无 ChatUI 时的回退显示）。"""
    entries = entries if entries is not None else build_plugin_entries()
    lines: list[str] = ["插件总览"]
    if not entries:
        lines.append("  (无插件数据)")
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
