"""底部栏包 — 拆分子组件目录。

原始单文件 ``_bottom_bar.py`` 已拆分为以下子模块：
  - ``_monitor.py`` — _SystemMonitor 跨平台系统监控
  - ``_popup.py`` — _CompletionPopup 补全弹窗
  - ``_status.py`` — 阶段显示映射 + 状态文本构建
  - ``_layout.py`` — 布局计算/输入行绘制/工具函数
  - ``_render.py`` — DECSTBM 滚动区域管理/生命周期/force_redraw
  - ``_bar.py`` — _BottomBar 终端底部固定输入栏（委托各子模块）

向后兼容：所有旧 ``from src.tui._bottom_bar import XXX`` 导入路径保持有效。
"""

from __future__ import annotations

from src.tui._bottom_bar._bar import _BottomBar
from src.tui._bottom_bar._popup import _CompletionPopup
from src.tui._bottom_bar._monitor import _SystemMonitor
from src.tui._bottom_bar._status import _PHASE_DISPLAY, _build_status_text
from src.tui._bottom_bar._layout import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _MIN_INPUT_ROWS,
    _TAB_WIDTH,
    _PLACEHOLDER_TEXT,
    _PLACEHOLDER_COMPACT,
    _PLACEHOLDER_STREAMING,
    _is_narrow,
    _visual_width,
    _truncate_by_width,
    _ansi_truncate,
    _build_glow_ansi,
    _compute_input_rows,
    _compute_bottom_lines_for,
    _draw_input_lines,
)
from src.tui._bottom_bar._render import (
    _build_gradient,
    _do_sync_bottom_lines,
    _do_ensure_cursor_in_upper,
    _do_ensure_cursor_in_lower,
    _do_register_sigwinch,
    _do_setup,
    _do_teardown,
    _do_force_redraw,
)

__all__ = [
    # 类
    "_BottomBar",
    "_CompletionPopup",
    "_SystemMonitor",
    # 状态/文本
    "_PHASE_DISPLAY",
    "_build_status_text",
    # 布局常量
    "_BOTTOM_MIN_HEIGHT",
    "_BOTTOM_MIN_LINES",
    "_MIN_INPUT_ROWS",
    "_TAB_WIDTH",
    "_PLACEHOLDER_TEXT",
    "_PLACEHOLDER_COMPACT",
    "_PLACEHOLDER_STREAMING",
    # 工具函数
    "_is_narrow",
    "_visual_width",
    "_truncate_by_width",
    "_ansi_truncate",
    "_build_glow_ansi",
    "_build_gradient",
    # 布局/渲染函数
    "_compute_input_rows",
    "_compute_bottom_lines_for",
    "_draw_input_lines",
    "_do_sync_bottom_lines",
    "_do_ensure_cursor_in_upper",
    "_do_ensure_cursor_in_lower",
    "_do_register_sigwinch",
    "_do_setup",
    "_do_teardown",
    "_do_force_redraw",
]
