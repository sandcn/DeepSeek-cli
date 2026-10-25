"""ANSI 转义序列门面 — 真源在 ``src/tui/_ansi_sequences.py``。

框架内部统一 ``from .ansi import cursor_up, clear_line, ...``；应用侧经
``src/tui/_screen.py`` re-export 同一批符号。真源放在 ``src/tui`` 包内而非本包，
是为了避免「``_screen`` → ``ink.ansi`` → ``ink.__init__`` → ``_render_api`` →
``_screen``」的包初始化期循环导入（见真源模块 docstring）。
"""

from __future__ import annotations

from .._ansi_sequences import (  # noqa: F401  （显式重导出，见 __all__）
    ANSI_EMERGENCY_CURSOR_BOTTOM,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
    ANSI_EMERGENCY_YELLOW,
    clear_line,
    clear_screen,
    clear_screen_from_cursor,
    cursor_down,
    cursor_forward,
    cursor_goto,
    cursor_hide,
    cursor_save,
    cursor_restore,
    cursor_show,
    cursor_up,
    insert_lines,
    move_clear,
    reset_scroll_region,
    scroll_down,
    scroll_up,
    set_scroll_region,
    sgr,
    sgr_reset,
)

__all__ = [
    "ANSI_EMERGENCY_RED",
    "ANSI_EMERGENCY_YELLOW",
    "ANSI_EMERGENCY_RESET",
    "ANSI_EMERGENCY_CURSOR_BOTTOM",
    "set_scroll_region",
    "reset_scroll_region",
    "cursor_save",
    "cursor_restore",
    "cursor_goto",
    "cursor_up",
    "cursor_down",
    "cursor_forward",
    "cursor_hide",
    "cursor_show",
    "clear_line",
    "clear_screen_from_cursor",
    "clear_screen",
    "move_clear",
    "scroll_up",
    "scroll_down",
    "insert_lines",
    "sgr",
    "sgr_reset",
]
