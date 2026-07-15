"""ui — 显示层（向后兼容层，已迁移至 tui/）

本模块已逐步废弃，所有功能已迁移至 src/tui/。

已迁移：
  - diff_renderer     → src/tui/consumer/diff_renderer
  - ansi              → src/tui/core/ansi_utils
  - colors            → src/tui/core/gradient + palettes + theme
  - theme             → src/tui/core/theme
  - output_target     → src/tui/core/output_target
  - terminal_adapter  → src/tui/terminal/adapter
  - base_display      → src/tui/consumer/base_display
  - components/cost_display → src/tui/core/cost + tui/components/_cost
  - formatters/param_formatter → src/tui/core/param_formatter

保留此模块为向后兼容存根。
新代码应直接导入 src.tui.*。
"""

from __future__ import annotations
