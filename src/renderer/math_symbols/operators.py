"""运算符与大运算符符号映射表。"""

from __future__ import annotations

from typing import Dict, Set

_OPERATOR_SYMBOLS: Dict[str, str] = {
    "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "cdot": "·", "circ": "∘", "ast": "∗", "star": "⋆",
    "otimes": "⊗", "oplus": "⊕", "odot": "⊙",
    "ominus": "⊖", "oslash": "⊘",
    "wedge": "∧", "vee": "∨", "land": "∧", "lor": "∨",
    "cap": "∩", "cup": "∪",
    "setminus": "∖", "triangle": "△",
    "boxplus": "⊞", "boxminus": "⊟",
    "boxtimes": "⊠", "boxdot": "⊡",
    "wr": "≀", "amalg": "⨿",
    "intercal": "⊺", "circeq": "≗",
    "smallsetminus": "∖",
    "ltimes": "⋉", "rtimes": "⋊",
    "leftthreetimes": "⋋", "rightthreetimes": "⋌",
    "curlywedge": "⋏", "curlyvee": "⋎",
    "centerdot": "·", "cdotp": "·",
    "barwedge": "⊼", "veebar": "⊻",
    "doublebarwedge": "⩞",
    "colon": ":",             # 冒号（用于函数定义 f: X → Y）
    "ldotp": ".",             # 点号（低点）
    "dotplus": "∔",           # 点加
    "divideontimes": "⋇",     # 乘除号
    "smalltriangleright": "▸", # 小三角右
    "smalltriangleleft": "◂", # 小三角左
    "Cap": "⋒",               # 双交
    "Cup": "⋓",               # 双并
    "barwedge": "⊼",          # 楔形带横
    "veebar": "⊻",            # 异或
    "boxdot": "⊡",            # 盒点
    "boxminus": "⊟",          # 盒减
    "boxplus": "⊞",           # 盒加
    "boxtimes": "⊠",          # 盒乘
    "uplus": "⊎",             # 并集加
    "sqcap": "⊓",             # 方帽
    "sqcup": "⊔",             # 方杯
    "sqdoublecap": "⩎",       # 双线方帽
    "sqdoublecup": "⩏",       # 双线方杯
    # === 第二批补充（常用二元运算符）===
    "dagger": "†",
    "ddagger": "‡",
    "bullet": "•",
    "diamond": "⋄",
    "bigcirc": "○",
    "circledast": "⊛",
    "circledcirc": "⊚",
    "circleddash": "⊝",
    "bigtriangleup": "△",
    "bigtriangledown": "▽",
    "triangleleft": "◁",
    "triangleright": "▷",
    "unlhd": "⊴",
    "unrhd": "⊵",
    "lhd": "⊲",
    "rhd": "⊳",
    # === 第三批补充（KaTeX 全量二元运算符别名与缺项）===
    "And": "&",
    "plusmn": "±",
    "sdot": "⋅",
    "bull": "•",
    "minuso": "∸",
    "bigominus": "⨸",
    "bigoslash": "⦸",
    "dblcolon": "∷",
    "coloncolon": "∷",
    "smallint": "∫",
}

_BIG_OPERATORS: Dict[str, str] = {
    "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮",
    "iint": "∬", "iiint": "∭", "iiiint": "⨌",
    "oiint": "∯", "oiiint": "∰",
    "idotsint": "∫⋯∫",
    "intop": "∫",
    "coprod": "∐",
    "bigcup": "⋃", "bigcap": "⋂",
    "bigvee": "⋁", "bigwedge": "⋀",
    "bigoplus": "⨁", "bigotimes": "⨂",
    "bigsqcup": "⨆", "biguplus": "⨄",
    "bigsqcap": "⨅", "bigtriangleup": "△", "bigtriangledown": "▽",
    "bigtimes": "⨉",
    "bigast": "✱",
    "bigodot": "⊙",
    "bigcirc": "○",
    "bigominus": "⨸",
    "bigoslash": "⦸",
}

_BIG_OPERATOR_COMMANDS: Set[str] = {
    "sum", "prod", "coprod",
    "int", "oint", "iint", "iiint", "oiint", "oiiint", "iiiint",
    "idotsint", "intop",
    "bigcup", "bigcap", "bigvee", "bigwedge",
    "bigoplus", "bigotimes", "bigsqcup", "biguplus",
    "bigsqcap", "bigtriangleup", "bigtriangledown",
    "bigominus", "bigoslash",
    # ★ 修复（review 方向）：四个大运算符缺失（_BIG_OPERATORS 中存在）——
    #   修复前 \bigtimes/\bigast/\bigodot/\bigcirc 不走大运算符极限路径，
    #   _{...}^{...} 落入通用上下标处理。
    "bigtimes", "bigast", "bigodot", "bigcirc",
}
