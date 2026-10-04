"""Preset 插件 — 提供 ``ctx.presets``（每会话能力组合）。

对应 dsh 的 per-session preset：同一进程里不同会话可以跑不同的工具/persona
组合。Preset 声明工具排除/包含集合与可选模型覆盖；``scope(name)`` 经
``ctx.isolate`` 派生隔离子上下文（空间可组合性），把 preset 挂到会话自己的
作用域下，互不污染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..kernel import Service, plugin


@dataclass(frozen=True)
class Preset:
    """一个能力组合预设。"""

    name: str
    description: str = ""
    tool_excludes: Tuple[str, ...] = ()
    tool_includes: Tuple[str, ...] = ()
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "tool_excludes": list(self.tool_excludes),
            "tool_includes": list(self.tool_includes),
            "model": self.model,
        }

    def allows(self, tool_name: str) -> bool:
        if self.tool_includes and tool_name not in self.tool_includes:
            return False
        return tool_name not in self.tool_excludes


BUILTIN_PRESETS = (
    Preset("standard", "标准模式：全部工具可用（默认）"),
    Preset(
        "minimal",
        "最小模式：只读 + 基础交互工具",
        tool_excludes=(
            "bash", "bash_opt", "subagent", "subagent_opt", "user_select",
            "web_search", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        ),
    ),
    Preset(
        "code",
        "编码模式：读写 + shell，无网络与委派",
        tool_excludes=("web_search", "subagent", "subagent_opt", "user_select"),
    ),
)


class PresetsService(Service):
    """预设服务 — 占据 ``ctx.presets``。"""

    provide = "presets"
    name = "presets"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._presets: Dict[str, Preset] = {}
        for preset in BUILTIN_PRESETS:
            self._presets[preset.name] = preset

    # ── 注册表 ───────────────────────────────────────────

    def list(self) -> List[str]:
        return sorted(self._presets)

    def all(self) -> List[Preset]:
        return [self._presets[name] for name in self.list()]

    def get(self, name: str) -> Preset:
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"未定义的 preset: {name!r}（可用: {self.list()}）")
        return preset

    def register(self, preset: Preset) -> None:
        self._presets[preset.name] = preset

    def unregister(self, name: str) -> bool:
        return self._presets.pop(name, None) is not None

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
    return PresetsService(ctx)
