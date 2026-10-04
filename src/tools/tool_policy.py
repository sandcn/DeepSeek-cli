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
    },
    "review": {
        "bash", "bash_opt",
        "subagent_opt", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "subagent", "user_select",
    },
    "plan": {
        "bash", "bash_opt", "subagent_opt",
        "rm",
        "mv",
        "cp",
        "subagent",
        "user_select",
    },
    "execute": {
        "subagent",
        "subagent_opt",
        "user_select",
        "web_search",
    },
}


def get_excluded_tools(agent_type: str) -> set:
    """根据 agent_type 返回应排除的工具名集合。未知类型回退 execute 策略。"""
    return TOOL_EXCLUSION_MAP.get(agent_type, TOOL_EXCLUSION_MAP["execute"])


__all__ = ["TOOL_EXCLUSION_MAP", "get_excluded_tools"]
