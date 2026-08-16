"""技能根目录扫描 — 目录包 / 扁平 Markdown 两种形态

与 DeepSeek Harness（dsh-skill-filesystem）相同的磁盘形态：

- 目录包：``<root>/<skill-name>/SKILL.md``（可携带相对资源）
- 扁平技能：``<root>/<skill-name>.md``
- 单技能根：``<root>/SKILL.md``（GitHub 单技能仓库安装后）
- 隐藏条目（``.`` 开头）一律跳过

frontmatter 解析失败的条目仅记日志跳过，不影响其他技能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from .frontmatter import parse_frontmatter
from .models import (
    InvocationPolicy,
    SkillDefinition,
    is_skill_name,
)

_logger = logging.getLogger(__name__)

# frontmatter 合法键
_KEY_NAME = "name"
_KEY_DESCRIPTION = "description"
_KEY_WHEN_TO_USE = "whenToUse"
_KEY_DISABLE_MODEL_INVOCATION = "disable-model-invocation"
_KEY_USER_INVOCABLE = "user-invocable"
_KEY_METADATA = "metadata"


def _string_field(data: dict, key: str) -> Optional[str]:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _boolean_field(data: dict, key: str) -> Optional[bool]:
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if value in (1, "1"):
        return True
    if value in (0, "0"):
        return False
    if isinstance(value, str):
        low = value.lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
    return None


def _invocation_policy(data: dict) -> InvocationPolicy:
    """解析调用策略 frontmatter。"""
    disable_model = _boolean_field(data, _KEY_DISABLE_MODEL_INVOCATION)
    user_invocable = _boolean_field(data, _KEY_USER_INVOCABLE)
    return InvocationPolicy(
        model_invocable=disable_model is not True,
        user_invocable=user_invocable is not False,
    )


def parse_skill_file(path: Path) -> Optional[SkillDefinition]:
    """解析单个技能文件为定义（含正文）。

    Args:
        path: SKILL.md 或 *.md 文件路径。

    Returns:
        解析成功返回 SkillDefinition（content 字段已填充），
        无效/缺失返回 None。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        _logger.warning("skill 文件 %s 读取失败: %s", path, e)
        return None

    parsed = parse_frontmatter(raw)
    if parsed is None:
        _logger.warning("skill 文件 %s 忽略: 缺少 YAML frontmatter", path)
        return None
    data, body = parsed

    name = _string_field(data, _KEY_NAME)
    description = _string_field(data, _KEY_DESCRIPTION)
    if name is None or description is None:
        _logger.warning("skill 文件 %s 忽略: frontmatter 必须包含 name 和 description", path)
        return None
    if not is_skill_name(name):
        _logger.warning('skill 文件 %s 忽略: 非法技能名 "%s"', path, name)
        return None

    when_to_use = _string_field(data, _KEY_WHEN_TO_USE)
    metadata = data.get(_KEY_METADATA)
    if not isinstance(metadata, dict):
        metadata = None

    return SkillDefinition(
        name=name,
        description=description,
        invocation=_invocation_policy(data),
        source="",  # 由调用方填充
        provider="",  # 由调用方填充
        when_to_use=when_to_use,
        path=str(path),
        metadata=metadata,
        rank=0,  # 由调用方填充
        directory=str(path.parent),
        content=body.strip(),
    )


def scan_skill_root(root: Path) -> List[SkillDefinition]:
    """扫描一个技能根目录下的全部技能定义。

    Args:
        root: 技能根目录（不存在时返回空列表）。

    Returns:
        候选列表（按条目名排序）。
    """
    if not root.is_dir():
        return []
    candidates: List[SkillDefinition] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as e:
        _logger.warning("skill 根目录 %s 读取失败: %s", root, e)
        return []

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            skill_file = entry / "SKILL.md"
            if skill_file.is_file():
                candidate = parse_skill_file(skill_file)
                if candidate is not None:
                    candidates.append(candidate)
        elif entry.is_file() and entry.name.endswith(".md"):
            candidate = parse_skill_file(entry)
            if candidate is not None:
                candidates.append(candidate)

    return candidates


__all__ = ["parse_skill_file", "scan_skill_root"]
