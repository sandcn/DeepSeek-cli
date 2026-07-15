"""ANSI 工具（已废弃） — 所有功能已迁移到 src.tui.core.ansi_utils。

请使用以下替代路径：
  - src.tui.core.ansi_utils
  - src.tui.core.text_utils
  - src.tui.core.style.Style

详细迁移指南见 src/tui/__init__.py 注释。
"""

from __future__ import annotations

raise ImportError(
    "src.ui 已废弃，所有功能已迁移到 src.tui 包。"
    "请使用 src.tui.core.ansi_utils / src.tui.core.text_utils / src.tui.core.style 替代。"
    "详细迁移指南见 src/tui/__init__.py 注释。"
)
