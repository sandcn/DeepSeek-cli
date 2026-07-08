"""代码高亮行号解析与 Pygments 样式工具。"""

from __future__ import annotations

import threading
from typing import Type
from pygments.style import Style as PygmentsStyle
from pygments.styles import get_style_by_name


# 缓存已生成的样式类
_code_style_cache: dict[str, Type[PygmentsStyle]] = {}
_cache_lock = threading.Lock()


def parse_highlight_lines(attrs: str) -> list[int]:
    """从 attrs 中提取高亮行号，如 {.numberLines hl_lines='1,3-5'}

    字符级扫描，无正则表达式：在 attrs 中逐字符搜索 hl_lines="..."，
    提取引号内的值，解析逗号分隔的行号和范围。

    Args:
        attrs: 代码块属性字符串，如 '{.numberLines hl_lines="1,3-5"}'

    Returns:
        高亮行号列表，如 [1, 3, 4, 5]
    """
    if not attrs:
        return []
    # ★ 优化：用 str.find 替代逐字符 while 循环（C 级 memchr 实现）
    marker = 'hl_lines="'
    start = attrs.find(marker)
    if start == -1:
        marker = "hl_lines='"
        start = attrs.find(marker)
        if start == -1:
            return []
    i = start + len(marker)
    value_end = attrs.find('"', i)
    if value_end == -1:
        return []
    value = attrs[i:value_end]
    lines = []
    for part in value.split(','):
        part = part.strip()
        if '-' in part:
            try:
                a, b = part.split('-', 1)
                lines.extend(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                lines.append(int(part))
            except ValueError:
                pass
    return lines


def get_code_style(theme_name: str = "monokai") -> Type[PygmentsStyle]:
    """获取基于指定 Pygments 主题的样式类，去除所有文本样式和背景色。

    语法高亮仅保留前景色（color），确保代码块内容始终以原始代码形式呈现，
    不受 Pygments 主题中文本样式属性（如 **bold** → Token.Generic.Strong）的影响。
    **同时去除所有背景色定义**，确保代码块在任何终端主题下都不带底色。

    Args:
        theme_name: Pygments 主题名称，如 "monokai", "default", "native" 等。

    Returns:
        继承自原主题的 Pygments Style 子类，所有 token 样式仅保留前景色。
    """
    with _cache_lock:
        if theme_name in _code_style_cache:
            return _code_style_cache[theme_name]

    BaseStyle = get_style_by_name(theme_name)

    # 背景色前缀集合（用于过滤 token 级背景色）
    _BG_PREFIXES = ('bg:', 'bgcolor:', 'background:')

    # 遍历原主题的所有 token 样式，去除文本样式关键字和背景色定义
    cleaned_styles: dict = {}
    for ttype, style_str in BaseStyle.styles.items():
        parts = style_str.split()
        cleaned = [
            p for p in parts
            if p not in ('bold', 'italic', 'underline', 'strike')
            and not any(p.startswith(prefix) for prefix in _BG_PREFIXES)
        ]
        cleaned_styles[ttype] = ' '.join(cleaned)

    # 动态生成一个继承自原主题的新 Style 类
    new_style = type(
        f'{BaseStyle.__name__}_CodeBlock',
        (BaseStyle,),
        {'styles': cleaned_styles},
    )
    with _cache_lock:
        _code_style_cache[theme_name] = new_style
    return new_style
