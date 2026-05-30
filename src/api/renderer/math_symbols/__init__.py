"""数学符号表与样式常量子包。"""

from __future__ import annotations

# ── 样式常量 ──
from .styles import (
    _STYLE_DEFAULT, _STYLE_FUNCTION, _STYLE_NUMBER,
    _STYLE_OPERATOR, _STYLE_SUPERSCRIPT, _STYLE_SUBSCRIPT,
    _STYLE_FRAC_LINE, _STYLE_TEXT, _STYLE_BOLD, _STYLE_ITALIC,
    _STYLE_INLINE, _STYLE_BLOCK, _STYLE_CANCEL, _STYLE_TAG,
    _STYLE_BOXED, _STYLE_COLOR_NOTICE, _STYLE_ACCENT,
    _COLOR_ALIAS,
)

# ── 符号表 ──
from .greek import (
    _GREEK_LETTERS,
)
from .relations import (
    _RELATION_SYMBOLS,
)
from .operators import (
    _OPERATOR_SYMBOLS,
    _BIG_OPERATORS,
    _BIG_OPERATOR_COMMANDS,
)
from .arrows import (
    _ARROW_SYMBOLS,
    _LOGICAL_ARROWS,
)
from .functions import (
    _FUNCTION_NAMES,
    _LIMIT_FUNCTIONS,
)
from .misc import (
    _MISC_SYMBOLS,
    _ACCENT_MAP,
    _OPERATOR_CHARS,
    _SILENT_COMMANDS,
)
from .delimiters import (
    _DELIMITER_MAP,
    _SPACE_MAP,
)

# ── 上下标 ──
from .scripts import (
    _SUPERSCRIPT_MAP,
    _SUBSCRIPT_MAP,
)

# ── 合并命令映射 ──
from .builder import (
    _build_command_map,
    _COMMAND_MAP,
)


# ═══════════════════════════════════════════════════════════
# __all__ — 定义 from .math_symbols import * 的导入列表
# （所有名称均以下划线开头，不显式定义 __all__ 则不会被 * 导入）
# ═══════════════════════════════════════════════════════════

__all__ = [
    # 样式常量
    "_STYLE_DEFAULT", "_STYLE_FUNCTION", "_STYLE_NUMBER",
    "_STYLE_OPERATOR", "_STYLE_SUPERSCRIPT", "_STYLE_SUBSCRIPT",
    "_STYLE_FRAC_LINE", "_STYLE_TEXT", "_STYLE_BOLD", "_STYLE_ITALIC",
    "_STYLE_INLINE", "_STYLE_BLOCK", "_STYLE_CANCEL", "_STYLE_TAG",
    "_STYLE_BOXED", "_STYLE_COLOR_NOTICE", "_STYLE_ACCENT",
    "_COLOR_ALIAS",
    # 符号表
    "_GREEK_LETTERS", "_RELATION_SYMBOLS", "_OPERATOR_SYMBOLS",
    "_ARROW_SYMBOLS", "_BIG_OPERATORS", "_FUNCTION_NAMES",
    "_LIMIT_FUNCTIONS", "_LOGICAL_ARROWS", "_BIG_OPERATOR_COMMANDS",
    "_MISC_SYMBOLS", "_ACCENT_MAP", "_OPERATOR_CHARS",
    "_SILENT_COMMANDS", "_DELIMITER_MAP", "_SPACE_MAP",
    # 上下标
    "_SUPERSCRIPT_MAP", "_SUBSCRIPT_MAP",
    # 构建函数及合并映射
    "_build_command_map", "_COMMAND_MAP",
]
