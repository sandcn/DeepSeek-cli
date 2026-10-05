"""Preset 插件 — 提供 ``ctx.presets``（每会话能力组合）。

对应 dsh 的 per-session preset：同一进程里不同会话可以跑不同的工具/persona
组合。Preset 声明工具排除/包含集合与可选模型覆盖；``scope(name)`` 经
``ctx.isolate`` 派生隔离子上下文（空间可组合性），把 preset 挂到会话自己的
作用域下，互不污染。

「一切皆插件」：内置 preset（standard/minimal/code）不再是本模块的硬编码元组，
而是由清单中的独立条目（``preset``，经 ``src.plugins.preset_entries``）注册进
``src.core.presets`` 注册表——可按 Profile/Patch 禁用、覆盖或替换；外部插件可
经 ``ctx.presets.register(...)`` 注册自定义 preset。
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..core.presets import (
    Preset,
    active_presets,
    builtin_preset_names,
    disable_builtin_presets,
    managed_preset_names,
    register_preset,
    set_managed_builtin_presets,
    unregister_preset,
)
from ..kernel import Service, plugin


class PresetsService(Service):
    """预设服务 — 占据 ``ctx.presets``。"""

    provide = "presets"
    name = "presets"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_presets") or ()
        if managed:
            undo_managed = set_managed_builtin_presets(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_presets") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_presets(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 注册表 ───────────────────────────────────────────

    def list(self) -> List[str]:
        return sorted(active_presets())

    def all(self) -> List[Preset]:
        presets = active_presets()
        return [presets[name] for name in sorted(presets)]

    def get(self, name: str) -> Preset:
        preset = active_presets().get(name)
        if preset is None:
            raise KeyError(f"未定义的 preset: {name!r}（可用: {self.list()}）")
        return preset

    def register(self, preset: Preset):
        """注册一个扩展 preset（注册即副作用，卸载时自动撤销）。"""
        undo = register_preset(preset)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, name: str) -> bool:
        return unregister_preset(name)

    # ── 自省 ─────────────────────────────────────────────

    def builtin(self) -> List[str]:
        """全部内置 preset 名（含被接管/禁用的）。"""
        return builtin_preset_names()

    def managed(self) -> List[str]:
        return managed_preset_names()

    def disabled(self) -> List[str]:
        return sorted(self._disabled)

    # ── 作用域（空间可组合性） ───────────────────────────

    def scope(self, name: str) -> Any:
        """派生隔离子上下文并把 preset 挂到该作用域。"""
        preset = self.get(name)
        scope = self.ctx.isolate("preset")
        scope.provide("preset", preset)
        return scope

    def resolve_scoped(self, name: str) -> Optional[Preset]:
        """在隔离作用域内解析 preset（自省/测试用）。"""
        return self.scope(name).preset

    # ── 应用 ─────────────────────────────────────────────

    def allows(self, name: str, tool_name: str) -> bool:
        return self.get(name).allows(tool_name)

    def filter_schemas(self, name: str, schemas) -> List[dict]:
        preset = self.get(name)
        result = []
        for schema in schemas:
            function = schema.get("function", {}) if isinstance(schema, dict) else {}
            tool_name = function.get("name", "")
            if preset.allows(tool_name):
                result.append(schema)
        return result

    def apply_to_agent(self, name: str, agent: Any) -> None:
        """把 preset 应用到 Agent（过滤工具 schema + 可选模型覆盖）。"""
        preset = self.get(name)
        try:
            agent.preset = preset.name
        except Exception:
            pass
        tools = getattr(agent, "tools", None)
        if tools:
            agent.tools = tuple(self.filter_schemas(name, tools))
        if preset.model:
            try:
                agent.model = preset.model
            except Exception:
                pass


@plugin("presets", provide=["presets"])
def apply(ctx):
    return PresetsService(ctx, ctx.config)


__all__ = ["Preset", "PresetsService", "apply"]
