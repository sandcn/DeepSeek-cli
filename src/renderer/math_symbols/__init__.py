"""数学符号表与样式常量子包。

★ 启动性能（PEP 562 惰性导出）：本包原先在导入期 eager 导入 ``styles``（依赖
``rich.style``）与全部符号表。ANSI 渲染路径（``src.renderer.ansi``，设计上零
Rich 依赖）需要复用其中的**纯数据**符号表（greek / relations / operators /
arrows / functions / misc / delimiters / scripts），若 eager 加载会让 ANSI 路径
被迫引入整条 Rich 链。

现改为模块级 ``__getattr__`` 惰性解析：访问符号表（纯数据模块）不触碰 Rich；
只有真正访问 ``_STYLE_*`` / ``_COMMAND_MAP``（Rich Style 承载）的 Rich 渲染
路径才付出该代价。``from .math_symbols import X`` 的既有调用面保持不变。
"""

from __future__ import annotations

from importlib import import_module

#: 惰性导出表：公开名 → (包内子模块名, 模块内属性名)。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # ── 样式常量（styles.py —— 依赖 rich.style） ──
    "_STYLE_DEFAULT": (".styles", "_STYLE_DEFAULT"),
    "_STYLE_FUNCTION": (".styles", "_STYLE_FUNCTION"),
    "_STYLE_NUMBER": (".styles", "_STYLE_NUMBER"),
    "_STYLE_OPERATOR": (".styles", "_STYLE_OPERATOR"),
    "_STYLE_SUPERSCRIPT": (".styles", "_STYLE_SUPERSCRIPT"),
    "_STYLE_SUBSCRIPT": (".styles", "_STYLE_SUBSCRIPT"),
    "_STYLE_FRAC_LINE": (".styles", "_STYLE_FRAC_LINE"),
    "_STYLE_TEXT": (".styles", "_STYLE_TEXT"),
    "_STYLE_BOLD": (".styles", "_STYLE_BOLD"),
    "_STYLE_ITALIC": (".styles", "_STYLE_ITALIC"),
    "_STYLE_INLINE": (".styles", "_STYLE_INLINE"),
    "_STYLE_BLOCK": (".styles", "_STYLE_BLOCK"),
    "_STYLE_CANCEL": (".styles", "_STYLE_CANCEL"),
    "_STYLE_TAG": (".styles", "_STYLE_TAG"),
    "_STYLE_BOXED": (".styles", "_STYLE_BOXED"),
    "_STYLE_COLOR_NOTICE": (".styles", "_STYLE_COLOR_NOTICE"),
    "_STYLE_ACCENT": (".styles", "_STYLE_ACCENT"),
    "_COLOR_ALIAS": (".styles", "_COLOR_ALIAS"),
    # ── 纯数据符号表 ──
    "_GREEK_LETTERS": (".greek", "_GREEK_LETTERS"),
    "_RELATION_SYMBOLS": (".relations", "_RELATION_SYMBOLS"),
    "_OPERATOR_SYMBOLS": (".operators", "_OPERATOR_SYMBOLS"),
    "_BIG_OPERATORS": (".operators", "_BIG_OPERATORS"),
    "_BIG_OPERATOR_COMMANDS": (".operators", "_BIG_OPERATOR_COMMANDS"),
    "_ARROW_SYMBOLS": (".arrows", "_ARROW_SYMBOLS"),
    "_LOGICAL_ARROWS": (".arrows", "_LOGICAL_ARROWS"),
    "_FUNCTION_NAMES": (".functions", "_FUNCTION_NAMES"),
    "_LIMIT_FUNCTIONS": (".functions", "_LIMIT_FUNCTIONS"),
    "_MISC_SYMBOLS": (".misc", "_MISC_SYMBOLS"),
    "_ACCENT_MAP": (".misc", "_ACCENT_MAP"),
    "_OPERATOR_CHARS": (".misc", "_OPERATOR_CHARS"),
    "_SILENT_COMMANDS": (".misc", "_SILENT_COMMANDS"),
    "_DELIMITER_MAP": (".delimiters", "_DELIMITER_MAP"),
    "_SPACE_MAP": (".delimiters", "_SPACE_MAP"),
    "_SUPERSCRIPT_MAP": (".scripts", "_SUPERSCRIPT_MAP"),
    "_SUBSCRIPT_MAP": (".scripts", "_SUBSCRIPT_MAP"),
    # ── 合并命令映射（builder.py —— 依赖 styles） ──
    "_build_command_map": (".builder", "_build_command_map"),
    "_COMMAND_MAP": (".builder", "_COMMAND_MAP"),
}

#: 与惰性导出面一致的公开名清单（供 ``from .math_symbols import *`` 使用）。
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


def __getattr__(name: str):
    """PEP 562 模块级惰性属性解析（首次访问后缓存到模块命名空间）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
