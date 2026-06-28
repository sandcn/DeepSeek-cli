"""_BottomBar — 流式输出期间固定底部输入栏（动态拆行）。

在终端底部使用 ANSI DECSTBM 滚动区域创建固定区域：
上方内容区正常滚动，底部固定显示（分隔线 + 状态行 + 输入区）。

线程安全（分两级）：
  - 内容变更全量重绘（文本/状态/尺寸变化）→ output_lock 串行化
  - 纯光标移动轻量路径 → 无锁直写 ANSI 序列（GIL + 幂等性保证安全）

拆分为多个子模块：
  - _bottom_bar_theme    — ANSI 颜色常量 + 占位符 + 布局配置
  - _bottom_bar_status   — 状态行格式化 + 工具计数（_StatusMixin）
  - _bottom_bar_completion — 补全弹窗（_CompletionPopup 独立类）
  - _bottom_bar_selection  — run_bottom_bar_selection() 交互选择

终端控制策略：
  - 非关键路径 ANSI 序列（光标定位、清行）使用 Blessed Terminal
  - 性能关键路径（SCOSC/SCRC、DECSTBM、SU/SD）保留原始 ANSI
  - 颜色常量保持原始 ANSI 字符串（与 Blessed 序列可混合使用）
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from wcwidth import wcswidth

from ._blessed import get_terminal
from ._bottom_bar_completion import _CompletionPopup
from ._bottom_bar_selection import run_bottom_bar_selection  # noqa: F401 — 重导出保持兼容
from ._bottom_bar_status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT  # noqa: F401 — 重导出供测试 patch
from ._stdout_tracker import _StdoutLineTracker
from ._bottom_bar_theme import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _MIN_INPUT_ROWS,
    _PLACEHOLDER_COMPACT,
    _PLACEHOLDER_STREAMING,
    _PLACEHOLDER_TEXT,
)
from ._bottom_cursor import (
    _compute_cursor_visual_pos,
    _expand_tabs,
    _wrap_by_width,
)
from ._cursor_tracker import CursorTracker
from ._lock import _try_acquire_output_lock


# ── Blessed 辅助函数 ─────────────────────────────────────
# 将常用的 ANSI 序列封装为 Blessed 调用，带降级回退


def _blessed_move_clear(row: int) -> str:
    """生成移到指定行并清行的 ANSI 序列。

    通过 Blessed Terminal.move_xy + clear_eol 生成，
    Blessed 不可用时或返回空时回退到原始 ANSI。

    Args:
        row: 1-based 行号。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        result = term.move_xy(0, row - 1) + term.clear_eol()
        return result if result else f"\033[{row};1H\033[K"
    except Exception:
        return f"\033[{row};1H\033[K"


def _blessed_cursor_goto(row: int, col: int) -> str:
    """生成移到指定行列的 ANSI 序列。

    通过 Blessed Terminal.move_xy 生成。
    Blessed 使用 0-based 坐标，不可用时或返回空时回退到原始 ANSI。

    Args:
        row: 1-based 行号。
        col: 1-based 列号。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        result = term.move_xy(col - 1, row - 1)
        return result if result else f"\033[{row};{col}H"
    except Exception:
        return f"\033[{row};{col}H"

# ── Blessed 光标保存/恢复、滚动、DECSTBM 辅助函数 ─────────
# 将保留的原始 ANSI 序列（DECSC/DECRC/SCOSC/SCRC/SU/SD/DECSTBM）
# 封装为 Blessed API 调用，带 try/except 回退到原始 ANSI。


def _blessed_save_cursor() -> str:
    """保存光标位置（DECSC/SCOSC）。

    通过 Blessed Terminal.sc 生成 DECSC 序列，
    Blessed 不可用时回退到原始 ANSI。

    Returns:
        ANSI 序列字符串。
    """
    try:
        sc = get_terminal().sc
        return sc if isinstance(sc, str) and sc else "\0337"
    except Exception:
        return "\0337"


def _blessed_restore_cursor() -> str:
    """恢复光标位置（DECRC/SCRC）。

    通过 Blessed Terminal.rc 生成 DECRC 序列，
    Blessed 不可用时回退到原始 ANSI。

    Returns:
        ANSI 序列字符串。
    """
    try:
        rc = get_terminal().rc
        return rc if isinstance(rc, str) and rc else "\0338"
    except Exception:
        return "\0338"


def _blessed_scroll_up(n: int) -> str:
    """向上滚动 n 行（SU）。

    通过 Blessed Terminal.indn 生成 SU 序列。
    n <= 0 时返回空字符串。
    Blessed 不可用时回退到原始 ANSI。

    Args:
        n: 滚动行数。

    Returns:
        ANSI 序列字符串。
    """
    if n <= 0:
        return ""
    try:
        seq = get_terminal().indn(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}S"
    except Exception:
        return f"\033[{n}S"


def _blessed_scroll_down(n: int) -> str:
    """向下滚动 n 行（SD/RI）。

    通过 Blessed Terminal.rin 生成 SD 序列。
    n <= 0 时返回空字符串。
    Blessed 不可用时回退到原始 ANSI。

    Args:
        n: 滚动行数。

    Returns:
        ANSI 序列字符串。
    """
    if n <= 0:
        return ""
    try:
        seq = get_terminal().rin(n)
        return seq if isinstance(seq, str) and seq else f"\033[{n}T"
    except Exception:
        return f"\033[{n}T"


def _blessed_set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域（DECSTBM）。top/bottom 为 1-based。

    通过 Blessed Terminal.csr 生成 DECSTBM 序列。
    Blessed 使用 0-based 坐标，内部自动转换。
    Blessed 不可用时回退到原始 ANSI。

    Args:
        top: 滚动区域起始行（1-based）。
        bottom: 滚动区域结束行（1-based）。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        seq = term.csr(top - 1, bottom - 1)
        return seq if isinstance(seq, str) and seq else f"\033[{top};{bottom}r"
    except Exception:
        return f"\033[{top};{bottom}r"


def _blessed_reset_scroll_region() -> str:
    """重置滚动区域为全屏（DECSTBM 重置）。

    ★ P0 修复 2026-06-11: 始终返回原始 ANSI 序列 \033[r，不使用
    Blessed Terminal.csr(0, -1) 生成。Blessed 的 csr(0, -1) 会返回
    \033[1;0r（因为 -1+1=0），这是非法的 DECSTBM 参数——底部行号 0
    小于顶部行号 1。不同终端对此非法序列的处理不一致：
    Termux/部分终端会清空屏幕或行为异常，导致「弹出补全信息清空上屏内容」的 Bug。
    \033[r（无参数）是标准 ANSI/DEC 重置序列，所有终端正确支持。

    Returns:
        ANSI 序列字符串（"\033[r"）。
    """
    return "\033[r"


_logger = logging.getLogger(__name__)


class _BottomBar(_StatusMixin):
    """终端底部固定输入栏，流式输出期间始终可见。

    使用 ANSI DECSTBM 滚动区域：上方内容区（1 至 H-底部行数）正常滚动，
    底部行（分隔线 + 状态行 + 动态输入区）位于滚动区域之外，
    通过手动定位绘制保持固定。

    视觉风格（优雅信息风）：
      - 分隔线：蓝灰 `━` 实线做内容区与输入区边界
      - 状态行：多色分层（◉ 模型名·耗时·令牌数·工具计数）
                使用亮青/蓝灰/灰色三层颜色，信息密度高但易读
      - 输入区：亮青 `❯` 提示符，空输入时显示灰色占位提示
                多行续行以灰色 `·` 前缀连接，视觉连贯
      - 补全弹窗：无边框扁平样式（标题行 + ▶ 指示器高亮 + 快捷键提示）

    线程安全（分两级）：
      - 内容变更全量重绘（文本/状态/尺寸变化）→ output_lock 串行化
      - 纯光标移动轻量路径 → 无锁直写 ANSI 序列（GIL + 幂等性保证安全）
    """

    _MIN_HEIGHT = _BOTTOM_MIN_HEIGHT

    def __init__(self, cursor_tracker: CursorTracker | None = None):
        self._active = False
        self._last_text = ""
        self._last_status = ""
        self._last_refresh = 0.0
        # ── _StatusMixin 依赖字段 ──
        self._status_active: bool = False
        self._model_name: str = ""
        self._tool_count: int = 0
        self._tool_fail_count: int = 0
        self._tool_total: int = 0
        self._subagent_lines: list[str] = []
        self._last_subagent_lines: list[str] = []
        # ── 布局/光标 ──
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._cached_wrapped_for: str = ""
        self._cached_wrapped_width: int = 0
        self._cached_wrapped_lines: list[str] | None = None
        self._cached_input_rows: int = _MIN_INPUT_ROWS
        self._last_rendered_text: str = ""
        self._last_scroll_end: int = 0
        self._last_height: int = 0  # 哨兵值，首次 force_redraw() 必然触发全量重绘（终端高度始终 ≥1）
        self._last_sync_height: int = 0  # sync_bottom_lines() 中用于检测终端 resize
        # ── 补全弹窗组合对象 ──
        self._completion = _CompletionPopup(cursor_tracker=cursor_tracker)
        # ── stdout 行追踪器 ──
        self._tracker: _StdoutLineTracker | None = None
        # ── 光标坐标追踪器（全局共享实例） ──
        self._cursor_tracker = cursor_tracker or CursorTracker()

    # ── 补全弹窗兼容 property（供外部直读私有属性的调用方） ──

    @property
    def _completion_visible(self) -> bool:
        return self._completion._visible

    @property
    def _completion_title(self) -> str:
        return self._completion._title

    @property
    def _completion_items(self) -> list[str]:
        return self._completion._items

    @property
    def _completion_texts(self) -> list[str]:
        return self._completion._texts

    @property
    def _completion_start_pos(self) -> int:
        return self._completion._start_pos

    @property
    def _completion_orig_prefix(self) -> str:
        return self._completion._orig_prefix

    @property
    def _completion_is_selection(self) -> bool:
        return self._completion._is_selection

    @property
    def _completion_idx(self) -> int:
        return self._completion._idx

    @property
    def _completion_popup_height(self) -> int:
        return self._completion._popup_height

    @_completion_popup_height.setter
    def _completion_popup_height(self, value: int) -> None:
        self._completion._popup_height = value

    # ── 动态行数计算 ──────────────────────────────────────

    @property
    def _bottom_lines(self) -> int:
        """当前底部栏总行数（分隔线 + subagent面板行 + 状态行 + 输入行）。"""
        return 2 + len(self._subagent_lines) + self._compute_input_rows()

    def _compute_input_rows(self) -> int:
        """根据当前输入文本计算所需的输入行数（最少 3 行 + 补全弹窗高度）。"""
        text = self._last_text or ""
        if not text:
            base = _MIN_INPUT_ROWS
        else:
            max_input = max(1, self._term_width() - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(_MIN_INPUT_ROWS, len(wrapped))
        return base + self._completion.height

    # ── 终端尺寸查询（通过 Blessed Terminal） ──────────

    def _term_height(self) -> int:
        """获取终端高度，通过 Blessed Terminal 实时查询。"""
        try:
            return get_terminal().height
        except Exception:
            import shutil
            return shutil.get_terminal_size().lines

    def _term_width(self) -> int:
        """获取终端宽度，通过 Blessed Terminal 实时查询。"""
        try:
            return get_terminal().width
        except Exception:
            import shutil
            return shutil.get_terminal_size().columns

    # ── 光标定位相关 ──────────────────────────────────

    def get_scroll_end(self) -> int:
        """获取当前滚动区域底部行号（1-based）。

        供 RenderEngine 在 resize 处理时保存旧 scroll_end 使用。
        """
        return self._last_scroll_end

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        """获取光标定位所需数据：文本、光标位置、终端高度、终端宽度。

        供 RenderEngine.position_cursor 使用，避免直接访问私有属性。
        返回值: (last_text, cursor_pos, term_height, term_width)

        使用 _last_rendered_text 而非 _last_text 作为定位基准：
        force_redraw() 渲染输入行时使用 _last_text 快照，但 EscapeMonitor
        线程可能在 force_redraw 和 position_cursor 之间调用 set_input_state()
        更新 _last_text。此时若新文本拆行数与渲染结果不同，position_cursor
        会计算出错误的 r_cursor，导致光标偏移 1 行。
        使用 _last_rendered_text 确保与屏幕显示的文本布局一致。
        cursor_pos 被 clamp 到文本长度，防止因文本版本不一致产生越界。
        """
        text = self._last_rendered_text if self._last_rendered_text else self._last_text
        cursor_pos = min(self._input_cursor_pos, len(text))
        return (
            text,
            cursor_pos,
            self._term_height(),
            self._term_width(),
        )

    def compute_cursor_position(
        self, text: str, cursor_pos: int, h: int, w: int,
    ) -> tuple[int, int]:
        """计算光标在底部栏中的终端行号和列号（公开 API）。

        封装以下私有访问：
          - _cursor_visual_pos_from_cache(text, cursor_pos, max_width)
          - _bottom_lines property（间接通过 _compute_input_rows 计算）
          - _completion_popup_height property

        供 RenderEngine.position_cursor() 使用。
        纯计算函数，不执行终端 I/O，调用方负责 flush。

        Args:
            text: 当前输入文本
            cursor_pos: 光标在文本中的偏移位置
            h: 终端高度
            w: 终端宽度

        Returns:
            (r_cursor, cursor_col) — 光标所在行号（1-based）和列号（1-based）
        """
        max_input = max(1, w - 4)
        vis_row, vis_col = self._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
        total_bottom = max(5, self._bottom_lines)  # 至少 2 分隔线+状态行 + 3 最少输入行
        popup_offset = self._completion.height
        # ★ +3 跳过 分隔线(1) + 状态行(1) + 输入区起始偏移(1)，
        #   +len(_subagent_lines) 补偿分隔线与状态行之间的 subagent 面板行
        subagent_offset = len(self._subagent_lines)
        r_cursor = max(1, h - total_bottom + 3 + subagent_offset + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    def _cursor_visual_pos_from_cache(
        self, text: str, cursor_pos: int, max_width: int,
    ) -> tuple[int, int]:
        """从缓存的拆行结果计算光标视觉位置。

        复用 _cached_wrapped_lines（_draw_input_lines_locked 中更新），
        避免在轻量路径中重算 _wrap_by_width。缓存失效时自动计算
        并更新缓存，避免回退到完整 O(n·wcswidth) 重算。

        Returns:
            (visual_line_idx, visual_col) —— 均为 0-based。
        """
        # 缓存失效时自动计算并更新缓存
        if (self._cached_wrapped_for != text
                or self._cached_wrapped_width != max_width
                or self._cached_wrapped_lines is None):
            expanded = _expand_tabs(text)
            self._cached_wrapped_lines = _wrap_by_width(expanded, max_width)
            self._cached_wrapped_for = text
            self._cached_wrapped_width = max_width
        abs_cursor = len(text) if cursor_pos < 0 else cursor_pos
        # 将光标位置从原始文本映射到展开后文本
        from ._bottom_cursor import _tab_pos_to_expanded
        expanded_pos = _tab_pos_to_expanded(text, abs_cursor)
        if expanded_pos < 0:
            expanded_pos = sum(len(s) for s in self._cached_wrapped_lines)
        newlines_before = text[:abs_cursor].count('\n')
        adjusted_pos = expanded_pos - newlines_before
        wrapped = self._cached_wrapped_lines
        cum = 0
        for i, seg in enumerate(wrapped):
            seg_len = len(seg)
            if adjusted_pos <= cum + seg_len:
                if adjusted_pos == cum + seg_len and i + 1 < len(wrapped):
                    return (i + 1, 0)
                prefix = seg[:adjusted_pos - cum]
                col = wcswidth(prefix)
                return (i, col)
            cum += seg_len
        last_idx = len(wrapped) - 1 if wrapped else 0
        last_col = wcswidth(wrapped[-1]) if wrapped else 0
        return (last_idx, last_col)

    def sync_bottom_lines(self) -> None:
        """同步当前 DECSTBM 滚动区域与 _bottom_lines 缓存值。

        当 _bottom_lines 变化但不触发 resize 时（如用户输入变长/补全弹窗弹出），
        此方法将最新的 _bottom_lines 转换为 scroll_end 并写入 DECSTBM ANSI 序列，
        同步更新 _last_scroll_end 缓存，确保后续 ensure_cursor_upper() 定位准确。

        调用方须持有 output_lock。
        在 _drain_queue() Stage 1 中 ensure_cursor_upper() 之前调用。
        """
        if not self._active:
            return
        height = self._term_height()
        scroll_end = height - self._bottom_lines
        if scroll_end == self._last_scroll_end and height == self._last_sync_height:
            return
        resized = height != self._last_sync_height
        shrunk = height < self._last_sync_height  # 终端缩小标志（更新 _last_sync_height 前保存）
        if scroll_end < 1:
            scroll_end = height
        old_scroll = self._last_scroll_end  # 保存旧值（更新前），供缩小场景清除旧行
        self._last_scroll_end = scroll_end
        self._last_sync_height = height
        if self._tracker is not None:
            self._tracker.set_scroll_end(scroll_end)
        out = sys.__stdout__

        # ★ 底部栏扩大时，直接设置新 DECSTBM 滚动区域。
        #    不执行 SU（Scroll Up）上滚旧内容区——SU 在 DECSTBM 区域内
        #    无 scrollback 缓冲，滚出顶部的行永久丢失。底部栏扩大导致
        #    滚动区域缩小时，新划入底部栏的区域（原内容区底部行）会被
        #    force_redraw() 中的 _draw_input_lines_locked() 覆盖。
        out.write(f"{_blessed_set_scroll_region(1, scroll_end)}")
        # ★ resize 后清除新 scroll_end 行上由终端模拟器位移残留的旧内容，
        #    确保后续内容渲染从干净行开始，消除旧内容与底部栏之间的 1 行重叠
        if resized and scroll_end >= 1:
            out.write(_blessed_move_clear(scroll_end))
            # 终端高度缩小时，清除 scroll_end+1 到 min(old_scroll, height) 整个区间，
            # 而非仅清除单一边界行。在流式输出路径中，CONTENT 渲染写入内容到滚动区
            # 会自然「冲刷」掉这些残留行；但在底部栏刷新（输入）路径中，
            # request_bottom_redraw 是标志位唤醒，无冲刷行为，必须在此处彻底清除，
            # 否则旧内容在 force_redraw() 执行前可见，导致视觉上的 1 行重叠。
            if shrunk and old_scroll > scroll_end:
                for r in range(scroll_end + 1, min(old_scroll, height) + 1):
                    out.write(_blessed_move_clear(r))
        out.write(_blessed_cursor_goto(scroll_end, 1) + _blessed_save_cursor())
        out.flush()

    def set_subagent_frame(self, lines: list[str]) -> None:
        """设置 subagent 面板行数据（仅写内存，由 force_redraw() 消费）。

        由 TuiRenderer._do_subagent_frame() 调用，在同一次 drain_queue
        的 output_lock 临界区内 force_redraw() 会自动拾取新数据。
        """
        self._subagent_lines = list(lines)

    def ensure_cursor_in_upper(self) -> None:
        """将光标移到上屏内容区底部（滚动区域内），准备渲染内容。

        渲染内容前调用：确保 renderer 写入内容时光标在正确区域，
        避免内容误写入底部固定栏（下屏）。
        使用 _last_scroll_end 缓存值，保证光标定位与当前 DECSTBM 一致，
        避免底部行数变化（补全弹窗/输入文本变化）时光标位置偏移导致覆盖旧内容。
        终端高度过小时将光标放在最后一行。

        坐标追踪：定位后同步 tracker 到 scroll_end。
        """
        if not self._active:
            return
        scroll_end = self._last_scroll_end
        if scroll_end < 1:
            scroll_end = self._term_height()
        sys.__stdout__.write(_blessed_cursor_goto(scroll_end, 1))
        self._cursor_tracker.set(scroll_end, 1)

    def ensure_cursor_in_lower(self) -> None:
        """渲染完成后将光标移回下屏输入行末尾（含动态拆行，最少3行输入区）。

        只做光标跳转，不重绘输入行（避免覆盖用户通过
        左右键移动光标后的位置）。光标停在输入文本末尾。
        超长文本会自动拆行，光标位于最后一行末尾。
        空输入时光标位于输入区第一行（> 提示符行）。
        制表符按内部默认宽度展开为空格。
        终端高度过小时将光标放在最后一行。

        坐标追踪：定位后同步 tracker 到精确光标位置。
        """
        if not self._active:
            return
        height = self._term_height()
        term_w = self._term_width()
        text = self._last_text or ""
        cursor_pos = self._input_cursor_pos
        max_input = max(1, term_w - 4)
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total = max(_BOTTOM_MIN_LINES, self._last_bottom_lines)
        # ★ +3 跳过 分隔线(1) + 状态行(1) + 输入区起始偏移(1)，
        #   +len(_subagent_lines) 补偿分隔线与状态行之间的 subagent 面板行
        subagent_offset = len(self._subagent_lines)
        r_cursor = height - total + 3 + subagent_offset + self._completion.height + vis_row
        r_cursor = max(1, min(r_cursor, height))
        col = min(3 + vis_col, term_w)
        sys.__stdout__.write(_blessed_cursor_goto(r_cursor, col))
        self._cursor_tracker.set(r_cursor, col)

    # ── 生命周期 ──────────────────────────────────────────

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        """设置输入文本和光标位置（线程安全，仅更新状态，不直接 I/O）。

        由 ChatUIConsumer.refresh_bottom_bar() 调用，替代直接访问
        私有属性 _last_text 和 _input_cursor_pos 的模式。
        """
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def setup(self) -> None:
        """启用底部栏：设置滚动区域 + 状态初始化（不绘制）。

        终端高度不足 _MIN_HEIGHT 时静默跳过，不做任何操作。
        幂等：已激活时重复调用无效果。

        初始绘制推迟到 render 线程启动后首帧 force_redraw() 执行，
        _last_height 保留哨兵值 0 确保 layout_unchanged=False 触发全量重绘。
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
                self._last_bottom_lines = self._bottom_lines
                scroll_end = height - self._bottom_lines
                self._last_scroll_end = scroll_end
                self._last_sync_height = height
                # ★ 不设置 _last_height（保留哨兵 0），确保 render 线程
                #    首帧 force_redraw() 中 layout_unchanged=False 触发全量重绘
                self._tracker.set_scroll_end(scroll_end)
                out = sys.__stdout__
                out.write(_blessed_save_cursor())
                out.write(f"{_blessed_set_scroll_region(1, scroll_end)}")
                # ★ 不调用 _draw_all_locked()——绘制推迟到 render 线程首帧
                out.write(_blessed_restore_cursor())
                out.write(_blessed_cursor_goto(scroll_end, 1) + _blessed_save_cursor())
                out.write(_blessed_cursor_goto(height, 1))
                out.flush()
            else:
                sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
                sys.__stdout__.flush()

    def teardown(self) -> None:
        """停用底部栏：重置滚动区域为全屏 + 清理底部残留。

        使用 \0337/\0338 保存/恢复光标，确保不干扰内容区光标位置。
        幂等：未激活时重复调用无效果。
        """
        if not self._active:
            return
        self._active = False

        # ── 卸载 stdout 行追踪器 ──
        if self._tracker is not None and sys.__stdout__ is self._tracker:
            sys.__stdout__ = self._tracker._real_stdout
            self._tracker = None

        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                out.write(_blessed_reset_scroll_region())
                out.write(_blessed_save_cursor())
                height = self._term_height()
                start_row = max(1, height - self._last_bottom_lines + 1)
                for r in range(start_row, height + 1):
                    out.write(_blessed_move_clear(r))
                out.write(_blessed_restore_cursor())
                out.write(_blessed_save_cursor())
                out.flush()
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._last_height = 0
        self._last_sync_height = 0

    # ── 刷新 ──────────────────────────────────────────────

    def force_redraw(self) -> None:
        """无条件重绘全部底部栏（绕过节流和变更检测），超长文本自动拆行。

        所有共享可变状态在 output_lock 保护下更新。
        可被任何线程安全调用。

        内置快速路径：状态行文本、输入文本、底部行数三者均未变化时
        跳过全量重绘，仅更新 _last_refresh 时间戳。避免流式输出期间
        高频 CONTENT chunk 触发的冗余终端 I/O（~5 行写入→0 行）。

        ★ 性能优化：先检查文本和布局是否变化，确认需要重绘后再
        调用 _format_status()（含 shutil 系统调用），避免不必要开销。

        """
        if not self._active:
            return

        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.force_redraw", timeout=1.0) as locked:
            if not locked:
                return
            text = self._last_text
            total = self._bottom_lines

            layout_unchanged = (text == self._last_rendered_text
                                and total == self._last_bottom_lines
                                and height == self._last_height
                                and self._subagent_lines == self._last_subagent_lines)
            if layout_unchanged:
                new_status = self._format_status()
                if new_status == self._last_status:
                    self._last_refresh = time.monotonic()
                    self._last_cursor_pos = self._input_cursor_pos
                    return
            else:
                new_status = self._format_status()

            old_bottom_lines = self._last_bottom_lines
            scroll_end = height - total
            delta = total - old_bottom_lines
            # ★ 使用 _last_height 计算 old_scroll_end，否则终端高度变化时
            #    会错用当前 height 算出错误的 old_scroll_end，导致无法正确
            #    清理旧内容区域中现在属于底部栏的行。
            old_scroll_end = (self._last_height if self._last_height > 0 else height) - old_bottom_lines
            self._last_refresh = time.monotonic()
            self._last_status = new_status
            self._last_subagent_lines = list(self._subagent_lines)

            out = sys.__stdout__
            out.write(_blessed_save_cursor())

            out.write(_blessed_reset_scroll_region())

            self._last_bottom_lines = total

            # ★ 底部栏扩大时（delta > 0），在内容区做 SU 上滚以腾出空间。
            #    先临时将 DECSTBM 设为仅内容区 [1, old_scroll_end]，
            #    再 SU(delta) 将内容整体上移，底部留出空白行供底部栏使用。
            #    顶部滚出的行从终端显示消失（DECSTBM 内 SU 无 scrollback），
            #    但消息/状态在内存中保留，需要时可通过 display_messages 重显。
            if delta > 0 and old_scroll_end > 0:
                out.write(f"{_blessed_set_scroll_region(1, old_scroll_end)}")
                out.write(_blessed_cursor_goto(old_scroll_end, 1))
                out.write(f"{_blessed_scroll_up(delta)}")
                out.write(_blessed_reset_scroll_region())



            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(_blessed_move_clear(r))
                out.write(_blessed_restore_cursor())
                out.write(_blessed_cursor_goto(height, 1) + _blessed_save_cursor())
                out.flush()
                self._cursor_tracker.set(height, 1)
                self._last_cursor_pos = self._input_cursor_pos
                self._last_height = height
                return

            clear_start = max(old_scroll_end, scroll_end) + 1
            clear_end = height
            for r in range(clear_start, clear_end + 1):
                out.write(_blessed_move_clear(r))
            self._cursor_tracker.set(clear_end, 1)

            # ★ 终端高度缩小时，额外清理旧内容区中现在属于新底部栏区域的行
            #    （与 delta 符号无关），必须在画分隔线/状态行之前执行，
            #    避免擦除已绘制内容。
            if self._last_height > 0 and height < self._last_height:
                for r in range(max(scroll_end + 1, 1), min(old_scroll_end, height) + 1):
                    out.write(_blessed_move_clear(r))
                self._cursor_tracker.set(min(old_scroll_end, height), 1)

            r1 = height - total + 1
            subagent_start = r1 + 1
            r2 = subagent_start + len(self._subagent_lines)

            tw = self._term_width()
            sep_len = min(tw - 2, 40)
            sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
            out.write(_blessed_move_clear(r1) + "  " + sep)
            # ★ force_redraw 中的 tracker.set 是近似值，仅记录当前绘制行号。
            #    最终光标位置在方法末尾 set(scroll_end, 1) 处修正。
            self._cursor_tracker.set(r1, 3)  # 分隔线从第3列开始
            # ── subagent 面板行（在分隔线与状态行之间） ──
            for i, line in enumerate(self._subagent_lines):
                sr = subagent_start + i
                out.write(_blessed_move_clear(sr) + line)
            out.write(_blessed_move_clear(r2) + self._last_status)
            self._cursor_tracker.set(r2, 1)

            self._draw_input_lines_locked(out, text, r2 + 1, tw)
            input_rows = self._cached_input_rows
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(_blessed_move_clear(r))
            self._cursor_tracker.set(height, 1)

            self._last_scroll_end = scroll_end
            if self._tracker is not None:
                self._tracker.set_scroll_end(scroll_end)
            out.write(f"{_blessed_set_scroll_region(1, scroll_end)}")
            # ★ 底部栏缩小时（delta < 0），清除释放的上屏内容区域（原先
            #    被底部栏覆盖的行），后续内容渲染会自然填充该区域。
            if delta < 0 and old_scroll_end > 0:
                for r in range(old_scroll_end + 1, scroll_end + 1):
                    out.write(_blessed_move_clear(r))
                self._cursor_tracker.set(scroll_end, 1)
            out.write(_blessed_restore_cursor())
            out.write(_blessed_cursor_goto(scroll_end, 1) + _blessed_save_cursor())
            self._cursor_tracker.set(scroll_end, 1)
            out.flush()
            self._last_cursor_pos = self._input_cursor_pos
            self._last_height = height

    # ── 内部绘制 ──────────────────────────────────────────

    def _apply_scroll_delta(self, out, delta: int, old_scroll_end: int) -> None:
        """根据底部栏行数变化调整上屏内容滚动位置。

        ★ 自 2026-06-12 起 force_redraw() 不再调用此方法。
        SU 在 DECSTBM 区域内无 scrollback 缓冲，滚出顶部的行永久丢失，
        因此底部栏扩大时不再执行 SU，改为让弹窗直接覆盖底部内容区行。
        保留供将来可能的回退或替代方案使用（历史测试仍验证此方法）。

        delta > 0（底部栏扩大）：向上滚动内容腾出空间（SU）。
        delta <= 0 或 old_scroll_end < 1：无操作。

        参数:
            out: sys.__stdout__ 或等价的可写文件对象（TextIO）。
            delta: 底部栏行数变化量（新值 - 旧值）。
            old_scroll_end: 旧的 DECSTBM 滚动区域底部行号。
        """
        if delta <= 0 or old_scroll_end < 1:
            return
        out.write(_blessed_cursor_goto(old_scroll_end, 1))
        out.write(f"{_blessed_scroll_up(delta)}")

    @staticmethod
    def _reclaim_scroll_back(out, delta: int, scroll_end: int) -> None:
        """缩小后在新 DECSTBM 内下滚内容以消除空白间隙。

        ★ 自 2026-06-12 起 force_redraw() 不再调用此方法。
        SD 下滚会产生顶部空白行，回收区域直接清除即可，由新输出自然填充。
        保留供将来可能的回退或替代方案使用。

        delta < 0（底部栏缩小）：在新 DECSTBM[1;scroll_end] 内做 SD 下滚。
        回收行（旧面板区域）无实际内容（已被清除），SD 仅产生顶部空白行，
        立即清除这些空行避免上屏出现多余空白行。

        参数:
            out: sys.__stdout__ 或等价的可写文件对象。
            delta: 底部栏行数变化量（新值 - 旧值，应为负数）。
            scroll_end: 新的 DECSTBM 滚动区域底部行号。
        """
        if delta >= 0 or scroll_end < 1:
            return
        n = -delta
        out.write(_blessed_cursor_goto(scroll_end, 1))
        out.write(f"{_blessed_scroll_down(n)}")
        # 清除 SD 下滚后在滚动区顶部产生的 n 行空行
        for r in range(1, min(n, scroll_end) + 1):
            out.write(_blessed_move_clear(r))

    def _draw_input_lines_locked(self, out, text: str, r_start: int, term_width: int) -> None:
        """绘制输入行（需持有 output_lock），超长文本自动拆行。

        Args:
            out: stdout 文件对象。
            text: 输入文本（空字符串显示占位提示）。
            r_start: 第一行输入区的行号（分隔线+状态行之后）。
            term_width: 当前终端宽度（由调用方传入，避免重复系统调用）。
        """
        max_input = max(1, term_width - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        self._cached_wrapped_for = text
        self._cached_wrapped_width = max_input
        self._cached_wrapped_lines = wrapped
        base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
        self._cached_input_rows = base_rows + self._completion.height
        self._last_rendered_text = text

        # ── 补全弹窗（委托 _CompletionPopup.render） ──
        self._completion.render(out, r_start, term_width)
        popup_height = self._completion.height

        # ── 输入文本行（在弹窗下方） ──
        text_start = r_start + popup_height
        for i, segment in enumerate(wrapped):
            r = text_start + i
            if i == 0:
                if text:
                    out.write(_blessed_move_clear(r)
                              + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                              f" {segment}")
                else:
                    if self._status_active:
                        ph = _PLACEHOLDER_STREAMING
                        out.write(_blessed_move_clear(r)
                                  + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
                    else:
                        ph = _PLACEHOLDER_COMPACT if self._completion.is_visible else _PLACEHOLDER_TEXT
                        out.write(_blessed_move_clear(r)
                                  + f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
            else:
                out.write(_blessed_move_clear(r)
                          + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
            self._cursor_tracker.set(r, 3)  # 提示符从第3列开始
        # ★ 填充剩余空白行，确保输入区至少 3 行
        for r in range(text_start + len(wrapped), text_start + 3):
            out.write(_blessed_move_clear(r) + "  ")
            self._cursor_tracker.set(r, 1)

    def _draw_all_locked(self, out, height: int) -> None:
        """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

        布局（简约风）：
          第 1 行：左青右灰渐变分隔线（内容区与输入区的视觉边界）
          第 2 行：状态行（模型名·耗时·令牌数，青/灰两色）
          第 3 行起：青 ❯ <text>   （输入提示符 + 实时键入文本，超长拆行）
                     灰 · <text>    （续行，· 前缀）
                     （空输入时显示灰色占位提示）

        终端高度不足以容纳底部栏时跳过绘制。
        """
        total = self._bottom_lines
        if height - total < 1:
            return
        self._last_bottom_lines = total
        r1 = height - total + 1
        subagent_start = r1 + 1
        r2 = subagent_start + len(self._subagent_lines)

        for r in range(r1, height + 1):
            out.write(_blessed_move_clear(r))

        tw = self._term_width()
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        out.write(_blessed_cursor_goto(r1, 1) + "  " + sep)

        # ── subagent 面板行（在分隔线与状态行之间） ──
        for i, line in enumerate(self._subagent_lines):
            sr = subagent_start + i
            out.write(_blessed_move_clear(sr) + line)

        status = self._format_status()
        self._last_status = status
        if status:
            out.write(_blessed_move_clear(r2) + status)

        text = self._last_text or ""
        self._draw_input_lines_locked(out, text, r2 + 1, tw)

    # ── 补全弹窗（委托 _CompletionPopup） ──────────────────

    @property
    def is_completion_visible(self) -> bool:
        """补全弹窗是否可见。"""
        return self._completion.is_visible

    def _redraw_cycle_only(self) -> None:
        """仅重绘补全弹窗高亮变化（轻量路径，调用方须持有 output_lock）。

        与 force_redraw() 不同，此方法仅更新弹窗行的选中高亮
        和快捷键提示行，不重绘分隔线/状态行/输入区。

        由 render 线程在 CYCLE_COMPLETION 命令 handler 中调用。
        """
        if not self._completion.is_visible or not self._completion._items:
            return
        out = sys.__stdout__
        out.write(_blessed_save_cursor())
        height = self._term_height()
        total = self._bottom_lines
        popup_start = height - total + 3
        tw = self._term_width()
        self._completion.render_cycle_update(out, popup_start, tw)
        out.write(_blessed_restore_cursor())
        out.flush()
        self._last_height = height

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None,
                         start_pos: int = 0, orig_prefix: str = "",
                         title: str = "补全",
                         types: list[str] | None = None,
                         match_prefix: str = "") -> None:
        """设置补全弹窗状态并触发全量重绘。

        状态设置（仅内存）+ force_redraw() 统一终端 I/O。
        _cmplHandler 路径：额外通过 push_cmd 入队后 render 线程
        _phase_redraw_bottom 也会调用 force_redraw()（幂等，二次调用无害）。

        空间检查保留在此处——若 items 为空或 _active=False，不设置状态。
        """
        if not items or not self._active:
            return

        total_items = len(items)
        h_items = min(total_items, _CompletionPopup._COMPLETION_MAX_ITEMS)
        popup_height = h_items + 2
        max_avail = self._term_height() - 5
        if max_avail <= 0:
            return
        if popup_height > max_avail:
            h_items = max(1, max_avail - 2)
            popup_height = h_items + 2
        visible_items = items[:h_items]
        selected_idx = min(selected_idx, h_items - 1)

        self._completion._popup_height = popup_height
        self._completion._visible = True
        self._completion._title = title
        self._completion._is_selection = (title != "补全")
        self._completion._items = list(visible_items)
        self._completion._texts = list(texts) if texts is not None else list(visible_items)
        self._completion._idx = selected_idx
        self._completion._start_pos = start_pos
        self._completion._orig_prefix = orig_prefix
        self._completion._types = list(types) if types is not None else []
        self._completion._match_prefix = match_prefix

        self.force_redraw()

    def hide_completions(self) -> None:
        """清除补全弹窗状态并触发全量重绘。

        幂等：弹窗未显示时无效果。
        """
        if not self._completion.is_visible or not self._active:
            return

        self._completion._popup_height = 0
        self._completion._visible = False
        self._completion._title = "补全"
        self._completion._is_selection = False
        self._completion._items = []
        self._completion._texts = []
        self._completion._idx = 0
        self._completion._start_pos = 0
        self._completion._orig_prefix = ""
        self._completion._types = []
        self._completion._match_prefix = ""

        self.force_redraw()

    def cycle_completion(self, delta: int = 1) -> int:
        """切换补全选中项并触发轻量弹窗重绘。

        不调用 force_redraw()（全量），仅通过 _redraw_cycle_only()
        重绘弹窗行的选中高亮和快捷键提示。

        Args:
            delta: +1 下一项，-1 上一项。

        Returns:
            新的选中索引。
        """
        if not self._completion.is_visible or not self._completion._items:
            return 0
        self._completion.cycle(delta)
        with _try_acquire_output_lock(name="bottom_bar.cycle_completion", timeout=0.3) as locked:
            if locked:
                self._redraw_cycle_only()
            # ★ 拿不到锁时跳过 I/O，下次 force_redraw 会纠正视觉状态
        return self._completion._idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        return self._completion.get_selected()
