"""否定关系符号合成表（``\\not`` 后接关系符）。

LaTeX 的 ``\\not`` 用于对紧随其后的一元关系符取反（``\\not=`` → ``≠``）。
终端渲染时若能直接给出预组合的否定字符，比「``¬`` + 原字符」或「原字符 +
组合长斜线 U+0338」更清晰、字宽更稳定。

本模块只提供**纯数据**映射（无 Rich 依赖），ANSI 路径与 Rich 路径共用：
  - ``_NEGATED_SYMBOLS``：正向关系符 → 预组合否定字符；
  - ``_COMBINING_LONG_SOLIDUS``：无预组合字符时的组合长斜线（U+0338）。
"""

from __future__ import annotations

from typing import Dict

#: 关系符 → 预组合否定字符（Unicode 中存在对应「斜线否定」码点）
_NEGATED_SYMBOLS: Dict[str, str] = {
    "=": "≠", "<": "≮", ">": "≯",
    "∈": "∉", "∋": "∌",
    "⊂": "⊄", "⊃": "⊅", "⊆": "⊈", "⊇": "⊉",
    "≤": "≰", "≥": "≱", "≦": "≰", "≧": "≱", "⩽": "⪇", "⩾": "⪈",
    "≈": "≉", "≡": "≢", "∼": "≁", "≅": "≇", "≃": "≄", "≂": "≂",
    "≍": "≭", "≊": "≉", "∽": "≁",
    "∣": "∤", "∥": "∦", "⊢": "⊬", "⊨": "⊭", "⊩": "⊮", "⊪": "⊯",
    "→": "↛", "←": "↚", "↔": "↮", "⇒": "⇏", "⇐": "⇍", "⇔": "⇎",
    "∃": "∄", "≺": "⊀", "≻": "⊁", "≼": "⋠", "≽": "⋡",
    "≪": "≮", "≫": "≯", "≲": "≴", "≳": "≵",
    "⊑": "⋢", "⊒": "⋣", "◁": "⋪", "▷": "⋫",
    "⊴": "⋬", "⊵": "⋭",
    "≄": "≄",
}

#: 组合长斜线（U+0338）：无预组合字符时叠加到原关系符上（如 ``∅`` → ``∅̸``）
_COMBINING_LONG_SOLIDUS = "\u0338"


def negate_symbol(symbol: str) -> str:
    """关系符 → 否定形式（优先预组合字符，其次叠加组合长斜线）。

    Args:
        symbol: 关系符（如 ``=`` / ``∈``）。

    Returns:
        否定后的字符串；``symbol`` 为空时返回空串。
    """
    if not symbol:
        return ""
    mapped = _NEGATED_SYMBOLS.get(symbol)
    if mapped is not None:
        return mapped
    return symbol + _COMBINING_LONG_SOLIDUS


__all__ = ["_NEGATED_SYMBOLS", "_COMBINING_LONG_SOLIDUS", "negate_symbol"]
