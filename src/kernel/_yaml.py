"""极简 YAML 子集解析器 — 供插件清单（bundle/profile/patch）使用。

仅支持声明式清单所需的子集：嵌套映射、列表、``- key: value`` 列表项、
标量（字符串/布尔/整数/浮点/null）、行内列表 ``[a, b]``、注释（``#``）。
不支持锚点、多行字符串、复杂流式结构——清单不需要这些。

若环境中存在 PyYAML，可由 :mod:`src.kernel.manifest` 优先使用；本模块提供
无第三方依赖的兜底实现。
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple


class YamlError(ValueError):
    """YAML 子集解析错误。"""


_KEY_RE = re.compile(r"^(?P<key>[^:#]+?)\s*:(?P<rest>.*)$")


def load(text: str) -> Any:
    """解析 YAML 子集文本，返回 Python 对象。"""
    lines: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw:
            raise YamlError("YAML 子集不支持制表符缩进，请使用空格")
        stripped_comment = _strip_comment(raw)
        if not stripped_comment.strip():
            continue
        indent = len(stripped_comment) - len(stripped_comment.lstrip(" "))
        lines.append((indent, stripped_comment.strip()))
    if not lines:
        return {}
    pos = [0]
    value = _parse_block(lines, pos, lines[0][0])
    return value


def _strip_comment(line: str) -> str:
    result = []
    quote = None
    for index, ch in enumerate(line):
        if quote:
            result.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            result.append(ch)
            continue
        if ch == "#" and (index == 0 or line[index - 1] in " \t"):
            break
        result.append(ch)
    return "".join(result).rstrip()


def _parse_block(lines: List[Tuple[int, str]], pos: List[int], indent: int) -> Any:
    if pos[0] >= len(lines):
        return None
    current_indent, content = lines[pos[0]]
    if current_indent < indent:
        return None
    if content.startswith("- ") or content == "-":
        return _parse_list(lines, pos, current_indent)
    return _parse_map(lines, pos, current_indent)


def _parse_map(lines: List[Tuple[int, str]], pos: List[int], indent: int) -> dict:
    result: dict = {}
    while pos[0] < len(lines):
        current_indent, content = lines[pos[0]]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise YamlError(f"意外缩进: {content!r}")
        if content.startswith("- "):
            break
        match = _KEY_RE.match(content)
        if not match:
            raise YamlError(f"无法解析映射条目: {content!r}")
        key = match.group("key").strip()
        rest = match.group("rest").strip()
        pos[0] += 1
        if rest:
            result[key] = _scalar(rest)
        else:
            if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                result[key] = _parse_block(lines, pos, lines[pos[0]][0])
            else:
                result[key] = None
    return result


def _parse_list(lines: List[Tuple[int, str]], pos: List[int], indent: int) -> list:
    result: list = []
    while pos[0] < len(lines):
        current_indent, content = lines[pos[0]]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise YamlError(f"意外缩进: {content!r}")
        if not (content.startswith("- ") or content == "-"):
            break
        item_content = content[2:].strip() if content.startswith("- ") else ""
        pos[0] += 1
        if not item_content:
            if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                result.append(_parse_block(lines, pos, lines[pos[0]][0]))
            else:
                result.append(None)
            continue
        if _KEY_RE.match(item_content):
            item_indent = indent + 2
            lines.insert(pos[0], (item_indent, item_content))
            result.append(_parse_map(lines, pos, item_indent))
            continue
        result.append(_scalar(item_content))
    return result


def _scalar(text: str) -> Any:
    value = text.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in _split_inline(inner)]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        if not inner:
            return {}
        result = {}
        for part in _split_inline(inner):
            if ":" not in part:
                raise YamlError(f"行内映射条目缺少 ':': {part!r}")
            key, _, val = part.partition(":")
            result[key.strip().strip("'\"")] = _scalar(val)
        return result
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _split_inline(text: str) -> list:
    parts = []
    depth = 0
    quote = None
    current = []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part != ""]


__all__ = ["load", "YamlError"]
