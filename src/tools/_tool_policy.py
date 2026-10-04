"""Agent 类型工具策略 — agent_type → 排除工具集合

工具可用性的单一真源：各类型 agent（map/review/plan/execute）允许使用的
工具集合。原定义在 ``core/subagent.py``，为打破
``tools.base ↔ core.subagent`` 循环依赖而提取为独立模块（零依赖）。

策略差异说明：
- map: 只读分析，排除所有写入类工具 + web_search
- review: 代码审查，排除所有写入类工具 + bash/bash_opt，保留 web_search（可查文档）；
  2026-08-21（用户需求）起删除 bash/bash_opt——review 为纯只读审查（read_file/
  search/find/ls/web_search），彻底无 shell 执行能力，从工具层杜绝任何修改行为
- plan: 计划生成，保留 write_file/update_file，但在 FileToolBase
  ._validate_path_and_size() 中有额外的路径白名单校验（仅限 .chat/plan/）
- execute: 计划执行型（默认），保留读写工具 + bash，排除 web_search + subagent + user_select，
  无路径白名单限制，用于执行计划文件步骤并返回修改文件列表
"""

from __future__ import annotations

_TOOL_EXCLUSION_MAP = {
    "map": {
        "bash", "bash_opt", "subagent_opt", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "web_search",
        "subagent", "user_select",
    },
    "review": {
        # bash/bash_opt 已删除（2026-08-21 用户需求）：review 为纯只读审查，
        # 无任何 shell 执行能力，从工具层杜绝用 bash 修改文件的可能。
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


def _get_excluded_tools(agent_type: str) -> set:
    """根据 agent_type 返回应排除的工具名集合。未知类型回退 execute 策略。"""
    return _TOOL_EXCLUSION_MAP.get(agent_type, _TOOL_EXCLUSION_MAP["execute"])


__all__ = ["_TOOL_EXCLUSION_MAP", "_get_excluded_tools"]
