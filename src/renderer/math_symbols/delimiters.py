"""定界符与空格映射表。"""

from __future__ import annotations

from typing import Dict

# 定界符映射
# ★ 修复（review 方向）：backslash 条目 key 改为无前缀 "backslash"——
#   解析器提取的 delim_cmd 为纯字母（"backslash"），原 "\\backslash" key
#   永远匹配不到（\left\backslash 渲染为字面 "backslash"）。
_DELIMITER_MAP: Dict[str, str] = {
    "(": "(", ")": ")",
    "[": "[", "]": "]",
    "{": "{", "}": "}",
    "|": "|", "\\|": "‖",
    ".": "",
    "langle": "⟨", "rangle": "⟩",
    "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉",
    "lvert": "|", "rvert": "|",
    "lVert": "‖", "rVert": "‖",
    "/": "/", "backslash": "\\",
    "uparrow": "↑", "downarrow": "↓",
    "Uparrow": "⇑", "Downarrow": "⇓",
    "updownarrow": "↕", "Updownarrow": "⇕",
    "lgroup": "⟮", "rgroup": "⟯",
    "lmoustache": "⎰", "rmoustache": "⎱",
    "ulcorner": "⌜", "urcorner": "⌝",
    "llcorner": "⌞", "lrcorner": "⌟",
}

# 空格映射
_SPACE_MAP: Dict[str, str] = {
    "quad": "    ",
    "qquad": "        ",
    ",": " ",
    ":": "  ",
    ";": "   ",
    "!": "",
    "enspace": "  ",
    "thinspace": " ",
    "negthinspace": "",
    "medspace": "  ",
    "negmedspace": "",
    "thickspace": "   ",
    "negthickspace": "",
}
