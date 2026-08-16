"""系统提示词技能章节 — 在环境信息之后注入，构建时只注入一次

设计：技能目录与 auto_load 正文随系统提示词构建（``Agent.__init__`` /
``rebuild_system_prompt``）一次性注入，不再每轮维护目录消息。
技能发生变化时，通过 ``/skill refresh``（或 install/update/remove 后
自动调用）重建系统提示词生效。
"""

from __future__ import annotations

import os
from typing import Optional

from .models import is_model_invocable
from .registry import SkillRegistry, default_registry
from .render import render_skill_content

_SECTION_INTRO = (
    "任务与以下技能描述明确匹配、或用户点名某技能时，"
    "执行任务动作前必须先调用 `skill` 工具加载该技能的完整指令"
    "（返回 <skill_content>），加载后严格遵循其正文；"
    "目录只含摘要，禁止凭摘要臆测技能内容。"
)

_AUTOLOAD_INTRO = (
    "以下技能正文已注入本提示词（skills.auto_load 配置），"
    "直接遵循即可，禁止再调用 `skill` 工具加载它们。"
)


def build_skills_prompt_section(
    cwd: Optional[str] = None,
    registry: Optional[SkillRegistry] = None,
) -> str:
    """构建系统提示词中的技能章节（无技能时返回空字符串）。

    Args:
        cwd: 技能发现的工作目录；None 取 ``os.getcwd()``。
        registry: 技能注册表；None 使用进程级默认（测试可注入）。

    Returns:
        完整章节文本（Markdown），无可用技能且无 auto_load 时为空。
    """
    if registry is None:
        registry = default_registry()
    if not registry.enabled():
        return ""
    cwd = cwd if cwd is not None else os.getcwd()

    entries = registry.catalog_entries(
        cwd, max_length=registry.catalog_description_max_length()
    )
    auto_skills = [
        s for s in registry.auto_load_skills(cwd) if is_model_invocable(s)
    ]

    lines: list = []
    if entries:
        lines.append("## 可用技能（Skills）")
        lines.append(_SECTION_INTRO)
        lines.append("")
        lines.extend(f"- `{name}`: {desc}" for name, desc in entries)
    if auto_skills:
        if lines:
            lines.append("")
        lines.append("### 已自动加载的技能")
        lines.append(_AUTOLOAD_INTRO)
        lines.append("")
        for skill in auto_skills:
            lines.append(render_skill_content(skill))
    return "\n".join(lines)


__all__ = ["build_skills_prompt_section"]
