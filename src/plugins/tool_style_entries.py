"""工具表现条目插件 — 清单中每个工具 / 类别 / Agent 类型一个独立插件条目。

「一切皆插件」：工具/类别/Agent 类型表现映射（工具名 → 类别/图标、类别 →
配色、Agent 类型 → 缩写/配色）不再硬编码在 ``src.tui._tool_icons`` 的字典里，
而是由清单中的独立条目声明::

    - id: tool_style_tool_bash
      plugin: src.plugins.tool_style_entries:apply_tool_style
      config:
        id: tool_bash                       # 内置表现条目 id（可被 patch/overlay 定位）
        # category: shell                   # 可选：覆盖类别
        # icon: "\\u26a1"                    # 可选：覆盖图标

    - id: tool_style_cat_shell
      plugin: src.plugins.tool_style_entries:apply_tool_style
      config:
        id: cat_shell
        # fg: 41                            # 可选：覆盖配色色号

    - id: tool_style_agent_map
      plugin: src.plugins.tool_style_entries:apply_tool_style
      config:
        id: agent_map
        # abbrev: mp                        # 可选：覆盖缩写
        # fg: 33                            # 可选：覆盖配色色号

插件挂载时把该 id 的内置表现注册进注册表（``spec=None`` 用默认规格）；卸载时
撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置表现随之缺席
（``tool_styles`` 聚合插件经 ``managed_tool_styles`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: tool_style 条目可由 config 覆盖的字段
_SPEC_FIELDS = ("kind", "name", "category", "icon", "abbrev", "fg")


def _spec_from_config(spec_id: str, config: dict):
    from ..tui._tool_styles import PresentationSpec, default_presentation

    if not any(field in config for field in _SPEC_FIELDS):
        return None
    base = default_presentation(spec_id)
    return PresentationSpec(
        id=spec_id,
        kind=str(config.get("kind", base.kind)),
        name=str(config.get("name", base.name)),
        category=str(config.get("category", base.category)),
        icon=str(config.get("icon", base.icon)),
        abbrev=str(config.get("abbrev", base.abbrev)),
        fg=int(config.get("fg", base.fg)),
    )


@plugin("tool_style")
def apply_tool_style(ctx):
    from ..tui._tool_styles import register_builtin_presentation

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("tool_style 条目缺少 config.id")
    undo = register_builtin_presentation(spec_id, _spec_from_config(spec_id, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_tool_style"]
