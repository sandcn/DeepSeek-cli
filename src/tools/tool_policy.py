"""Agent 类型工具策略 — agent_type → 排除工具集合（公共模块）。

工具可用性的单一真源：各类型 agent（map/review/plan/execute）允许使用的
工具集合。原定义在 ``core/subagent.py``，为打破 ``tools.base ↔
core.subagent`` 循环依赖而提取为独立零依赖模块。

策略差异（**仅约束 SubAgent 类型**）：
- map: 只读分析，排除写入类工具 + web_search
- review: 纯只读审查（read/grep/glob/ls/web_search），无 shell 能力
- plan: 计划生成，保留 write/edit（另有 ~/.chat/plan/ 路径白名单）
- execute: 默认执行型，保留读写 + bash，排除 web_search + subagent + user_select

主 Agent 不设 agent_type（None）——它不是任何一种子代理类型，排除表不适用，
工具集中包含 user_select / web_search / subagent 等全部工具。

旧模块 ``tools._tool_policy`` 仅 re-export 兼容；新代码从此模块导入。
"""

from __future__ import annotations

import threading
from typing import Callable

from ..core.agent_types import exclusion_map as _exclusion_map
from ..core.cordis_tools import CORDIS_TOOLS

#: agent_type → 排除工具集合。真源下沉到 ``src.core.agent_types`` 的 Agent 类型
#: 注册表（每个类型是清单中的独立插件条目，可被 Profile/Patch/Overlay 声明、
#: 禁用或替换）；此处持有注册表的**同一** dict 引用——MCP 工具策略在运行期向
#: 具体集合 ``add`` 动态工具名仍然可见（向后兼容）。
TOOL_EXCLUSION_MAP = _exclusion_map()


# 全局禁用工具：对**任何** agent（主 Agent 与全部 SubAgent 类型）都不可加载。
# 与 TOOL_EXCLUSION_MAP（按 SubAgent 类型排除）不同，本集合在工具发现/注册
# 阶段即剔除，任何 agent 的 schema 都不会出现这些工具——从加载层杜绝其可用性。
#
# 「一切皆插件」：以下静态集合是**声明真源**（无清单场景/单元测试的默认）；
# 生产路径下每个禁用项由清单中的独立插件条目（``global_disabled_tool``，经
# ``src.plugins.tool_policy_entries``）显式注册进本模块的注册表——条目可被
# Profile/Patch/Overlay 按 id 禁用、覆盖或新增，从而细粒度调整全局禁用集合。
GLOBAL_DISABLED_TOOLS = frozenset(CORDIS_TOOLS)

# ── 模型能力门禁（read_image 只在模型档案声明支持多模态读图时加载） ──
# 用户需求（2026-10-19）：`/models` 模型档案新增 `multimodal`（true/false）
# 开关；false 时 read_image 不进入该模型的 tool schemas，也不能执行。
IMAGE_TOOLS: frozenset = frozenset({"read_image"})


def model_supports_image_tools(model=None) -> bool:
    """模型档案是否声明支持多模态读图（``multimodal=true``）。

    唯一判据是模型档案的 ``multimodal`` 字段（不再按模型名模式推断）；
    配置读取失败/无档案时返回 False（未声明即不加载图像工具）。
    """
    try:
        from ..config.model_profiles import model_supports_multimodal

        return bool(model_supports_multimodal(model))
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "读取模型多模态配置失败（按不支持处理）", exc_info=True,
        )
        return False


def _schema_tool_name(schema) -> str:
    """从 tool schema 提取函数名（非法结构返回空串）。"""
    if not isinstance(schema, dict):
        return ""
    function = schema.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return ""


def filter_schemas_for_model(schemas, model=None) -> list:
    """按模型档案的多模态能力过滤 tool schemas。

    ``multimodal=false``（或无法判定）时移除 :data:`IMAGE_TOOLS`（read_image），
    其余工具原样保留；``true`` 时原样返回副本。过滤只做「移除」，不会重新
    加入被 preset / 其他策略排除的工具。
    """
    items = list(schemas or ())
    if model_supports_image_tools(model):
        return items
    return [s for s in items if _schema_tool_name(s) not in IMAGE_TOOLS]


#: 空模式（单体执行范式）在 schema 层排除的工具。
#: 用户需求（2026-10-25，方案 A）：空模式下 ``subagent`` / ``subagent_opt``
#: **不再移除**——空模式提词本就允许「用户明确点名要求时派发子代理」，
#: 且 ``subagent_opt(op="release")`` 是主 Agent 文件沙盒锁的唯一主动释放
#: 通道，移除会让持锁方无法主动释放。集合保留为空，作为后续按需收紧的
#: 扩展位：需要恢复门禁时把工具名填回本集合即可，调用方无需改动。
EMPTY_MODE_EXCLUDED_TOOLS: frozenset = frozenset()


def filter_schemas_for_empty_mode(schemas) -> list:
    """按空模式排除集合过滤工具 schemas（当前集合为空 → 原样保留）。

    保留该函数作为策略扩展位：空模式需要收紧工具集时，向
    :data:`EMPTY_MODE_EXCLUDED_TOOLS` 加入工具名即可（例如重新排除
    ``subagent`` / ``subagent_opt``），调用方不需要改动。
    """
    return [
        s for s in list(schemas or ())
        if _schema_tool_name(s) not in EMPTY_MODE_EXCLUDED_TOOLS
    ]


# ── 全局禁用工具注册表（声明 + 清单接管 + 扩展） ──────────────

_lock = threading.RLock()
_ABSENT = object()

#: 内置全局禁用工具声明（顺序即展示顺序，每项由清单独立条目注册）
#: 名单来自 ``src.core.cordis_tools``（单一真源，与 SubAgent 排除表、清单条目同源）
_BUILTIN_GLOBAL_DISABLED_TOOLS: tuple[str, ...] = tuple(CORDIS_TOOLS)

_registered_builtin_global: dict[str, bool] = {}
_managed_builtin_global: set = set()
_disabled_builtin_global: set = set()
_extension_global: set = set()


def builtin_global_disabled_tool_ids() -> list[str]:
    """全部内置全局禁用工具 id（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_GLOBAL_DISABLED_TOOLS)


def _normalize_global_ids(ids) -> list[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: list[str] = []
    for item in ids or ():
        if item not in _BUILTIN_GLOBAL_DISABLED_TOOLS:
            raise KeyError(
                f"未知内置全局禁用工具: {item!r}（可用: {list(_BUILTIN_GLOBAL_DISABLED_TOOLS)}）"
            )
        selected.append(item)
    return selected


def active_global_disabled_tools() -> set:
    """当前生效的全局禁用工具集合（内置装配 + 扩展）。"""
    with _lock:
        result: set = set()
        for name in _BUILTIN_GLOBAL_DISABLED_TOOLS:
            if name in _disabled_builtin_global:
                continue
            if name in _registered_builtin_global:
                result.add(name)
                continue
            if name in _managed_builtin_global:
                continue
            result.add(name)
        result |= set(_extension_global)
        return result


def register_builtin_global_disabled_tool(name: str) -> Callable[[], None]:
    """注册/覆盖一个内置全局禁用工具条目；返回幂等撤销。

    Args:
        name: 内置工具名（须在内置声明中）。

    Returns:
        幂等撤销函数（恢复注册前状态）。
    """
    name = str(name)
    if name not in _BUILTIN_GLOBAL_DISABLED_TOOLS:
        raise KeyError(
            f"未知内置全局禁用工具: {name!r}（可用: {list(_BUILTIN_GLOBAL_DISABLED_TOOLS)}）"
        )
    with _lock:
        previous = _registered_builtin_global.get(name, _ABSENT)
        _registered_builtin_global[name] = True

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin_global.pop(name, None)
            else:
                _registered_builtin_global[name] = previous

    return _undo


def unregister_builtin_global_disabled_tool(name: str) -> bool:
    with _lock:
        return _registered_builtin_global.pop(str(name), None) is not None


def set_managed_builtin_global_disabled_tools(names) -> Callable[[], None]:
    """声明这些内置 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_global_ids(names)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin_global]
        _managed_builtin_global.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin_global.discard(item)

    return _undo


def managed_global_disabled_tool_ids() -> list[str]:
    with _lock:
        return sorted(_managed_builtin_global)


def disable_builtin_global_disabled_tools(names) -> Callable[[], None]:
    """禁用一个或多个内置全局禁用项（返回幂等撤销）。"""
    selected = _normalize_global_ids(names)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin_global]
        _disabled_builtin_global.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin_global.discard(item)

    return _undo


def register_global_disabled_tool(name: str) -> Callable[[], None]:
    """注册一个扩展全局禁用工具（第三方经插件扩展）；返回幂等撤销。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("全局禁用工具名必须是非空字符串")
    with _lock:
        previous = name in _extension_global
        _extension_global.add(name)

    def _undo() -> None:
        with _lock:
            if not previous:
                _extension_global.discard(name)

    return _undo


def unregister_global_disabled_tool(name: str) -> bool:
    with _lock:
        if str(name) in _extension_global:
            _extension_global.discard(str(name))
            return True
        return False


def global_disabled_tool_names() -> list[str]:
    return sorted(active_global_disabled_tools())


def clear() -> None:
    """清空扩展项与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension_global.clear()
        _registered_builtin_global.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension_global.clear()
        _registered_builtin_global.clear()
        _managed_builtin_global.clear()
        _disabled_builtin_global.clear()


def is_globally_disabled(tool_name: str) -> bool:
    """该工具是否被全局禁用（任何 agent 都不能加载）。

    「一切皆插件」：解析优先级 —— 内核 ``ctx.policy`` 服务的显式覆盖
    （config ``globally_disabled_tools``）> 注册表当前生效集合（由清单独立
    条目声明，可 patch/overlay 调整）> 无内核/注册表异常时的静态常量兜底。
    """
    try:
        from ..kernel.runtime import active_service

        service = active_service("policy")
        if service is not None:
            disabled = service.globally_disabled_tools()
            if disabled is not None:
                return tool_name in disabled
    except Exception:
        import logging
        logging.getLogger(__name__).debug("忽略异常（best-effort 路径）", exc_info=True)
    try:
        return tool_name in active_global_disabled_tools()
    except Exception:
        return tool_name in GLOBAL_DISABLED_TOOLS


def get_excluded_tools(agent_type: str) -> set:
    """根据 agent_type 返回应排除的工具名集合。未知类型回退 execute 策略。

    ★ ``agent_type`` 为 None/空（主 Agent / 未标注类型）时返回**空集**：
    排除表是 **SubAgent 类型**（map/review/plan/execute）的能力边界，主 Agent
    不属于任何一种子代理类型，不受其约束（修复前主 Agent 因 agent_type=None
    被策略当作 execute → user_select/web_search/subagent 等被误拒）。

    「一切皆插件」：内核挂载策略插件后经 ``ctx.policy`` 解析（策略是独立插件，
    可整体替换）；内核缺失或插件尚在构造中时回退本模块的静态真源。
    策略服务的 ``excluded_tools`` 直接读取 ``TOOL_EXCLUSION_MAP``，不会回调
    本函数，故无递归。
    """
    if not agent_type:
        return set()
    try:
        from ..kernel.runtime import active_service

        service = active_service("policy")
        if service is not None:
            excluded = service.excluded_tools(agent_type)
            if excluded is not None:
                return excluded
    except Exception:
        import logging
        logging.getLogger(__name__).debug("忽略异常（best-effort 路径）", exc_info=True)
    return TOOL_EXCLUSION_MAP.get(agent_type, TOOL_EXCLUSION_MAP["execute"])


__all__ = [
    "TOOL_EXCLUSION_MAP",
    "GLOBAL_DISABLED_TOOLS",
    "IMAGE_TOOLS",
    "EMPTY_MODE_EXCLUDED_TOOLS",
    "is_globally_disabled",
    "get_excluded_tools",
    "model_supports_image_tools",
    "filter_schemas_for_model",
    "filter_schemas_for_empty_mode",
    "builtin_global_disabled_tool_ids",
    "active_global_disabled_tools",
    "global_disabled_tool_names",
    "register_builtin_global_disabled_tool",
    "unregister_builtin_global_disabled_tool",
    "set_managed_builtin_global_disabled_tools",
    "managed_global_disabled_tool_ids",
    "disable_builtin_global_disabled_tools",
    "register_global_disabled_tool",
    "unregister_global_disabled_tool",
    "clear",
    "reset",
]
