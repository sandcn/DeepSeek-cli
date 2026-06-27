"""chat_ui.bottom_bar — 底部栏纯计算模块。

本包从 src.ui 迁移而来，包含：
  - _bridge.py：BottomBarBridge — DECSTBM 管理层（替换旧 _BottomBar）
  - _cursor.py：光标视觉位置计算（制表符展开/拆行/ANSI宽度）
  - _theme.py：底部栏视觉主题常量与颜色辅助函数
  - _scroll_region.py：滚动区域管理和 Blessed 终端控制
  - _stdout_tracker.py：stdout 行追踪器
  - status_bar.py：状态行渲染
"""

from __future__ import annotations

from ._bridge import BottomBarBridge
from ._scroll_region import ScrollRegionManager
from ._theme import _SUBAGENT_TYPE_ABBR

__all__ = [
    "BottomBarBridge",
    "ScrollRegionManager",
    "_SUBAGENT_TYPE_ABBR",
]
