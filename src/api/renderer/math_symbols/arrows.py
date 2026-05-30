"""箭头符号与逻辑箭头映射表。"""

from __future__ import annotations

from typing import Dict

_ARROW_SYMBOLS: Dict[str, str] = {
    "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐",
    "longrightarrow": "⟶", "longleftarrow": "⟵",
    "Longrightarrow": "⟹", "Longleftarrow": "⟸",
    "mapsto": "↦", "longmapsto": "⟼",
    "uparrow": "↑", "downarrow": "↓",
    "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "nearrow": "↗", "searrow": "↘",
    "swarrow": "↙", "nwarrow": "↖",
    "implies": "⟹", "impliedby": "⟸", "iff": "⟺",
    # 附加箭头
    "hookrightarrow": "↪", "hookleftarrow": "↩",
    "twoheadrightarrow": "↠", "twoheadleftarrow": "↞",
    "rightharpoonup": "⇀", "rightharpoondown": "⇁",
    "leftharpoonup": "↼", "leftharpoondown": "↽",
    "upharpoonright": "↾", "upharpoonleft": "↿",
    "downharpoonright": "⇂", "downharpoonleft": "⇃",
    "rightleftharpoons": "⇌", "leftrightharpoons": "⇋",
    "rightrightarrows": "⇉", "leftleftarrows": "⇇",
    "rightleftarrows": "⇄",
    "updownarrow": "↕", "Updownarrow": "⇕",
    "Lsh": "↰", "Rsh": "↱",
    "circlearrowright": "↻", "circlearrowleft": "↺",
    "dashleftarrow": "⇠", "dashrightarrow": "⇢",
    "rightsquigarrow": "⇝",
    "nRightarrow": "⇏",
    "nLeftarrow": "⇍",
    "nLeftrightarrow": "⇎",
    "nleftarrow": "↚",
    "nrightarrow": "↛",
    "nleftrightarrow": "↮",
    "leftarrowtail": "↢",
    "rightarrowtail": "↣",
    "curvearrowleft": "↶",
    "curvearrowright": "↷",
    "leftrightsquigarrow": "↭",
}

# 逻辑箭头（渲染时自动加空格）
_LOGICAL_ARROWS: Dict[str, str] = {
    "implies": "  ⟹  ",
    "impliedby": "  ⟸  ",
    "iff": "  ⟺  ",
    "Rightarrow": "  ⇒  ",
    "Leftarrow": "  ⇐  ",
    "Leftrightarrow": "  ⇔  ",
}
