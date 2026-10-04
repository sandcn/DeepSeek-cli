"""Agent 类型工具策略 — agent_type → 排除工具集合（公共模块）。

工具可用性的单一真源：各类型 agent（map/review/plan/execute）允许使用的
工具集合。原定义在 ``core/subagent.py``，为打破 ``tools.base ↔
core.subagent`` 循环依赖而提取为独立零依赖模块。

策略差异：
- map: 只读分析，排除写入类工具 + web_search
- review: 纯只读审查（read_file/search/find/ls/web_search），无 shell 能力
- plan: 计划生成，保留 write_file/update_file（另有 .chat/plan/ 路径白名单）
- execute: 默认执行型，保留读写 + bash，排除 web_search + subagent + user_select

旧模块 ``tools._tool_policy`` 仅 re-export 兼容；新代码从此模块导入。
"""

from __future__ import annotations

TOOL_EXCLUSION_MAP = {
    "map": {
        "bash", "bash_opt", "subagent_opt", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "web_search",
        "subagent", "user_select",
        "cordis_define", "cordis_run", "cordis_stop", "cordis_undefine",
    },
    "review": {
        "bash", "bash_opt",
        "subagent_opt", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "subagent", "user_select",
        "cordis_define", "cordis_run", "cordis_stop", "cordis_undefine",
    },
    "plan": {
        "bash", "bash_opt", "subagent_opt",
        "rm",
        "mv",
        "cp",
        "subagent",
        "user_select",
        "cordis_define", "cordis_run", "cordis_stop", "cordis_undefine",
    },
    "execute": {
        "subagent",
        "subagent_opt",
        "user_select",
        "web_search",
    },
}


def get_excluded_tools(agent_type: str) -> set:
    """根据 agent_type 返回应排除的工具名集合。未知类型回退 execute 策略。

    「一切皆插件」：内核挂载策略插件后经 ``ctx.policy`` 解析（策略是独立插件，
    可整体替换）；内核缺失或插件尚在构造中时回退本模块的静态真源。
    策略服务的 ``excluded_tools`` 直接读取 ``TOOL_EXCLUSION_MAP``，不会回调
    本函数，故无递归。
    """
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


__all__ = ["TOOL_EXCLUSION_MAP", "get_excluded_tools"]
