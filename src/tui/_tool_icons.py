"""工具图标存根 — 替换已删除的 core/tool_icons.py。

2026-07-29 TUI 重构：原 core/tool_icons.py 依赖 theme/color 等已删除模块，
此处提供最小化 AGENT_TYPE_ABBREV 字典存根。
"""

from __future__ import annotations

AGENT_TYPE_ABBREV: dict[str, str] = {
    "map": "MAP",
    "plan": "PLN",
    "review": "REV",
    "execute": "EXE",
}

__all__ = ["AGENT_TYPE_ABBREV"]
