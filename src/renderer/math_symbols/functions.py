"""数学函数名称与极限函数集合。"""

from __future__ import annotations

from typing import Dict, Set

_FUNCTION_NAMES: Dict[str, str] = {
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot",
    "sec": "sec", "csc": "csc",
    "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctan",
    "arcsinh": "arcsinh", "arccosh": "arccosh", "arctanh": "arctanh",
    "arccoth": "arccoth", "sech": "sech", "csch": "csch",
    "arccot": "arccot", "arcsec": "arcsec", "arccsc": "arccsc",
    "sinh": "sinh", "cosh": "cosh", "tanh": "tanh", "coth": "coth",
    "log": "log", "ln": "ln", "lg": "lg", "exp": "exp",
    "lim": "lim", "sup": "sup", "inf": "inf",
    "max": "max", "min": "min", "sgn": "sgn",
    "det": "det", "dim": "dim", "ker": "ker", "deg": "deg",
    "arg": "arg", "hom": "hom", "gcd": "gcd",
    "liminf": "liminf", "limsup": "limsup",
    "Pr": "Pr",
    "mod": "mod", "bmod": "mod",
    # === 第三批补充（KaTeX 全量数学算子）===
    "arctg": "arctg", "arcctg": "arcctg",
    "cotg": "cotg", "ctg": "ctg", "tg": "tg",
    "sh": "sh", "ch": "ch", "th": "th", "cth": "cth",
    "cosec": "cosec",
    "argmax": "arg max", "argmin": "arg min",
    "plim": "plim",
    "injlim": "inj lim", "projlim": "proj lim",
    "varinjlim": "lim", "varprojlim": "lim",
    "varlimsup": "lim", "varliminf": "lim",
}

# 支持极限下标（\lim_{x \to 0}）的命令集合
_LIMIT_FUNCTIONS: Set[str] = {
    "lim", "liminf", "limsup",
    "sup", "inf", "max", "min",
    "det", "Pr", "gcd",
    "argmax", "argmin", "plim",
    "injlim", "projlim", "varinjlim", "varprojlim",
    "varlimsup", "varliminf",
}
