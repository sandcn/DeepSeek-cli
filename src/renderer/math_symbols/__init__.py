"""数学符号表子包（纯数据，无第三方渲染依赖）。

ANSI 渲染路径（``src.renderer.ansi``）与其它消费方直接导入各纯数据符号表
（greek / relations / operators / arrows / functions / misc / delimiters /
scripts / negations）。本 ``__init__`` 仅做统一 re-export，导入本包不会引入
任何第三方渲染库。
"""

from __future__ import annotations

from .arrows import _ARROW_SYMBOLS, _LOGICAL_ARROWS
from .delimiters import _DELIMITER_MAP, _SPACE_MAP
from .functions import _FUNCTION_NAMES, _LIMIT_FUNCTIONS
from .greek import _GREEK_LETTERS
from .misc import (
    _ACCENT_MAP,
    _MISC_SYMBOLS,
    _OPERATOR_CHARS,
    _SILENT_COMMANDS,
)
from .negations import _NEGATED_SYMBOLS, negate_symbol
from .operators import (
    _BIG_OPERATOR_COMMANDS,
    _BIG_OPERATORS,
    _OPERATOR_SYMBOLS,
)
from .relations import _RELATION_SYMBOLS
from .scripts import _SUBSCRIPT_MAP, _SUPERSCRIPT_MAP

__all__ = [
    # 符号表
    "_GREEK_LETTERS", "_RELATION_SYMBOLS", "_OPERATOR_SYMBOLS",
    "_ARROW_SYMBOLS", "_BIG_OPERATORS", "_FUNCTION_NAMES",
    "_LIMIT_FUNCTIONS", "_LOGICAL_ARROWS", "_BIG_OPERATOR_COMMANDS",
    "_MISC_SYMBOLS", "_ACCENT_MAP", "_OPERATOR_CHARS",
    "_SILENT_COMMANDS", "_DELIMITER_MAP", "_SPACE_MAP",
    # 上下标
    "_SUPERSCRIPT_MAP", "_SUBSCRIPT_MAP",
    # 否定关系合成
    "_NEGATED_SYMBOLS", "negate_symbol",
]
