"""YAML frontmatter 解析 — 零依赖实现（PyYAML 可用时优先）

技能文件格式（与 DeepSeek Harness / Claude Skills 兼容）：

    ---
    name: my-skill
    description: 一句话描述
    whenToUse: 可选的路由提示
    disable-model-invocation: false   # 可选，模型面开关
    user-invocable: true              # 可选，用户面开关
    metadata:
      author: someone
    ---
    # 技能正文（Markdown）

本模块内置一个最小 YAML 子集解析器（无 PyYAML 依赖时使用），
覆盖技能 frontmatter 的常见形态：标量、引号字符串、布尔、
整数/浮点、行内列表、块列表与整行注释。无法解析的复杂 YAML
会被整体判定为无效 frontmatter（技能文件被跳过并告警），
不会抛出异常中断对话。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

_logger = logging.getLogger(__name__)

# 可选：PyYAML 存在时优先使用（更健壮），缺失时回退内置解析器
try:  # pragma: no cover - 环境相关分支
    import yaml as _yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover - 环境相关分支
    _yaml = None
    _HAS_YAML = False


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

def parse_frontmatter(raw: str) -> Optional[Tuple[dict, str]]:
    """解析 Markdown 文本的 YAML frontmatter。

    Args:
        raw: 文件全文。

    Returns:
        (data, body) — frontmatter 解析结果与正文（已去除 frontmatter）；
        无合法 frontmatter 时返回 None。
    """
    block = _extract_frontmatter_block(raw)
    if block is None:
        return None
    header, body = block
    if _HAS_YAML:  # pragma: no cover - 环境相关分支
        try:
            parsed = _yaml.safe_load(header)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed, body
        return None
    parsed = _parse_minimal(header)
    if parsed is None:
        return None
    return parsed, body


# ═══════════════════════════════════════════════════════════
# frontmatter 块提取
# ═══════════════════════════════════════════════════════════

def _extract_frontmatter_block(raw: str) -> Optional[Tuple[str, str]]:
    """提取首行 ``---`` 与下一个 ``---`` 之间的内容。

    Returns:
        (header, body) 或 None（无 frontmatter）。
    """
    first_line_end = raw.find("\n")
    if first_line_end < 0:
        return None
    if raw[:first_line_end].rstrip("\r") != "---":
        return None
    start = first_line_end + 1
    line_start = start
    while line_start <= len(raw):
        next_nl = raw.find("\n", line_start)
        line_end = len(raw) if next_nl < 0 else next_nl
        if raw[line_start:line_end].rstrip("\r") == "---":
            body_start = len(raw) if next_nl < 0 else next_nl + 1
            return raw[start:line_start], raw[body_start:]
        if next_nl < 0:
            return None
        line_start = next_nl + 1
    return None


# ═══════════════════════════════════════════════════════════
# 最小 YAML 子集解析器
# ═══════════════════════════════════════════════════════════

def _parse_minimal(header: str) -> Optional[dict]:
    """解析最小 YAML 子集，失败返回 None。"""
    data: dict = {}
    last_key: Optional[str] = None
    pending_list: Optional[list] = None

    for raw_line in header.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            # 块列表：挂到上一个键
            if last_key is None:
                return None
            if pending_list is None:
                pending_list = []
                data[last_key] = pending_list
            pending_list.append(_parse_scalar(stripped[2:].strip()))
            continue
        # 新的 key: value 行
        if ":" not in line:
            return None
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            return None
        # 去掉行尾注释（值内不带 # 的简单情况）
        rest = _strip_trailing_comment(rest)
        if rest == "" or rest in ("null", "~", "Null", "NULL"):
            value: Any = None
        elif rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if inner == "":
                value = []
            else:
                value = [_parse_scalar(item.strip()) for item in inner.split(",")]
        else:
            value = _parse_scalar(rest)
        data[key] = value
        last_key = key
        pending_list = None

    return data


def _strip_trailing_comment(value: str) -> str:
    """去除行尾注释。仅处理值中不含引号/井号内容的简单场景。"""
    if "#" not in value:
        return value
    # 引号内不剥离
    quote: Optional[str] = None
    for i, ch in enumerate(value):
        if quote is not None:
            if ch == quote and (i == 0 or value[i - 1] != "\\"):
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value


def _parse_scalar(text: str) -> Any:
    """解析单个标量值。"""
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        inner = text[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none"):
        return None
    # 数字
    try:
        if re_fullmatch_int(text):
            return int(text)
    except ValueError:
        pass
    try:
        if re_fullmatch_float(text):
            return float(text)
    except ValueError:
        pass
    return text


_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def re_fullmatch_int(text: str) -> bool:
    return bool(_INT_RE.match(text))


def re_fullmatch_float(text: str) -> bool:
    return bool(_FLOAT_RE.match(text))


__all__ = ["parse_frontmatter"]
