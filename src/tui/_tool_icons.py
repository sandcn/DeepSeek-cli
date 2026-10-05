"""工具图标 & Agent 类型标签 — 兼容门面（真源下沉到 ``_tool_styles`` 注册表）。

「一切皆插件」：工具/类别/Agent 类型表现映射的真源已下沉到
``src.tui._tool_styles`` 注册表——每个工具 / 类别 / Agent 类型一个清单插件
条目（``tool_style``），可被 Patch/Overlay 覆盖、禁用或替换。

本模块保留旧调用面：实时查询函数（``tool_category`` / ``tool_style`` /
``agent_type_abbrev`` / ``agent_type_style`` / ``category_style``）与**内置快照
字典**（外部测试/调用面兼容；快照为内置默认值，不随 overlay 变化）。生产
渲染统一走 ``_tool_styles`` 的实时函数。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._tool_styles import (
    agent_type_abbrev,
    agent_type_style,
    category_style,
    default_presentation,
    builtin_presentation_ids,
    tool_category,
    tool_icon,
    tool_style,
)


def _builtin_of_kind(kind: str) -> dict:
    result = {}
    for spec_id in builtin_presentation_ids():
        spec = default_presentation(spec_id)
        if spec.kind == kind:
            result[spec.name] = spec
    return result


# ── 内置快照字典（兼容 re-export；不随 overlay 变化） ──────────

#: 工具名 → 图标（内置默认）
TOOL_ICONS: dict[str, str] = {
    name: spec.icon for name, spec in _builtin_of_kind("tool").items()
}

#: 工具名 → 类别（内置默认）
TOOL_CATEGORY_MAP: dict[str, str] = {
    name: spec.category for name, spec in _builtin_of_kind("tool").items()
}

#: 类别 → Style（内置默认）
TOOL_CATEGORY_STYLES: dict[str, Style] = {
    name: Style(fg=spec.fg) for name, spec in _builtin_of_kind("category").items()
}

#: 类别 → 256 色 ANSI（兼容 re-export，同一色号）
TOOL_CATEGORY_COLORS: dict[str, str] = {
    k: f"\033[38;5;{s.fg}m" for k, s in TOOL_CATEGORY_STYLES.items()
}

#: Agent 类型 → 缩写（内置默认）
AGENT_TYPE_ABBREV: dict[str, str] = {
    name: spec.abbrev for name, spec in _builtin_of_kind("agent").items()
}

#: Agent 类型 → Style（内置默认）
AGENT_TYPE_STYLES: dict[str, Style] = {
    name: Style(fg=spec.fg) for name, spec in _builtin_of_kind("agent").items()
}

#: Agent 类型 → 256 色 ANSI（兼容 re-export，同一色号）
AGENT_TYPE_COLORS: dict[str, str] = {
    k: f"\033[38;5;{s.fg}m" for k, s in AGENT_TYPE_STYLES.items()
}

__all__ = [
    "TOOL_ICONS",
    "TOOL_CATEGORY_MAP",
    "TOOL_CATEGORY_STYLES",
    "TOOL_CATEGORY_COLORS",
    "AGENT_TYPE_ABBREV",
    "AGENT_TYPE_STYLES",
    "AGENT_TYPE_COLORS",
    "tool_category",
    "tool_icon",
    "tool_style",
    "category_style",
    "agent_type_abbrev",
    "agent_type_style",
]
