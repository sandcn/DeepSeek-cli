"""DECSTBM 滚动区域管理 + ANSI 辅助函数。

从 _bottom_bar.py 拆分，管理终端滚动区域的生命周期和光标定位。
所有函数返回 ANSI 序列字符串，由调用方负责写入终端。

职责：
  - DECSTBM 设置/重置（_blessed_set_scroll_region / _blessed_reset_scroll_region）
  - 行操作（_blessed_move_clear / _blessed_cursor_goto）
  - 光标保存/恢复（_blessed_save_cursor / _blessed_restore_cursor）
  - 滚动（_blessed_scroll_up / _blessed_scroll_down）
  - sync_bottom_lines — 同步 DECSTBM 到最新底部栏行数
  - ensure_cursor_in_upper / ensure_cursor_in_lower — 光标定位
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .._cursor_tracker import CursorTracker

from ...ui._blessed import get_terminal as _blessed_get_terminal

_logger = logging.getLogger(__name__)


def _get_terminal():
    """获取 get_terminal 函数引用，兼容测试通过 stub 路径或新路径 patch。

    双重路径兼容：
      1. 旧测试走 src.ui._scroll_region stub（检查 sys.modules）
      2. 新测试可直接 patch 本模块的 get_terminal 变量（module-level fallback）
    最终 fallback 到 get_terminal（而非 _blessed_get_terminal 直接引用），
    确保 patch 能覆盖到本模块的模块级变量。
    """
    try:
        _stub_mod = sys.modules.get("src.ui._scroll_region")
        if _stub_mod is not None:
            _stub_gt = _stub_mod.get_terminal
            if _stub_gt is not _blessed_get_terminal:
                return _stub_gt
    except Exception:
        pass
    return get_terminal


# ── 公开别名（供 stub 模块重导出） ──
get_terminal = _blessed_get_terminal


# ═══════════════════════════════════════════════════════════
# Blessed 辅助函数 — ANSI 序列生成（带降级回退）
# ═══════════════════════════════════════════════════════════

def blessed_move_clear(row: int) -> str:
    """生成移到指定行并清行的 ANSI 序列（1-based row）。"""
    try:
        term = _get_terminal()()
        result = term.move_xy(0, row - 1) + term.clear_eol()
        return result if result else f"\033[{row};1H\033[K"
    except Exception:
        return f"\033[{row};1H\033[K"


def blessed_cursor_goto(row: int, col: int) -> str:
    """生成移到指定行列的 ANSI 序列（1-based）。"""
    try:
        term = _get_terminal()()
        result = term.move_xy(col - 1, row - 1)
        return result if result else f"\033[{row};{col}H"
    except Exception:
        return f"\033[{row};{col}H"


def blessed_save_cursor() -> str:
    """保存光标位置（DECSC/SCOSC）。"""
    try:
        sc = _get_terminal()().sc
        return sc if isinstance(sc, str) and sc else "\0337"
    except Exception:
        return "\0337"


def blessed_restore_cursor() -> str:
    """恢复光标位置（DECRC/SCRC）。"""
    try:
        rc = _get_terminal()().rc
        return rc if isinstance(rc, str) and rc else "\0338"
    except Exception:
        return "\0338"


def blessed_scroll_up(n: int) -> str:
    """向上滚动 n 行（SU），n<=0 时返回空字符串。"""
    if n <= 0:
        return ""
    try:
        seq = _get_terminal()().indn(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}S"
    except Exception:
        return f"\033[{n}S"


def blessed_scroll_down(n: int) -> str:
    """向下滚动 n 行（SD/RI），n<=0 时返回空字符串。"""
    if n <= 0:
        return ""
    try:
        seq = _get_terminal()().rin(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}T"
    except Exception:
        return f"\033[{n}T"


def blessed_set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域 DECSTBM（1-based）。"""
    try:
        term = _get_terminal()()
        seq = term.csr(top - 1, bottom - 1)
        return seq if isinstance(seq, str) and seq else f"\033[{top};{bottom}r"
    except Exception:
        return f"\033[{top};{bottom}r"


def blessed_reset_scroll_region() -> str:
    """重置滚动区域为全屏（\\033[r）。"""
    return "\033[r"


# ═══════════════════════════════════════════════════════════
# 终端尺寸查询
# ═══════════════════════════════════════════════════════════

def _term_height() -> int:
    """获取终端高度。"""
    try:
        return _get_terminal()().height
    except Exception:
        import shutil
        return shutil.get_terminal_size().lines


def _term_width() -> int:
    """获取终端宽度。"""
    try:
        return _get_terminal()().width
    except Exception:
        import shutil
        return shutil.get_terminal_size().columns


# ═══════════════════════════════════════════════════════════
# ScrollRegionManager — DECSTBM 滚动区域管理
# ═══════════════════════════════════════════════════════════

class ScrollRegionManager:
    """DECSTBM 滚动区域管理器。

    管理滚动区域的设置、同步和光标定位。不持有 _BottomBar 的完整状态，
    仅操作 shared mutable state（通过闭包/回调注入）。
    """

    def __init__(self, cursor_tracker: "CursorTracker"):
        self._cursor_tracker = cursor_tracker

    def sync_bottom_lines(
        self,
        active: bool,
        bottom_lines: int,
        last_scroll_end: int,
        last_sync_height: int,
        tracker,
    ) -> tuple[int, int]:
        """同步 DECSTBM 滚动区域到最新底部栏行数。

        返回 (new_scroll_end, new_sync_height)。
        """
        if not active:
            return (last_scroll_end, last_sync_height)
        height = _term_height()
        scroll_end = height - bottom_lines
        if scroll_end == last_scroll_end and height == last_sync_height:
            return (last_scroll_end, last_sync_height)
        resized = height != last_sync_height
        shrunk = height < last_sync_height
        if scroll_end < 1:
            scroll_end = height
        old_scroll = last_scroll_end
        new_scroll = scroll_end
        if tracker is not None:
            tracker.set_scroll_end(new_scroll)
        out = sys.__stdout__
        out.write(f"{blessed_set_scroll_region(1, new_scroll)}")
        if resized and new_scroll >= 1:
            out.write(blessed_move_clear(new_scroll))
            if shrunk and old_scroll > new_scroll:
                for r in range(new_scroll + 1, min(old_scroll, height) + 1):
                    out.write(blessed_move_clear(r))
        out.write(blessed_cursor_goto(new_scroll, 1) + blessed_save_cursor())
        out.flush()
        return (new_scroll, height)

    def ensure_cursor_in_upper(self, active: bool, last_scroll_end: int) -> None:
        """将光标移到上屏内容区底部（滚动区域内）。"""
        if not active:
            return
        scroll_end = last_scroll_end
        if scroll_end < 1:
            scroll_end = _term_height()
        sys.__stdout__.write(blessed_cursor_goto(scroll_end, 1))
        self._cursor_tracker.set(scroll_end, 1)

    def ensure_cursor_in_lower(
        self,
        active: bool,
        last_text: str,
        cursor_pos: int,
        last_bottom_lines: int,
        popup_height: int,
    ) -> None:
        """将光标移回下屏输入行末尾。"""
        if not active:
            return
        height = _term_height()
        term_w = _term_width()
        text = last_text or ""
        max_input = max(1, term_w - 4)
        # 复用 _compute_cursor_visual_pos（从迁移模块导入）
        from ._cursor import _compute_cursor_visual_pos
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        from ._theme import _BOTTOM_MIN_LINES
        total = max(_BOTTOM_MIN_LINES, last_bottom_lines)
        r_cursor = height - total + 3 + popup_height + vis_row
        r_cursor = max(1, min(r_cursor, height))
        col = min(3 + vis_col, term_w)
        sys.__stdout__.write(blessed_cursor_goto(r_cursor, col))
        self._cursor_tracker.set(r_cursor, col)
