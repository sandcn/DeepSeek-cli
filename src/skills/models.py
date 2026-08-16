"""技能(skill)核心模型 — 数据结构与校验

参照 DeepSeek Harness（dsh-skill）的设计：
- ``SkillSummary`` — 面向目录消费者的摘要（不含正文）
- ``SkillCandidate`` — 注册表内部候选（含 rank / locator）
- ``SkillDefinition`` — 完整定义（含正文）
- ``InvocationPolicy`` — 模型/用户两种调用面开关
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src._compat import dataclass

# ── 技能名称语法（kebab-case） ─────────────────────────────
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# ── rank 优先级（越小越优先） ───────────────────────────────
RANK_PROJECT = 100          # 项目 ./.skills
RANK_INSTALLED = 200        # ./.skills/installed（GitHub 安装）
RANK_RUNTIME = 250          # 进程内运行时注册


def is_skill_name(name: str) -> bool:
    """判断是否为合法的 kebab-case 技能名。"""
    return bool(SKILL_NAME_RE.match(name))


@dataclass
class InvocationPolicy:
    """技能调用开关：模型面 / 用户面。"""

    model_invocable: bool = True
    user_invocable: bool = True


@dataclass
class SkillSummary:
    """技能摘要 — 目录消费方看到的最小信息。"""

    name: str
    description: str
    invocation: InvocationPolicy
    source: str
    provider: str
    when_to_use: Optional[str] = None
    path: Optional[str] = None
    metadata: Optional[dict] = None


@dataclass
class SkillCandidate(SkillSummary):
    """注册表内部候选 — 摘要 + 排序与定位信息。"""

    rank: int = RANK_PROJECT
    directory: Optional[str] = None


@dataclass
class SkillDefinition(SkillCandidate):
    """完整技能定义 — 含指令正文。"""

    content: str = ""


def is_model_invocable(skill: SkillSummary) -> bool:
    """技能是否允许模型调用。"""
    return skill.invocation.model_invocable


def is_user_invocable(skill: SkillSummary) -> bool:
    """技能是否允许用户以 /name 手势直接调用。"""
    return skill.invocation.user_invocable


def normalize_invocation(value: Any) -> InvocationPolicy:
    """将 frontmatter/注册参数归一化为 InvocationPolicy。

    支持：``None``（全开）、``InvocationPolicy``、dict（键为
    model_invocable / user_invocable）。
    """
    if value is None:
        return InvocationPolicy()
    if isinstance(value, InvocationPolicy):
        return value
    if isinstance(value, dict):
        return InvocationPolicy(
            model_invocable=bool(value.get("model_invocable", True)),
            user_invocable=bool(value.get("user_invocable", True)),
        )
    raise TypeError(f"非法 invocation 策略: {value!r}")


__all__ = [
    "InvocationPolicy",
    "SkillCandidate",
    "SkillDefinition",
    "SkillSummary",
    "RANK_INSTALLED",
    "RANK_PROJECT",
    "RANK_RUNTIME",
    "SKILL_NAME_RE",
    "is_model_invocable",
    "is_skill_name",
    "is_user_invocable",
    "normalize_invocation",
]
