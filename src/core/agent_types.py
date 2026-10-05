"""Agent 类型注册表 — SubAgent 类型的单一来源（一切皆插件）。

每一种 SubAgent 类型（``map`` / ``review`` / ``plan`` / ``execute`` 及第三方
自定义类型）都由本模块的**声明**描述，并由清单中的**独立插件条目**
（``agent_type``，经 ``src.plugins.agent_type_entries``）显式注册——因此类型
可被 Profile/Bundle 声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖或替换，
而非硬编码在 ``SubAgent.__init__`` / ``tool_policy`` / 提示词构建器里。

**清单接管**：``subagents`` 聚合插件收到组合根注入的 ``managed_agent_types``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_agent_types`` 声明
这些 id 由清单条目负责——对应内置类型不再走默认装配；被禁用（未挂载）的条目
因此真正缺席。无清单（单元测试、独立调用）时无接管，全部内置类型默认生效。

类型规格（:class:`AgentTypeSpec`）声明：

- ``prompt_builder``：提示词端口方法名（如 ``build_map_agent_prompt``），
  空串表示通用子代理提示词；
- ``agent_name``：提示词文件基名（``prompts_export_<agent_name>.md``）；
- ``exclusions``：该类型排除的工具集合（元组）；
- ``low_model``：是否优先使用低优先级模型；
- ``path_whitelist``：写入路径白名单标识（``"plan"`` 表示仅允许 ``.chat/plan/``）；
- ``mcp_agent_type``：提示词 MCP 章节使用的权限类型（空表示用自身名字）。

排除集合仍以可变 ``dict[str, set]`` 形式（``TOOL_EXCLUSION_MAP``）对外呈现，
MCP 工具策略在运行期向其 ``add`` 动态工具名——注册表持有同一 dict，保证
「运行期注册的排除项」与「清单声明的类型」同源。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

_lock = threading.RLock()
_ABSENT = object()


@dataclass(frozen=True)
class AgentTypeSpec:
    """一个 SubAgent 类型的规格。"""

    name: str
    description: str = ""
    prompt_builder: str = ""
    agent_name: str = ""
    exclusions: Tuple[str, ...] = ()
    low_model: bool = False
    path_whitelist: str = ""
    mcp_agent_type: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "prompt_builder": self.prompt_builder,
            "agent_name": self.agent_name,
            "exclusions": list(self.exclusions),
            "low_model": self.low_model,
            "path_whitelist": self.path_whitelist,
            "mcp_agent_type": self.mcp_agent_type,
        }


# ── 内置类型声明（排除集合的静态真源） ─────────────────────

_CORDIS_TOOLS = ("cordis_define", "cordis_run", "cordis_stop", "cordis_undefine")

_BUILTIN_SPECS: Tuple[AgentTypeSpec, ...] = (
    AgentTypeSpec(
        name="map",
        description="只读分析型：项目探底、模块地图、调用链追踪",
        prompt_builder="build_map_agent_prompt",
        agent_name="map",
        exclusions=(
            "bash", "bash_opt", "subagent_opt", "write_file", "update_file",
            "rm", "mv", "cp", "mkdir", "web_search", "subagent", "user_select",
            *_CORDIS_TOOLS,
        ),
        low_model=True,
    ),
    AgentTypeSpec(
        name="review",
        description="代码审查型：纯只读审阅（含 web_search），P0-P3 分级",
        prompt_builder="build_review_agent_prompt",
        agent_name="review",
        exclusions=(
            "bash", "bash_opt", "subagent_opt", "write_file", "update_file",
            "rm", "mv", "cp", "mkdir", "subagent", "user_select",
            *_CORDIS_TOOLS,
        ),
    ),
    AgentTypeSpec(
        name="plan",
        description="计划型：任务拆解并写入 .chat/plan/ 目录",
        prompt_builder="build_plan_agent_prompt",
        agent_name="plan",
        exclusions=(
            "bash", "bash_opt", "subagent_opt", "rm", "mv", "cp",
            "subagent", "user_select",
            *_CORDIS_TOOLS,
        ),
        path_whitelist="plan",
    ),
    AgentTypeSpec(
        name="execute",
        description="执行型：读写 + bash，执行计划步骤并返回修改文件列表",
        prompt_builder="build_execute_agent_system_prompt",
        agent_name="execute",
        exclusions=("subagent", "subagent_opt", "user_select", "web_search"),
        low_model=True,
    ),
)

#: 通用子代理类型（未知/空 agent_type 使用）
_GENERIC_SPEC = AgentTypeSpec(
    name="sub",
    description="通用子代理：只读 + 基础工具",
    prompt_builder="build_subagent_prompt",
    agent_name="sub",
)

_builtin_specs: Dict[str, AgentTypeSpec] = {spec.name: spec for spec in _BUILTIN_SPECS}

#: 由清单条目注册/覆盖的内置类型（name → AgentTypeSpec）
_registered_builtin: Dict[str, AgentTypeSpec] = {}
#: 由清单接管的 id（默认装配被抑制）
_managed_builtin: set = set()
#: 显式禁用的内置类型
_disabled_builtin: set = set()
#: 第三方扩展类型（不在内置声明中）
_extension_specs: Dict[str, AgentTypeSpec] = {}

#: agent_type → 排除工具集合（可变；MCP 工具策略运行期可 add）
#: 注册表持有该 dict 本身，外部（tool_policy.TOOL_EXCLUSION_MAP）持有同一引用。
_exclusion_map: Dict[str, set] = {
    spec.name: set(spec.exclusions) for spec in _BUILTIN_SPECS
}


def _normalize_names(names) -> List[str]:
    if isinstance(names, str):
        names = [names]
    selected: List[str] = []
    for item in names or ():
        if item not in _builtin_specs:
            raise KeyError(f"未知内置 Agent 类型: {item!r}（可用: {list(_builtin_specs)}）")
        selected.append(item)
    return selected


# ── 内置类型：装配读取 ─────────────────────────────────────


def builtin_agent_type_ids() -> list[str]:
    """全部内置 Agent 类型 id（声明顺序）。"""
    return list(_builtin_specs)


def default_agent_type_spec(name: str) -> AgentTypeSpec:
    try:
        return _builtin_specs[name]
    except KeyError:
        raise KeyError(f"未知内置 Agent 类型: {name!r}（可用: {list(_builtin_specs)}）") from None


def _active_builtin_specs() -> Dict[str, AgentTypeSpec]:
    result: Dict[str, AgentTypeSpec] = {}
    for name, default in _builtin_specs.items():
        if name in _disabled_builtin:
            continue
        override = _registered_builtin.get(name)
        if override is not None:
            result[name] = override
            continue
        if name in _managed_builtin:
            continue
        result[name] = default
    return result


def active_specs() -> Dict[str, AgentTypeSpec]:
    """当前生效的 Agent 类型规格（内置装配 + 扩展），按名字。"""
    with _lock:
        specs = _active_builtin_specs()
        specs.update(_extension_specs)
        return specs


def agent_type_names() -> list[str]:
    return sorted(active_specs())


def get_spec(name: str, default: Optional[AgentTypeSpec] = None) -> AgentTypeSpec:
    """解析类型规格；未知/空返回 ``default``（缺省为通用子代理规格）。"""
    if not name:
        return default if default is not None else _GENERIC_SPEC
    with _lock:
        specs = _active_builtin_specs()
        specs.update(_extension_specs)
        return specs.get(name, default if default is not None else _GENERIC_SPEC)


# ── 排除集合 ───────────────────────────────────────────────


def exclusion_map() -> Dict[str, set]:
    """返回 ``agent_type → 排除工具集合`` 的可变映射（与注册表同源）。"""
    return _exclusion_map


def excluded_tools(agent_type: str, default: Optional[set] = None) -> set:
    """返回该类型的排除工具集合副本（未知类型回退 ``execute``）。

    ``agent_type`` 为空（主 Agent）时返回空集。
    """
    if not agent_type:
        return set()
    with _lock:
        entry = _exclusion_map.get(agent_type)
        if entry is None:
            entry = _exclusion_map.get("execute", set())
        return set(entry)


def uses_low_model(agent_type: str) -> bool:
    return bool(get_spec(agent_type).low_model)


def path_whitelist(agent_type: str) -> str:
    return get_spec(agent_type).path_whitelist


def prompt_builder_name(agent_type: str) -> str:
    return get_spec(agent_type).prompt_builder


def agent_name(agent_type: str) -> str:
    return get_spec(agent_type).agent_name


def mcp_agent_type(agent_type: str) -> str:
    spec = get_spec(agent_type)
    return spec.mcp_agent_type or spec.name


# ── 内置类型：清单条目注册 / 覆盖 ──────────────────────────


def register_builtin_agent_type(name: str, spec: Optional[AgentTypeSpec] = None) -> Callable[[], None]:
    """注册/覆盖一个内置 Agent 类型（``spec=None`` 用默认声明）。

    返回幂等撤销：恢复注册前的状态（含排除集合）。
    """
    if name not in _builtin_specs:
        raise KeyError(f"未知内置 Agent 类型: {name!r}（可用: {list(_builtin_specs)}）")
    with _lock:
        previous = _registered_builtin.get(name, _ABSENT)
        previous_exclusions = _exclusion_map.get(name, _ABSENT)
        effective = spec if spec is not None else _builtin_specs[name]
        _registered_builtin[name] = effective
        _exclusion_map[name] = set(effective.exclusions)

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(name, None)
            else:
                _registered_builtin[name] = previous
            if previous_exclusions is _ABSENT:
                _exclusion_map.pop(name, None)
            else:
                _exclusion_map[name] = previous_exclusions

    return _undo


def unregister_builtin_agent_type(name: str) -> bool:
    with _lock:
        return _registered_builtin.pop(name, None) is not None


def register_agent_type(spec: AgentTypeSpec) -> Callable[[], None]:
    """注册一个扩展 Agent 类型（返回幂等撤销）。"""
    if not isinstance(spec, AgentTypeSpec) or not spec.name:
        raise TypeError(f"Agent 类型规格非法: {spec!r}")
    with _lock:
        previous = _extension_specs.get(spec.name, _ABSENT)
        previous_exclusions = _exclusion_map.get(spec.name, _ABSENT)
        _extension_specs[spec.name] = spec
        _exclusion_map.setdefault(spec.name, set(spec.exclusions))

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension_specs.pop(spec.name, None)
            else:
                _extension_specs[spec.name] = previous
            if previous_exclusions is _ABSENT:
                _exclusion_map.pop(spec.name, None)
            else:
                _exclusion_map[spec.name] = previous_exclusions

    return _undo


def unregister_agent_type(name: str) -> bool:
    with _lock:
        return _extension_specs.pop(name, None) is not None


# ── 清单接管 / 禁用 ────────────────────────────────────────


def set_managed_builtin_agent_types(names) -> Callable[[], None]:
    """声明这些内置类型 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_agent_type_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_agent_types(names) -> Callable[[], None]:
    """禁用一个或多个内置类型（返回幂等撤销）。"""
    selected = _normalize_names(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def clear() -> None:
    """清空扩展类型与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _registered_builtin.clear()
        _extension_specs.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _registered_builtin.clear()
        _extension_specs.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()
        for name, spec in _builtin_specs.items():
            _exclusion_map[name] = set(spec.exclusions)


# ── 提示词端口映射 ─────────────────────────────────────────


def build_prompt_parts(port: Any, agent_type: str, cwd: Optional[str] = None) -> list[str]:
    """经提示词端口为某类型构建系统提词（类型 → 端口方法，来自注册表）。"""
    builder = prompt_builder_name(agent_type) or "build_subagent_prompt"
    method = getattr(port, builder, None)
    if method is None:
        method = getattr(port, "build_subagent_prompt")
    try:
        return list(method(cwd=cwd))
    except TypeError:
        return list(method())


__all__ = [
    "AgentTypeSpec",
    "exclusion_map",
    "excluded_tools",
    "uses_low_model",
    "path_whitelist",
    "prompt_builder_name",
    "agent_name",
    "mcp_agent_type",
    "active_specs",
    "agent_type_names",
    "get_spec",
    "builtin_agent_type_ids",
    "default_agent_type_spec",
    "register_builtin_agent_type",
    "unregister_builtin_agent_type",
    "register_agent_type",
    "unregister_agent_type",
    "set_managed_builtin_agent_types",
    "managed_agent_type_ids",
    "disable_builtin_agent_types",
    "build_prompt_parts",
    "clear",
    "reset",
]
