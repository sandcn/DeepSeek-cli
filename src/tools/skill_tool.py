"""skill 工具 — 模型加载技能完整指令的入口

参照 DeepSeek Harness 的 dsh-tool-skill：
- 工具返回规范的 ``<skill_content>`` 块（与 /name 手势注入同形）；
- 技能目录（``<available_skills>``）已随系统提示词注入（环境信息之后，
  构建时一次），本工具只负责按名加载正文；
- 未知技能 / 非模型可调用 → 返回错误文本（以 ``(`` 开头，与
  本项目工具错误约定一致）。
"""

from __future__ import annotations

import os
from typing import Optional

from ..skills import default_registry, is_model_invocable, render_skill_content
from .base import Func, tool_metadata

_TOOL_DESCRIPTION = (
    "加载可用技能（skill）的完整指令。调用前必须先确认技能名来自会话中的"
    "可用技能目录（<available_skills>）。技能是一组可复用的任务专用指令；"
    "当用户点名某个技能、或任务与某技能描述明确匹配时，在执行任务动作前"
    "先用本工具加载该技能，然后遵循其完整指令。"
)


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="general",
    priority=100,
    tool_category="read",
    description="加载技能（skill）的完整指令",
)
class SkillFunc(Func):
    """加载技能工具。"""

    name = "skill"

    def __init__(self, name: Optional[str] = None):
        """初始化。

        Args:
            name: 要加载的技能名（kebab-case）。
        """
        super().__init__()
        self.name_arg = name

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "skill",
                "description": _TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "技能名（来自可用技能目录，kebab-case）。"
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        name = arguments.get("name", "") if isinstance(arguments, dict) else ""
        return str(name)[:max_len]

    async def execute(self) -> str:
        """加载技能正文并渲染为 <skill_content> 块。"""
        name = self.name_arg
        if not name or not isinstance(name, str):
            return "(技能名缺失: 必须提供 name 参数)"
        registry = default_registry()
        if not registry.enabled():
            return "(技能系统已禁用，可在 ~/.chat_config/chatrc.json 的 skills.enabled 启用)"
        try:
            skill = registry.get(name, cwd=os.getcwd())
        except Exception as e:
            return f"(技能 \"{name}\" 加载失败: {e})"
        if skill is None:
            return f"(技能 \"{name}\" 不存在或不可用，请从可用技能目录中确认名称)"
        if not is_model_invocable(skill):
            return f"(技能 \"{name}\" 不允许模型调用)"
        return render_skill_content(skill)


__all__ = ["SkillFunc"]
