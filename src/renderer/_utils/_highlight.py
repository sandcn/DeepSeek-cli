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
    """从代码块 info 属性中提取高亮行号。

    支持多种书写形式（字符级扫描，无正则表达式）：
      - ``{.numberLines hl_lines="1,3-5"}``（引号属性，逗号或空格分隔）
      - ``{hl_lines='1 3-5'}``（单引号）
      - ``{1,3-5}`` / ``{1 3-5}`` / ``{1,3,7-9}``（Pandoc / kramdown 大括号）
      - ``hl_lines="1,3-5"``（无大括号裸属性）

    Args:
        attrs: 代码块属性字符串。

    Returns:
        高亮行号列表（升序去重），如 ``[1, 3, 4, 5]``。
    """
    if not attrs:
        return []
    value = _extract_attr_value(attrs, 'hl_lines')
    if value is None:
        value = _extract_brace_line_spec(attrs)
    if value is None:
        return []
    return _parse_line_spec(value)


def parse_linenos(attrs: str) -> bool:
    """代码块是否要求显示行号（``{.numberLines}`` / ``{linenos}`` / ``{line-numbers}``）。

    识别 ``linenos`` / ``numberLines`` / ``line-numbers`` 令牌（大小写不敏感，
    kramdown / Pandoc / Highlight 三种书写法的并集）。返回布尔值供渲染层
    决定是否输出行号前缀。
    """
    if not attrs:
        return False
    low = attrs.lower()
    return (
        'numberlines' in low
        or 'linenos' in low
        or 'line-numbers' in low
        or 'line_numbers' in low
    )


#: 行号起始值属性名（按优先级探测；值形如 ``=3`` / ``="3"``）
_LINENO_START_KEYS: tuple[str, ...] = (
    'linenostart', 'line-start', 'linestart', 'startfrom', 'start-from',
    'first-line', 'first_line', 'firstline', 'start',
)
#: 行号步长属性名（按优先级探测）
_LINENO_STEP_KEYS: tuple[str, ...] = (
    'linenostep', 'line-step', 'linestep', 'step',
)


def _to_positive_int(value: str) -> int | None:
    """属性值 → 正整数（失败/非正数返回 ``None``）。"""
    if not value:
        return None
    text = value.strip().strip('"\'')
    try:
        n = int(text)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_lineno_options(attrs: str) -> tuple[bool, int, int]:
    """解析代码块行号选项：``(是否显示, 起始行号, 步长)``。

    在 ``parse_linenos`` 的开关语义之上，支持起始值/步长：

      - 起始：``linenostart=3`` / ``startFrom=3`` / ``first-line=3`` /
        ``start=3`` / ``linenos=3``（开关带起始值）
      - 步长：``linenostep=2`` / ``line-step=2`` / ``step=2``

    非法/缺失值回退 ``(enabled, 1, 1)``。``attrs`` 为空时为 ``(False, 1, 1)``。
    """
    if not attrs:
        return (False, 1, 1)
    low = attrs.lower()
    enabled = (
        'numberlines' in low
        or 'linenos' in low
        or 'line-numbers' in low
        or 'line_numbers' in low
    )
    start = 1
    step = 1
    for key in _LINENO_START_KEYS:
        value = _extract_attr_value(attrs, key)
        n = _to_positive_int(value) if value is not None else None
        if n is not None:
            start = n
            break
    for key in _LINENO_STEP_KEYS:
        value = _extract_attr_value(attrs, key)
        n = _to_positive_int(value) if value is not None else None
        if n is not None:
            step = n
            break
    if start != 1 or step != 1:
        enabled = True
    return (enabled, start, step)


def _extract_attr_value(attrs: str, name: str) -> str | None:
    """提取 ``name="value"`` / ``name='value'`` / ``name=value`` 的属性值。

    属性名前必须是独立边界（前一字符非字母数字/连字符），避免 ``data-hl_lines``
    之类的子串误匹配。找不到返回 ``None``。
    """
    low = attrs.lower()
    key = name.lower()
    idx = low.find(key + '=')
    while idx > 0:
        prev = attrs[idx - 1]
        if not (prev.isalnum() or prev in '-_'):
            break
        idx = low.find(key + '=', idx + 1)
    if idx < 0:
        return None
    i = idx + len(key) + 1
    if i >= len(attrs):
        return None
    quote = attrs[i]
    if quote in ('"', "'"):
        end = attrs.find(quote, i + 1)
        return attrs[i + 1:end] if end > i else None
    end = i
    n = len(attrs)
    while end < n and not attrs[end].isspace() and attrs[end] not in '}':
        end += 1
    return attrs[i:end]


def _extract_brace_line_spec(attrs: str) -> str | None:
    """提取 ``{1,3-5}`` / ``{1 3-5}`` 大括号内的行号规格。

    仅当大括号内容完全由数字 / ``-`` / ``,`` / ``;`` / 空格组成时返回
    （否则视为普通属性集合，如 ``{python .numberLines}``），避免把语言名或
    类名误当行号。
    """
    s = attrs.strip()
    if not s.startswith('{'):
        return None
    end = s.find('}')
    inner = s[1:end] if end >= 0 else s[1:]
    inner = inner.strip()
    if not inner:
        return None
    for ch in inner:
        if not (ch.isdigit() or ch in '- ,;'):
            return None
    return inner


def _parse_line_spec(spec: str) -> list[int]:
    """解析行号规格（``1,3-5`` / ``1 3-5`` / ``1;3-5``）为升序去重列表。"""
    out: list[int] = []
    seen: set[int] = set()
    for raw in spec.replace(';', ',').replace(',', ' ').split():
        part = raw.strip()
        if not part:
            continue
        if '-' in part[1:]:
            a, _, b = part.partition('-')
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)
    out.sort()
    return out


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
