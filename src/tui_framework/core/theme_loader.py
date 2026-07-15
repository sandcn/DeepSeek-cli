"""轻量级 YAML 主题加载器 — 零第三方依赖。

从 ~/.deepseek/themes/*.yaml 加载用户自定义主题，
与 core/theme.py 的层次化 Theme 系统集成。

设计原则：
  - 零外部依赖：纯 Python 手动解析简单 YAML（仅一级键值对）
  - 容错设计：解析失败不抛异常，记录 warning 并跳过
  - 向后兼容：与内置 THEMES 字典格式完全一致

已知限制：
  - 仅支持外层一层引号剥离（不支持嵌套引号如 ``"'value'"``）
  - 值中若需含字面引号字符，请使用 YAML 单/双引号嵌套风格，
    或在引号内使用 ANSI 转义序列替代（如 ``\\033`` 代替 ESC）
"""

from __future__ import annotations

import glob
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .theme import Theme

logger = logging.getLogger(__name__)


def parse_simple_yaml(text: str) -> dict[str, str]:
    """解析简单 YAML 文本为扁平键值对字典。

    仅支持一级键值对（``key: value`` 或 ``key: "value"`` 格式），
    不支持嵌套结构、列表、多行值等高级 YAML 特性。

    支持的格式：
      - 注释行：以 ``#`` 开头（行首或缩进后）
      - 空行：自动跳过
      - 键值对：``key: value`` / ``key: "value"`` / ``key: 'value'``
      - 值可含 ANSI 转义序列（``\\033``、``\\x1b`` 等），
        通过 ``codecs.decode(..., 'unicode_escape')`` 解释

    Args:
        text: YAML 文本内容（UTF-8 字符串）。

    Returns:
        解析后的键值对字典。格式错误行跳过并记录 warning。
    """
    result: dict[str, str] = {}

    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()

        # 跳过空行和注释行
        if not stripped or stripped.startswith('#'):
            continue

        # 查找键值分隔符 ':'
        colon_idx = stripped.find(':')
        if colon_idx == -1:
            logger.warning(
                "YAML 解析：第 %d 行缺少冒号分隔符，跳过: %r",
                line_no, stripped,
            )
            continue

        key = stripped[:colon_idx].strip()
        raw_value = stripped[colon_idx + 1:].strip()

        # 键不能为空
        if not key:
            logger.warning(
                "YAML 解析：第 %d 行键为空，跳过: %r",
                line_no, stripped,
            )
            continue

        # 去除引号（双引号或单引号）
        if len(raw_value) >= 2:
            if (raw_value.startswith('"') and raw_value.endswith('"')) or \
               (raw_value.startswith("'") and raw_value.endswith("'")):
                raw_value = raw_value[1:-1]

        # 值不能为空
        if not raw_value:
            logger.warning(
                "YAML 解析：第 %d 行值为空，跳过: %r",
                line_no, stripped,
            )
            continue

        # 解释 ANSI 转义序列（仅 \033 / \x1b → ESC 字符）
        value = raw_value.replace("\\033", "\033").replace("\\x1b", "\x1b")

        result[key] = value

    return result


def load_theme_file(path: str) -> dict[str, str]:
    """从单个 YAML 文件加载主题。

    文件不存在或读取失败时静默返回空字典。

    Args:
        path: YAML 主题文件路径。

    Returns:
        主题键值对字典。失败时返回空字典。
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.warning("无法读取主题文件 %s: %s", path, e)
        return {}

    return parse_simple_yaml(text)


def load_user_themes_from_dir(
    theme_dir: str = "~/.deepseek/themes",
) -> dict[str, "Theme"]:
    """扫描用户主题目录，加载所有 ``*.yaml`` 主题文件。

    文件名（去扩展名）即为主题名称。
    目录不存在时静默返回空字典（不报错）。

    Args:
        theme_dir: 用户主题目录路径，默认 ``~/.deepseek/themes``。
                   ``~`` 自动展开为用户主目录。

    Returns:
        ``{主题名: Theme 实例}`` 的嵌套字典。
    """
    from .theme import Theme

    expanded_dir = os.path.expanduser(theme_dir)

    if not os.path.isdir(expanded_dir):
        return {}

    themes: dict[str, Theme] = {}
    yaml_files = sorted(glob.glob(os.path.join(expanded_dir, "*.yaml")))

    for yaml_path in yaml_files:
        theme_name = os.path.splitext(os.path.basename(yaml_path))[0]
        theme_data = load_theme_file(yaml_path)
        if theme_data:
            themes[theme_name] = Theme.from_dict(theme_name, theme_data)
            logger.info(
                "已加载用户主题: %s (%d 个语义键)",
                theme_name, len(theme_data),
            )
        else:
            logger.warning(
                "主题文件为空或解析失败，跳过: %s", yaml_path,
            )

    logger.info("用户主题加载完成: 共 %d 个", len(themes))
    return themes


__all__ = [
    "parse_simple_yaml",
    "load_theme_file",
    "load_user_themes_from_dir",
]
