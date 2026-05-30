"""合并符号表（构建命令→(字符, 样式) 快速查找表）。"""

from __future__ import annotations

from typing import Dict, Tuple
from rich.style import Style

from .styles import (
    _STYLE_DEFAULT,
    _STYLE_FUNCTION,
    _STYLE_OPERATOR,
)
from .greek import _GREEK_LETTERS
from .relations import _RELATION_SYMBOLS
from .operators import _OPERATOR_SYMBOLS, _BIG_OPERATORS
from .arrows import _ARROW_SYMBOLS
from .functions import _FUNCTION_NAMES
from .misc import _MISC_SYMBOLS
from .delimiters import _SPACE_MAP


def _build_command_map() -> Dict[str, Tuple[str, Style]]:
    """构建所有命令到 (显示字符, 样式) 的映射。"""
    m: Dict[str, Tuple[str, Style]] = {}
    for d in (_GREEK_LETTERS, _RELATION_SYMBOLS, _ARROW_SYMBOLS, _MISC_SYMBOLS):
        for cmd, char in d.items():
            m[cmd] = (char, _STYLE_DEFAULT)
    for cmd, char in _OPERATOR_SYMBOLS.items():
        m[cmd] = (char, _STYLE_OPERATOR)
    for cmd, char in _BIG_OPERATORS.items():
        m[cmd] = (char, _STYLE_OPERATOR)
    for cmd, name in _FUNCTION_NAMES.items():
        m[cmd] = (name, _STYLE_FUNCTION)
    # 空格命令也加入映射
    for cmd, space in _SPACE_MAP.items():
        if cmd not in m:
            m[cmd] = (space, _STYLE_DEFAULT)
    return m


_COMMAND_MAP = _build_command_map()
