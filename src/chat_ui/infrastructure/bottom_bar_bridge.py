"""BottomBarBridge — DECSTBM 管理层，替换 _BottomBar 的滚动区域管理。

将 ScrollRegionManager + _StdoutTracker + blessed_* ANSI 函数内联到本文件，
删除所有内容渲染逻辑（由 VNode 渲染路径处理）。

职责：
  - DECSTBM 滚动区域管理（setup/teardown/sync）
  - 光标定位（get_cursor_info/compute_cursor_position/ensure_cursor_*）
  - 状态管理（set_input_state/set_subagent_slots/enable_status/disable_status）
  - VNode 内容写入固定区域（force_redraw_from_vnode）

与 _BottomBar 的差异：
  - 移除 InputRenderer / _CompletionPopup / _StatusMixin
  - 移除 force_redraw()（全量重绘），改为 force_redraw_from_vnode()
  - _bottom_lines 计算内联（_expand_tabs + _wrap_by_width）
  - 新增 _completion_height 字段供 VNode 渲染路径设置
"""

from __future__ import annotations

import logging
import re
import sys
from collections import deque
from typing import IO, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .cursor_tracker import CursorTracker

from .terminal import get_terminal
from .text_visual import _expand_tabs, _wrap_by_width, _compute_cursor_visual_pos
from .bottom_theme import _BOTTOM_MIN_HEIGHT, _MIN_INPUT_ROWS, _BOTTOM_MIN_LINES
from .cursor_tracker import CursorTracker
from ...ui._lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)

__all__ = ["BottomBarBridge", "ScrollRegionManager", "_StdoutLineTracker"]


# ═══════════════════════════════════════════════════════════
# 内部：终端实例获取
# ═══════════════════════════════════════════════════════════

def _get_terminal():
    """获取 Blessed Terminal 实例。"""
    return get_terminal()


# ═══════════════════════════════════════════════════════════
# Blessed 辅助函数 — ANSI 序列生成（带降级回退）
# ═══════════════════════════════════════════════════════════

def blessed_move_clear(row: int) -> str:
    """生成移到指定行并清行的 ANSI 序列（1-based row）。"""
    try:
        term = _get_terminal()
        result = term.move_xy(0, row - 1) + term.clear_eol()
        return result if result else f"\033[{row};1H\033[K"
    except Exception:
        return f"\033[{row};1H\033[K"


def blessed_cursor_goto(row: int, col: int) -> str:
    """生成移到指定行列的 ANSI 序列（1-based）。"""
    try:
        term = _get_terminal()
        result = term.move_xy(col - 1, row - 1)
        return result if result else f"\033[{row};{col}H"
    except Exception:
        return f"\033[{row};{col}H"


def blessed_save_cursor() -> str:
    """保存光标位置（DECSC/SCOSC）。"""
    try:
        sc = _get_terminal().sc
        return sc if isinstance(sc, str) and sc else "\0337"
    except Exception:
        return "\0337"


def blessed_restore_cursor() -> str:
    """恢复光标位置（DECRC/SCRC）。"""
    try:
        rc = _get_terminal().rc
        return rc if isinstance(rc, str) and rc else "\0338"
    except Exception:
        return "\0338"


def blessed_scroll_up(n: int) -> str:
    """向上滚动 n 行（SU），n<=0 时返回空字符串。"""
    if n <= 0:
        return ""
    try:
        seq = _get_terminal().indn(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}S"
    except Exception:
        return f"\033[{n}S"


def blessed_scroll_down(n: int) -> str:
    """向下滚动 n 行（SD/RI），n<=0 时返回空字符串。"""
    if n <= 0:
        return ""
    try:
        seq = _get_terminal().rin(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}T"
    except Exception:
        return f"\033[{n}T"


def blessed_set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域 DECSTBM（1-based）。"""
    try:
        term = _get_terminal()
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
        return _get_terminal().height
    except Exception:
        import shutil
        return shutil.get_terminal_size().lines


def _term_width() -> int:
    """获取终端宽度。"""
    try:
        return _get_terminal().width
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
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total = max(_BOTTOM_MIN_LINES, last_bottom_lines)
        r_cursor = height - total + 3 + popup_height + vis_row
        r_cursor = max(1, min(r_cursor, height))
        col = min(3 + vis_col, term_w)
        sys.__stdout__.write(blessed_cursor_goto(r_cursor, col))
        self._cursor_tracker.set(r_cursor, col)


# ═══════════════════════════════════════════════════════════
# _StdoutLineTracker — stdout 行追踪器
# ═══════════════════════════════════════════════════════════

_CURSOR_POS_RE = re.compile(r'\x1b\[(\d+);(\d+)H')


class _StdoutLineTracker:
    """Transparent stdout wrapper that tracks complete lines for save/restore.

    All write/flush calls pass through to the real stdout unchanged.
    Lines are detected by \\n characters and stored in a ring buffer.
    Content written to the bottom bar area (detected via cursor positioning
    sequences) is filtered out and not tracked.
    """

    _MAX_LINES = 300

    def __init__(self, real_stdout: IO[str]):
        self._real_stdout = real_stdout
        self._ring: deque[str] = deque(maxlen=self._MAX_LINES)
        self._partial_line: str = ""
        self._scroll_end: int = 0
        self._in_bottom_bar: bool = False
        self._saved_rows: list[str] | None = None

    # ── File object protocol ──

    @property
    def encoding(self) -> str:
        return getattr(self._real_stdout, 'encoding', 'utf-8')

    @property
    def errors(self) -> str:
        return getattr(self._real_stdout, 'errors', 'strict')

    @property
    def buffer(self) -> Any:
        return self._real_stdout.buffer

    def fileno(self) -> int:
        return self._real_stdout.fileno()

    def isatty(self) -> bool:
        return self._real_stdout.isatty()

    def writable(self) -> bool:
        return True

    # ── Core write/flush ──

    def write(self, data: str) -> int:
        self._real_stdout.write(data)
        self._track(data)
        return len(data)

    def flush(self) -> None:
        self._real_stdout.flush()

    # ── Scroll end management ──

    def set_scroll_end(self, scroll_end: int) -> None:
        """Update the scroll region end row.

        Called by _BottomBar whenever DECSTBM is updated.
        scroll_end < 1 disables tracking.
        """
        self._scroll_end = scroll_end

    # ── Line tracking ──

    def _track(self, data: str) -> None:
        """Process data for line tracking.

        Strips cursor positioning sequences (\\033[{r};{c}H) from tracked
        content and uses them to detect bottom bar mode. Handles cursor
        restore sequences (\\0338, \\033[u) to exit bottom bar mode.
        Only tracks complete lines (ending with \\n) when scroll_end >= 1.
        """
        if self._scroll_end < 1:
            return

        # Handle cursor restore: \0338 or \033[u
        if '\x1b8' in data or '\x1b[u' in data:
            self._in_bottom_bar = False
            self._partial_line = ""

        # Process cursor positioning sequences: \033[{r};{c}H
        prev_end = 0
        for m in _CURSOR_POS_RE.finditer(data):
            # Text before this cursor position sequence
            if m.start() > prev_end:
                self._add_text(data[prev_end:m.start()])

            row = int(m.group(1))
            was_in_bottom_bar = self._in_bottom_bar
            self._in_bottom_bar = (row > self._scroll_end)
            if self._in_bottom_bar != was_in_bottom_bar:
                self._partial_line = ""

            prev_end = m.end()

        # Remaining text after last cursor position sequence
        if prev_end < len(data):
            self._add_text(data[prev_end:])

    def _add_text(self, text: str) -> None:
        """Accumulate text and extract complete lines (split on \\n)."""
        self._partial_line += text
        if '\n' in self._partial_line:
            *complete_lines, self._partial_line = self._partial_line.split('\n')
            if not self._in_bottom_bar:
                for line in complete_lines:
                    self._ring.append(line)

    # ── Save/restore API ──

    def save_rows_to_restore(self, n: int) -> None:
        """Save the last n complete lines from the ring buffer.

        Called before SU scroll in show_completions() to snapshot the
        content that will be scrolled out of view.

        Args:
            n: Number of rows to save.
        """
        if n <= 0:
            return
        ring_list = list(self._ring)
        if not ring_list:
            return
        self._saved_rows = list(ring_list[-n:]) if len(ring_list) >= n else list(ring_list)

    def get_saved_rows(self) -> list[str] | None:
        """Get the saved rows for restoration, or None if nothing saved."""
        return self._saved_rows

    def clear_saved(self) -> None:
        """Clear saved rows after they have been restored to the terminal."""
        self._saved_rows = None


# ═══════════════════════════════════════════════════════════
# _CmplState — 补全弹窗状态（纯数据，无 I/O）
# ═══════════════════════════════════════════════════════════

class _CmplState:
    """补全弹窗纯数据状态 — 由 BottomBarBridge 持有。

    仅存储补全/选择弹窗的运行时数据，不触发终端 I/O。
    实际渲染由 VNode 渲染路径（strategy.py）通过 BottomBarContent 组件完成。
    """

    __slots__ = (
        "_visible", "_title", "_is_selection",
        "_items", "_texts", "_idx",
        "_start_pos", "_orig_prefix",
        "_popup_height",
    )

    _MAX_ITEMS = 10  # 单屏最多显示选项数

    def __init__(self):
        self._visible: bool = False
        self._title: str = "补全"
        self._is_selection: bool = False
        self._items: list[str] = []
        self._texts: list[str] = []
        self._idx: int = 0
        self._start_pos: int = 0
        self._orig_prefix: str = ""
        self._popup_height: int = 0

    @property
    def is_visible(self) -> bool:
        return self._visible

    def show(self, items: list[str], selected_idx: int,
             texts: list[str] | None = None,
             start_pos: int = 0, orig_prefix: str = "",
             title: str = "补全") -> int:
        """设置补全弹窗状态（仅内存）。返回实际弹窗高度。"""
        if not items:
            self.hide()
            return 0

        h_items = min(len(items), self._MAX_ITEMS)
        popup_height = h_items + 2  # 标题行 + 选项行 + 快捷键行
        visible_items = items[:h_items]
        selected_idx = min(selected_idx, h_items - 1)

        self._popup_height = popup_height
        self._visible = True
        self._title = title
        self._is_selection = (title != "补全")
        self._items = list(visible_items)
        self._texts = list(texts) if texts is not None else list(visible_items)
        self._idx = selected_idx
        self._start_pos = start_pos
        self._orig_prefix = orig_prefix
        return popup_height

    def hide(self) -> None:
        """清除补全弹窗状态（仅内存）。"""
        self._popup_height = 0
        self._visible = False
        self._title = "补全"
        self._is_selection = False
        self._items = []
        self._texts = []
        self._idx = 0
        self._start_pos = 0
        self._orig_prefix = ""

    def cycle(self, delta: int) -> int:
        """切换选中索引（仅内存）。

        Args:
            delta: +1 下一项，-1 上一项。

        Returns:
            新的选中索引。
        """
        if not self._items:
            return 0
        self._idx = (self._idx + delta) % len(self._items)
        return self._idx

    def get_selected(self) -> tuple[str, int, str]:
        """获取当前选中项的补全数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        if not self._texts or self._idx >= len(self._texts):
            return ("", 0, "")
        return (self._texts[self._idx], self._start_pos, self._orig_prefix)


# ═══════════════════════════════════════════════════════════
# BottomBarBridge — DECSTBM 管理层 + VNode 桥接
# ═══════════════════════════════════════════════════════════

class BottomBarBridge:
    """DECSTBM 滚动区域管理器 + 光标定位 + VNode 内容桥接。

    替换 _BottomBar 的 DECSTBM 管理层。保留 ScrollRegionManager、
    _StdoutLineTracker、CursorTracker 组合，删除所有内容渲染逻辑。

    内容渲染（状态行、输入栏、补全弹窗、SubAgent 槽位）改由
    VNode 渲染路径产出，通过 force_redraw_from_vnode() 写入终端固定区域。
    """

    _MIN_HEIGHT = _BOTTOM_MIN_HEIGHT

    def __init__(self, cursor_tracker: CursorTracker | None = None):
        # ── 激活状态 ──
        self._active = False
        self._last_text = ""
        self._status_active = False
        self._model_name: str = ""  # 由 app_loop 通过 set_model_name 设置

        # ── 布局/光标 ──
        self._last_scroll_end: int = 0
        self._last_height: int = 0  # 哨兵值 0，确保首帧触发全量重绘
        self._last_sync_height: int = 0
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._completion_height: int = 0  # 由 VNode 渲染路径设置

        # ── SubAgent 槽位数据 ──
        self._subagent_slots: dict = {}
        self._subagent_slots_dirty: bool = False
        self._subagent_line_count: int = 0

        # ── 补全弹窗状态 ──
        self._cmpl = _CmplState()

        # ── 组合对象 ──
        self._cursor_tracker = cursor_tracker or CursorTracker()
        self._scroll = ScrollRegionManager(self._cursor_tracker)
        self._tracker: _StdoutLineTracker | None = None

    # ═══════════════════════════════════════════════════════════
    # 动态行数计算
    # ═══════════════════════════════════════════════════════════

    @property
    def _bottom_lines(self) -> int:
        """当前底部栏总行数（分隔线 + 状态行 + 输入行 + 补全 + SubAgent）。"""
        text = self._last_text or ""
        if text:
            # 使用与 renderer 一致的宽度 w - 5，按 \n 拆分计算
            max_input = max(1, self._term_width() - 5)
            total_visual_rows = 0
            for logical_line in text.split('\n'):
                expanded = _expand_tabs(logical_line)
                wrapped = _wrap_by_width(expanded, max_input)
                total_visual_rows += max(1, len(wrapped)) if wrapped else 1
            input_rows = max(_MIN_INPUT_ROWS, total_visual_rows)
        else:
            input_rows = _MIN_INPUT_ROWS
        # 2 = 分隔线 + 状态行
        return 2 + input_rows + self._subagent_line_count + self._completion_height

    # ═══════════════════════════════════════════════════════════
    # 终端尺寸查询
    # ═══════════════════════════════════════════════════════════

    def _term_height(self) -> int:
        return _term_height()

    def _term_width(self) -> int:
        return _term_width()

    # ═══════════════════════════════════════════════════════════
    # 光标定位
    # ═══════════════════════════════════════════════════════════

    def get_scroll_end(self) -> int:
        """获取当前滚动区域底部行号（1-based）。

        供 RenderEngine 在 resize 处理时保存旧 scroll_end 使用。
        """
        return self._last_scroll_end

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        """获取光标定位所需数据：文本、光标位置、终端高度、终端宽度。

        供 RenderEngine.position_cursor 使用，避免直接访问私有属性。
        """
        return (
            self._last_text,
            self._input_cursor_pos,
            self._term_height(),
            self._term_width(),
        )

    def compute_cursor_position(
        self, text: str, cursor_pos: int, h: int, w: int,
    ) -> tuple[int, int]:
        """计算光标在底部栏中的终端行号和列号（公开 API）。

        Args:
            text: 当前输入文本。
            cursor_pos: 光标在文本中的偏移位置（-1 表示末尾）。
            h: 终端高度。
            w: 终端宽度。

        Returns:
            (r_cursor, cursor_col) — 光标所在行号（1-based）和列号（1-based）。
        """
        # 使用与 renderer（BottomBarContent._render_input_area）一致的宽度：
        # 第一行前缀 "  ❯ " 占 5 列（2空格 + ❯(2列) + 空格），续行 "   · " 占 5 列
        # 因此可用宽度 = w - 5
        max_input = max(1, w - 5)
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)

        # 计算输入行数：按 \n 拆分后逐行展开+拆行，与 _compute_cursor_visual_pos 保持一致
        if text:
            total_visual_rows = 0
            for logical_line in text.split('\n'):
                expanded = _expand_tabs(logical_line)
                wrapped = _wrap_by_width(expanded, max_input)
                total_visual_rows += max(1, len(wrapped)) if wrapped else 1
            input_rows = max(_MIN_INPUT_ROWS, total_visual_rows)
        else:
            input_rows = _MIN_INPUT_ROWS

        # total_bottom = 分隔线 + 状态行 + 输入行 + 补全弹窗 + SubAgent 槽位
        total_bottom = max(
            5, 2 + input_rows + self._completion_height + self._subagent_line_count,
        )
        # 光标行 = 终端底 - 总行 + 3(分隔线+状态行+1) + 补全弹窗 + SubAgent 槽位 + 视觉行
        r_cursor = max(
            1,
            h - total_bottom + 3 + self._completion_height
            + self._subagent_line_count + vis_row,
        )
        r_cursor = min(r_cursor, h)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    def setup(self) -> None:
        """启用底部栏：设置滚动区域（不绘制）。

        终端高度不足 _MIN_HEIGHT（10）时静默跳过。
        幂等：已激活时重复调用无效果。

        初始绘制推迟到 render 线程首帧 force_redraw_from_vnode() 执行，
        _last_height 保留哨兵值 0 确保首帧必定触发终端写入。
        """
        if self._active:
            return
        height = self._term_height()
        if height < self._MIN_HEIGHT:
            return
        self._active = True

        # ── 安装 stdout 行追踪器 ──
        if self._tracker is None:
            self._tracker = _StdoutLineTracker(sys.__stdout__)
        if sys.__stdout__ is not self._tracker:
            sys.__stdout__ = self._tracker

        with _try_acquire_output_lock(name="bottom_bar.setup", timeout=1.0) as locked:
            if locked:
                self._last_text = ""
                self._completion_height = 0
                bottom_lines = self._bottom_lines
                scroll_end = height - bottom_lines
                self._last_scroll_end = scroll_end
                self._last_sync_height = height
                # ★ 不设置 _last_height（保留哨兵 0），确保首帧渲染
                if self._tracker is not None:
                    self._tracker.set_scroll_end(scroll_end)
                out = sys.__stdout__
                out.write(blessed_save_cursor())
                out.write(f"{blessed_set_scroll_region(1, scroll_end)}")
                # ★ 不绘制——推迟到 render 线程首帧
                out.write(blessed_restore_cursor())
                out.write(blessed_cursor_goto(scroll_end, 1) + blessed_save_cursor())
                out.write(blessed_cursor_goto(height, 1))
                out.flush()
            else:
                sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
                sys.__stdout__.flush()

    def teardown(self) -> None:
        """停用底部栏：重置滚动区域为全屏 + 清理底部残留。

        使用 DECSC/DECRC 保存/恢复光标，确保不干扰内容区光标位置。
        幂等：未激活时重复调用无效果。
        """
        if not self._active:
            return
        self._active = False

        # ── 卸载 stdout 行追踪器 ──
        if self._tracker is not None and sys.__stdout__ is self._tracker:
            sys.__stdout__ = self._tracker._real_stdout
            self._tracker = None

        bottom_lines_at_teardown = self._bottom_lines

        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                out.write(blessed_reset_scroll_region())
                out.write(blessed_save_cursor())
                height = self._term_height()
                start_row = max(1, height - bottom_lines_at_teardown + 1)
                for r in range(start_row, height + 1):
                    out.write(blessed_move_clear(r))
                out.write(blessed_restore_cursor())
                out.write(blessed_save_cursor())
                out.flush()
        self._last_height = 0
        self._last_sync_height = 0
        self._subagent_slots = {}
        self._subagent_line_count = 0
        self._subagent_slots_dirty = False
        self._completion_height = 0

    # ═══════════════════════════════════════════════════════════
    # 滚动区域同步 + 光标定位（委托 ScrollRegionManager）
    # ═══════════════════════════════════════════════════════════

    def sync_bottom_lines(self) -> None:
        """同步 DECSTBM 滚动区域与当前 _bottom_lines 缓存值。

        委托给 ScrollRegionManager.sync_bottom_lines。
        """
        new_scroll, new_height = self._scroll.sync_bottom_lines(
            self._active,
            self._bottom_lines,
            self._last_scroll_end,
            self._last_sync_height,
            self._tracker,
        )
        self._last_scroll_end = new_scroll
        self._last_sync_height = new_height

    def ensure_cursor_in_upper(self) -> None:
        """将光标移到上屏内容区底部。委托给 ScrollRegionManager。"""
        self._scroll.ensure_cursor_in_upper(self._active, self._last_scroll_end)

    def ensure_cursor_in_lower(self) -> None:
        """将光标移回下屏输入行末尾。委托给 ScrollRegionManager。

        使用 _bottom_lines（含 subagent_line_count + completion_height）
        传递为 last_bottom_lines，确保 subagent 槽位存在时光标行号正确。
        """
        self._scroll.ensure_cursor_in_lower(
            self._active,
            self._last_text,
            self._input_cursor_pos,
            self._bottom_lines,
            self._completion_height,
        )

    # ═══════════════════════════════════════════════════════════
    # 状态管理（仅内存，无 I/O）
    # ═══════════════════════════════════════════════════════════

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        """设置输入文本和光标位置（仅内存，不触发 I/O）。

        由 ChatUIConsumer.refresh_bottom_bar() 调用。
        """
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def set_subagent_slots(self, slots: dict) -> None:
        """设置 SubAgent 槽位数据，通过 Tree 组件动态计算行数（仅内存）。

        使用 subagent_slots_to_tree() + Tree.render() 动态计算实际渲染行数，
        确保 _bottom_lines 计算与 Tree 渲染产出精确一致。
        标记 _subagent_slots_dirty 通知 VNode 渲染路径需要重新渲染。
        """
        if slots == self._subagent_slots:
            return
        self._subagent_slots = slots
        if not slots:
            self._subagent_line_count = 0
            self._subagent_slots_dirty = True
            return

        # 使用 Tree 组件动态计算行数
        from ..components.subagent_tree import subagent_slots_to_tree
        from ..components.tree import Tree
        tree_root = subagent_slots_to_tree(slots)
        if tree_root is not None:
            tree = Tree(root=tree_root, indent=2)
            rendered = tree.render()
            self._subagent_line_count = rendered.count('\n') + 1 if rendered else 0
        else:
            self._subagent_line_count = 0
        self._subagent_slots_dirty = True

    def set_completion_height(self, height: int) -> None:
        """设置补全弹窗高度（由 VNode 渲染路径调用）。

        影响 _bottom_lines 计算和光标定位，不触发终端 I/O。
        """
        self._completion_height = height

    # ═══════════════════════════════════════════════════════════
    # 状态行活跃开关
    # ═══════════════════════════════════════════════════════════

    @property
    def is_status_active(self) -> bool:
        """状态行是否处于活跃刷新中（流式输出期间）。"""
        return self._status_active

    def enable_status(self) -> None:
        """激活状态行刷新（流式输出期间调用）。"""
        self._status_active = True

    def disable_status(self) -> None:
        """冻结状态行（流式结束后调用）。

        将 _status_active 置为 False。调用方负责在之后触发
        force_redraw_from_vnode() 更新视觉。
        """
        self._status_active = False

    def set_model_name(self, name: str) -> None:
        """设置当前模型名字（向后兼容 app_loop 调用）。

        跨线程安全：CPython GIL 保证简单 str 属性赋值原子安全。
        读取方（strategy._render_bottom_bar）通过 engine._bb._model_name 访问。
        """
        self._model_name = name

    def get_status_elapsed(self) -> float:
        """获取状态行耗时（向后兼容，返回 0.0）。

        原 _StatusMixin 通过 _get_snapshot() 获取真实耗时，
        现状态行数据由 TuiStore 管理，此方法返回哨兵值。
        """
        return 0.0

    def reset_tool_count(self) -> None:
        """重置工具计数（向后兼容，空操作）。

        工具计数现由 TuiStore reducer 管理，
        此方法仅保留接口兼容性。
        """
        pass

    # ═══════════════════════════════════════════════════════════
    # 补全弹窗状态管理（仅内存，无 I/O）
    # ═══════════════════════════════════════════════════════════

    @property
    def completion_index(self) -> int:
        """当前补全选中索引（供 run_bottom_bar_selection 查询）。

        弹窗不可见时返回 0。
        """
        return self._cmpl._idx

    @property
    def is_completion_visible(self) -> bool:
        """补全弹窗是否可见（供 EscapeMonitor 线程查询）。"""
        return self._cmpl.is_visible

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None,
                         start_pos: int = 0, orig_prefix: str = "",
                         title: str = "补全") -> None:
        """设置补全弹窗状态（仅内存，不触发终端 I/O）。

        由 _CmplHandler 或 user_select 工具在 EscapeMonitor 线程中调用。
        状态更新后，调用方应通过 request_redraw / push_cmd 触发 render 线程重绘。

        Args:
            items: 显示文本列表。
            selected_idx: 初始选中索引。
            texts: 补全结果文本列表（可选，默认同 items）。
            start_pos: 替换起始位置。
            orig_prefix: 原始前缀。
            title: 弹窗标题（"补全" / "选择" 等）。
        """
        if not self._active:
            return
        popup_height = self._cmpl.show(
            items, selected_idx, texts=texts,
            start_pos=start_pos, orig_prefix=orig_prefix, title=title,
        )
        self._completion_height = popup_height

    def hide_completions(self) -> None:
        """清除补全弹窗状态（仅内存，不触发终端 I/O）。

        幂等：弹窗未显示时无效果。
        调用方应通过 request_redraw / push_cmd 触发 render 线程重绘。
        """
        self._cmpl.hide()
        self._completion_height = 0

    def cycle_completion(self, delta: int = 1) -> int:
        """切换补全选中项（仅内存，不触发终端 I/O）。

        调用方应通过 request_redraw / push_cmd 触发 render 线程重绘。

        Args:
            delta: +1 下一项，-1 上一项。

        Returns:
            新的选中索引。
        """
        return self._cmpl.cycle(delta)

    def get_selected_completion(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        return self._cmpl.get_selected()

    def get_completion_snapshot(self) -> dict:
        """获取补全弹窗状态快照（公开 API），供外部渲染器使用。

        避免外部代码直接访问 _cmpl 私有属性。

        Returns:
            {"items": list[str], "selected": int, "title": str, "is_selection": bool}
        """
        return {
            "items": list(self._cmpl._items),
            "selected": self._cmpl._idx,
            "title": self._cmpl._title,
            "is_selection": self._cmpl._is_selection,
        }

    # ═══════════════════════════════════════════════════════════
    # VNode 内容渲染
    # ═══════════════════════════════════════════════════════════

    def force_redraw_from_vnode(self, vnode_content: str) -> None:
        """接收已渲染的 VNode 内容字符串，写入终端固定区域。

        vnode_content 为按行分隔（\\n）的 ANSI 文本，不含光标定位序列。
        本方法负责：
          1. 获取 output_lock
          2. 重置滚动区域 → 清除底部区域 → 逐行写入 → 恢复滚动区域
          3. 更新 _cursor_tracker 和 _last_scroll_end / _last_height

        线程安全：可被任何线程调用，通过 output_lock 串行化终端 I/O。
        """
        if not self._active:
            return

        with _try_acquire_output_lock(
            name="bottom_bar.force_redraw_from_vnode", timeout=1.0,
        ) as locked:
            if not locked:
                return

            out = sys.__stdout__
            out.write(blessed_save_cursor())

            height = self._term_height()
            # 从实际渲染内容计算行数，确保与 vnode_content 一致
            # （不再依赖可能过时的 _bottom_lines 属性）
            total = len(vnode_content.split('\n')) if vnode_content else self._bottom_lines
            scroll_end = height - total

            if scroll_end < 1:
                # 终端高度不足以容纳底部栏，清屏
                for r in range(1, height + 1):
                    out.write(blessed_move_clear(r))
                out.write(blessed_restore_cursor())
                out.write(blessed_cursor_goto(height, 1) + blessed_save_cursor())
                out.flush()
                self._cursor_tracker.set(height, 1)
                self._last_scroll_end = height
                self._last_height = height
                self._subagent_slots_dirty = False
                return

            # ── 重置滚动区域，写入内容到固定行 ──
            out.write(blessed_reset_scroll_region())

            # 逐行写入 vnode_content
            lines = vnode_content.split('\n')
            r = height - total + 1
            for line in lines:
                if 1 <= r <= height:
                    out.write(blessed_move_clear(r) + line)
                    r += 1

            # 清除底部区域剩余行（行数减少时清理残留）
            for rr in range(r, height + 1):
                out.write(blessed_move_clear(rr))

            self._cursor_tracker.set(height, 1)

            # ── 恢复滚动区域 ──
            if self._tracker is not None:
                self._tracker.set_scroll_end(scroll_end)
            out.write(f"{blessed_set_scroll_region(1, scroll_end)}")
            out.write(blessed_restore_cursor())
            out.write(blessed_cursor_goto(scroll_end, 1) + blessed_save_cursor())
            self._cursor_tracker.set(scroll_end, 1)
            out.flush()

            self._last_scroll_end = scroll_end
            self._last_height = height
            self._subagent_slots_dirty = False
