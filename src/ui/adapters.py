"""UI 适配器（已废弃） — 所有功能已迁移到 src.tui 包。

请使用以下替代路径：
  - src.tui.events.adapters
  - src.tui.widgets.lock
  - src.tui.terminal.adapter

详细迁移指南见 src/tui/__init__.py 注释。
"""

from __future__ import annotations

raise ImportError(
    "src.ui 已废弃，所有功能已迁移到 src.tui 包。"
    "请使用 src.tui.events.adapters / src.tui.widgets.lock / src.tui.terminal.adapter 替代。"
    "详细迁移指南见 src/tui/__init__.py 注释。"
)
