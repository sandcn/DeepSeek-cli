"""
语义化主题颜色映射 — 支持多主题切换。

每个主题预设为 {语义键: ANSI颜色码} 映射。
通过 set_theme(name) 动态切换，THEME 始终保持为当前活动主题的引用。

所有颜色值已升级为 256 色 ANSI 码（格式 \033[38;5;Nm 或 \033[48;5;Nm），
保持与 src/core/constants.py 中 _256 后缀常量一致。

从 src/ui/theme.py 提取 — TUI 核心主题系统。
"""
from __future__ import annotations

import glob
import logging
import os
from typing import Dict, List

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
        # 使用精准替换而非 codecs.decode('unicode_escape')，
        # 避免意外解码 \n \t \xNN 等控制字符。
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


# ══════════════════════════════════════════════════════════
# 内置主题加载（从 themes/ YAML 文件）
# ══════════════════════════════════════════════════════════

_BUILTIN_THEMES_DIR = os.path.join(os.path.dirname(__file__), "themes")


def _load_builtin_themes() -> Dict[str, Dict[str, str]]:
    """从 themes/ 目录加载内置主题 YAML 文件。"""
    themes: Dict[str, Dict[str, str]] = {}
    theme_dir = _BUILTIN_THEMES_DIR
    if not os.path.isdir(theme_dir):
        return themes
    for fname in sorted(os.listdir(theme_dir)):
        if fname.endswith(".yaml"):
            name = fname[:-5]
            data = load_theme_file(os.path.join(theme_dir, fname))
            if data:
                themes[name] = data
    return themes


THEMES: Dict[str, Dict[str, str]] = _load_builtin_themes()
_BUILTIN_THEMES: Dict[str, Dict[str, str]] = {k: dict(v) for k, v in THEMES.items()}


# ══════════════════════════════════════════════════════════
# 当前活动主题
# ══════════════════════════════════════════════════════════

_ACTIVE_NAME: str = "dark"
THEME: Dict[str, str] = dict(THEMES["dark"])  # 可变的当前主题副本


def set_theme(name: str) -> None:
    """切换到指定主题。

    Args:
        name: 主题名称（"dark" / "light" / "high-contrast" / "nord" / "catppuccin" / "solarized-dark" / "monokai"）

    Raises:
        ValueError: 主题名称不存在（可用: dark, light, high-contrast, nord, catppuccin, solarized-dark, monokai）
    """
    if name not in THEMES:
        raise ValueError(f"未知主题: {name}，可用主题: {', '.join(THEMES.keys())}")
    global _ACTIVE_NAME, THEME
    _ACTIVE_NAME = name
    THEME.clear()
    THEME.update(THEMES[name])


def get_active_theme() -> str:
    """返回当前主题名称。"""
    return _ACTIVE_NAME


def list_themes() -> List[str]:
    """返回所有可用主题名称列表。"""
    return list(THEMES.keys())


def get_theme_names_with_desc() -> List[tuple[str, str]]:
    """返回 (主题名, 简短描述) 列表。"""
    return [
        ("dark", "深色主题（默认）"),
        ("light", "亮色主题（浅色背景用）"),
        ("high-contrast", "高对比主题（高可读性）"),
        ("nord", "Nord 极光主题（蓝灰冰霜风）"),
        ("catppuccin", "Catppuccin Mocha 主题（暖暗紫柔和风）"),
        ("solarized-dark", "Solarized Dark 主题（深褐温和对比风）"),
        ("monokai", "Monokai 主题（高饱和荧光风）"),
    ]


def load_user_themes() -> None:
    """从 ``~/.deepseek/themes/*.yaml`` 加载用户自定义主题。

    加载后合并到 ``THEMES`` 字典中。
    用户主题与内置主题同名时，用户主题覆盖内置主题。
    目录不存在时静默跳过（不报错）。

    若当前活动主题被用户主题覆盖，``THEME`` 副本自动更新。
    """
    user_themes = load_user_themes_from_dir()
    if user_themes:
        THEMES.update(user_themes)
        # 若当前活动主题被用户主题覆盖，刷新 THEME 副本
        if _ACTIVE_NAME in user_themes:
            THEME.clear()
            THEME.update(THEMES[_ACTIVE_NAME])


def reload_themes() -> None:
    """重置为基础内置主题并重新加载用户主题。

    适用场景：用户在运行时修改了 ``~/.deepseek/themes/``
    下的 YAML 文件，调用此函数刷新主题列表。

    重置后若当前活动主题不存在（如已被删除的用户主题），
    自动回退为 ``"dark"``。
    """
    global THEMES, _ACTIVE_NAME
    THEMES.clear()
    THEMES.update(_BUILTIN_THEMES)
    load_user_themes()
    # 确保当前活动主题在重置后仍有效
    if _ACTIVE_NAME not in THEMES:
        _ACTIVE_NAME = "dark"
    THEME.clear()
    THEME.update(THEMES[_ACTIVE_NAME])


def load_user_themes_from_dir(
    theme_dir: str = "~/.deepseek/themes",
) -> dict[str, dict[str, str]]:
    """扫描用户主题目录，加载所有 ``*.yaml`` 主题文件。

    文件名（去扩展名）即为主题名称。
    目录不存在时静默返回空字典（不报错）。

    Args:
        theme_dir: 用户主题目录路径，默认 ``~/.deepseek/themes``。
                   ``~`` 自动展开为用户主目录。

    Returns:
        ``{主题名: {语义键: ANSI颜色码}}`` 的嵌套字典。
    """
    expanded_dir = os.path.expanduser(theme_dir)

    if not os.path.isdir(expanded_dir):
        return {}

    themes: dict[str, dict[str, str]] = {}
    yaml_files = sorted(glob.glob(os.path.join(expanded_dir, "*.yaml")))

    for yaml_path in yaml_files:
        theme_name = os.path.splitext(os.path.basename(yaml_path))[0]
        theme_data = load_theme_file(yaml_path)
        if theme_data:
            themes[theme_name] = theme_data
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


# ── 模块加载时自动加载用户主题 ──
load_user_themes()


__all__ = [
    "THEME", "THEMES",
    "set_theme", "get_active_theme", "list_themes",
    "get_theme_names_with_desc",
    "load_user_themes", "load_user_themes_into_themes", "reload_themes",
    "parse_simple_yaml", "load_theme_file", "load_user_themes_from_dir",
]
