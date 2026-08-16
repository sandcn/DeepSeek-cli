"""技能渲染 — 模型可见的规范 <skill_content> 形态

参照 DeepSeek Harness 的 ``renderSkillContent``：`skill` 工具结果与
``/name`` 手势注入共用同一渲染函数，保证两条路径模型看到的格式一致。

技能正文为受信任的本地内容，原样嵌入；仅元数据（名称/路径/URL）做
转义，防止破坏外层框架标签。
"""

from __future__ import annotations

from .models import SkillDefinition


def _escape_attr(value: str) -> str:
    """转义属性值（& " <）。"""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )


def escape_text(value: str) -> str:
    """转义嵌入正文中的元数据文本（& < >）。"""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _resource_hint(skill: SkillDefinition) -> list:
    """生成 <skill_resources> 提示行。"""
    base = skill.directory
    if base:
        return [
            f"Base directory for this skill: {escape_text(base)}",
            "Resolve relative paths mentioned by this skill against the base directory before using them. "
            "Load referenced resources only as needed.",
        ]
    return [
        f'Resources for this skill are managed by provider "{escape_text(skill.provider)}".',
        "Load referenced resources only as needed.",
    ]


def render_skill_content(skill: SkillDefinition) -> str:
    """渲染完整 <skill_content> 块。

    Args:
        skill: 技能定义（name/provider/directory/content 至少可用）。

    Returns:
        模型可见的完整技能内容。
    """
    resource_hint = _resource_hint(skill)
    lines = [
        f'<skill_content name="{_escape_attr(skill.name)}">',
        "<skill_resources>",
        *resource_hint,
        "</skill_resources>",
        "",
        "<skill_instructions>",
        skill.content,
        "</skill_instructions>",
        "</skill_content>",
    ]
    return "\n".join(lines)


__all__ = ["escape_text", "render_skill_content"]
