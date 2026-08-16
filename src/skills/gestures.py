"""技能手势 — /skill-name 扫描与注入

- ``scan_skill_gestures``：扫描用户消息中的 /name 手势（词边界规则，
  避免误匹配路径 ``/usr/bin``、分数 ``5/8``、URL ``http://x``）。
- ``inject_skill_gestures``：在 ``run_round`` 添加用户消息后调用，
  把用户可调用技能的正文（``<skill_content>``，与 ``skill`` 工具结果
  同形）作为 user 消息注入对话。
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from .models import is_skill_name, is_user_invocable
from .registry import SkillRegistry, default_registry
from .render import render_skill_content

_logger = logging.getLogger(__name__)

# 词边界 /name 手势：捕获组 2 为技能名
SKILL_GESTURE_RE = re.compile(r"(^|\s)\/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)")


def scan_skill_gestures(text: str) -> List[str]:
    """扫描文本中的 /name 手势，按出现顺序去重。

    Args:
        text: 用户输入文本。

    Returns:
        候选技能名列表（未对注册表校验）。
    """
    names: List[str] = []
    for match in SKILL_GESTURE_RE.finditer(text):
        name = match.group(2)
        if name is not None and is_skill_name(name) and name not in names:
            names.append(name)
    return names


def inject_skill_gestures(agent, user_input: str, cwd: Optional[str] = None) -> int:
    """扫描用户输入中的 /name 手势并把技能正文注入对话。

    在 ``run_round`` 添加用户消息之后调用。注入的技能正文以
    ``<skill_content>`` user 消息形式追加（与 ``skill`` 工具结果同形）。

    Args:
        agent: Agent/SubAgent 实例（需有 add_user_message）。
        user_input: 用户原始输入。
        cwd: 技能查找的工作目录；None 取 os.getcwd()。

    Returns:
        注入的技能数量。
    """
    try:
        registry: SkillRegistry = default_registry()
        if not registry.enabled():
            return 0
    except Exception:
        _logger.debug("技能注册表不可用，跳过手势注入", exc_info=True)
        return 0

    injected = 0
    for name in scan_skill_gestures(user_input):
        try:
            skill = registry.get(name, cwd=cwd)
        except Exception:
            _logger.debug("技能 %s 加载失败，跳过", name, exc_info=True)
            continue
        if skill is None or not is_user_invocable(skill):
            continue
        try:
            agent.add_user_message(render_skill_content(skill))
            injected += 1
        except Exception:
            _logger.warning("技能 %s 正文注入失败", name, exc_info=True)
    return injected


__all__ = ["SKILL_GESTURE_RE", "inject_skill_gestures", "scan_skill_gestures"]
