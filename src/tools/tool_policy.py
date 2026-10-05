"""Agent 类型工具策略 — agent_type → 排除工具集合（公共模块）。

工具可用性的单一真源：各类型 agent（map/review/plan/execute）允许使用的
工具集合。原定义在 ``core/subagent.py``，为打破 ``tools.base ↔
core.subagent`` 循环依赖而提取为独立零依赖模块。

策略差异（**仅约束 SubAgent 类型**）：
- map: 只读分析，排除写入类工具 + web_search
- review: 纯只读审查（read_file/search/find/ls/web_search），无 shell 能力
- plan: 计划生成，保留 write_file/update_file（另有 .chat/plan/ 路径白名单）
- execute: 默认执行型，保留读写 + bash，排除 web_search + subagent + user_select

主 Agent 不设 agent_type（None）——它不是任何一种子代理类型，排除表不适用，
工具集中包含 user_select / web_search / subagent 等全部工具。

旧模块 ``tools._tool_policy`` 仅 re-export 兼容；新代码从此模块导入。
"""

from __future__ import annotations

from ..core.agent_types import exclusion_map as _exclusion_map

#: agent_type → 排除工具集合。真源下沉到 ``src.core.agent_types`` 的 Agent 类型
#: 注册表（每个类型是清单中的独立插件条目，可被 Profile/Patch/Overlay 声明、
#: 禁用或替换）；此处持有注册表的**同一** dict 引用——MCP 工具策略在运行期向
#: 具体集合 ``add`` 动态工具名仍然可见（向后兼容）。
TOOL_EXCLUSION_MAP = _exclusion_map()


# 全局禁用工具：对**任何** agent（主 Agent 与全部 SubAgent 类型）都不可加载。
# 与 TOOL_EXCLUSION_MAP（按 SubAgent 类型排除）不同，本集合在工具发现/注册
# 阶段即剔除，任何 agent 的 schema 都不会出现这些工具——从加载层杜绝其可用性。
GLOBAL_DISABLED_TOOLS = frozenset({
    "cordis_inspect",
    "cordis_define",
    "cordis_run",
    "cordis_stop",
    "cordis_undefine",
})


def is_globally_disabled(tool_name: str) -> bool:
    """该工具是否被全局禁用（任何 agent 都不能加载）。

    「一切皆插件」：全局禁用集合不再只是模块常量——内核挂载策略插件
    （``ctx.policy``）后，以其 config ``globally_disabled_tools`` 为准（可经
    Profile/Patch/Overlay 调整）；内核缺失或策略插件尚在构造中时回退静态常量。
    """
    try:
        from ..kernel.runtime import active_service

        service = active_service("policy")
        if service is not None:
            disabled = service.globally_disabled_tools()
            if disabled is not None:
                return tool_name in disabled
    except Exception:
        pass
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
        pass
    return TOOL_EXCLUSION_MAP.get(agent_type, TOOL_EXCLUSION_MAP["execute"])


__all__ = [
    "TOOL_EXCLUSION_MAP",
    "GLOBAL_DISABLED_TOOLS",
    "is_globally_disabled",
    "get_excluded_tools",
]
