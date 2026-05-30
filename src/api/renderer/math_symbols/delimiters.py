"""定界符与空格映射表。"""

from __future__ import annotations

from typing import Dict

# 定界符映射
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
    "/": "/", "\\backslash": "\\",
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
