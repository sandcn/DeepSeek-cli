"""chat_ui 底部栏子系统 — 终端底部固定输入栏。

提供 BottomBar 主类及所有子模块的公开 API。
"""

from ._bar import BottomBar
from ._cursor_tracker import CursorTracker, CursorPosition
from ._selection import run_bottom_bar_selection, _KEY_ENTER, _KEY_UP, _KEY_DOWN, _KEY_ESCAPE
from ._completion_popup import _CompletionPopup
from ._theme import (
    _COLOR_ACCENT, _COLOR_DEEP_CYAN, _COLOR_DIM, _COLOR_RESET,
    _COLOR_SEP, _BOTTOM_MIN_HEIGHT, _BOTTOM_MIN_LINES, _MIN_INPUT_ROWS,
    _PLACEHOLDER_TEXT, _PLACEHOLDER_COMPACT, _PLACEHOLDER_STREAMING,
)
from ._status import StatusRenderer
from ._scroll_region import (
    ScrollRegionManager,
    blessed_move_clear, blessed_cursor_goto,
    blessed_save_cursor, blessed_restore_cursor,
    blessed_set_scroll_region, blessed_reset_scroll_region,
    blessed_scroll_up, blessed_scroll_down,
    _term_height, _term_width,
)
from ._cursor import (
    _expand_tabs, _wrap_by_width, _compute_cursor_visual_pos,
    _visual_len, _truncate_by_width, _tab_pos_to_expanded,
    _TAB_WIDTH,
)
from ._input_renderer import InputRenderer

__all__ = [
    "BottomBar",
    "CursorTracker", "CursorPosition",
    "run_bottom_bar_selection",
    "_KEY_ENTER", "_KEY_UP", "_KEY_DOWN", "_KEY_ESCAPE",
    "_CompletionPopup",
    "StatusRenderer",
    "InputRenderer",
    "ScrollRegionManager",
    "_COLOR_ACCENT", "_COLOR_DEEP_CYAN", "_COLOR_DIM", "_COLOR_RESET",
    "_COLOR_SEP", "_BOTTOM_MIN_HEIGHT", "_BOTTOM_MIN_LINES", "_MIN_INPUT_ROWS",
    "_PLACEHOLDER_TEXT", "_PLACEHOLDER_COMPACT", "_PLACEHOLDER_STREAMING",
    "blessed_move_clear", "blessed_cursor_goto",
    "blessed_save_cursor", "blessed_restore_cursor",
    "blessed_set_scroll_region", "blessed_reset_scroll_region",
    "blessed_scroll_up", "blessed_scroll_down",
    "_term_height", "_term_width",
    "_expand_tabs", "_wrap_by_width", "_compute_cursor_visual_pos",
    "_visual_len", "_truncate_by_width", "_tab_pos_to_expanded", "_TAB_WIDTH",
]
