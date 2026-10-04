"""工具显示名映射（核心层公共模块，零依赖）。

工具注册名（snake_case）→ UI 显示名（PascalCase）映射，供核心层与表现层
展示工具名使用；``tools.registry`` / ``tools._constants`` 从此导入并
re-export 兼容。
"""

from __future__ import annotations

TOOL_DISPLAY_NAME: dict[str, str] = {
    "read_file": "ReadFile",
    "read_image": "ReadImage",
    "write_file": "WriteFile",
    "update_file": "UpdateFile",
    "str_replace_editor": "StrReplaceEditor",
    "file_editor": "FileEditor",
    "bash": "Bash",
    "execute_command": "ExecuteCommand",
    "bash_opt": "BashOpt",
    "subagent": "Subagent",
    "subagent_opt": "SubagentOpt",
    "find": "Find",
    "grep": "Grep",
    "glob": "Glob",
    "search": "Search",
    "cp": "Cp",
    "mv": "Mv",
    "rm": "Rm",
    "mkdir": "Mkdir",
    "user_select": "UserSelect",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "ls": "Ls",
    "skill": "Skill",
    "cordis_inspect": "CordisInspect",
    "cordis_define": "CordisDefine",
    "cordis_run": "CordisRun",
    "cordis_stop": "CordisStop",
    "cordis_undefine": "CordisUndefine",
}


def get_tool_display_name(tool_name: str) -> str:
    """获取工具在 UI 上显示的完整名称（PascalCase）；无映射返回原名称。"""
    return TOOL_DISPLAY_NAME.get(tool_name, tool_name)


__all__ = ["TOOL_DISPLAY_NAME", "get_tool_display_name"]
