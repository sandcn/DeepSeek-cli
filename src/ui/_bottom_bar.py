"""_BottomBar — 流式输出期间固定底部输入栏（动态拆行）。

底部栏通过"清除旧区域 → 命令渲染 → 重绘底部栏"的简化流水线实现：
先清除旧底部栏区域，再渲染命令输出，最后在底部重绘分隔线+状态行+输入区。

线程安全（分两级）：
  - 内容变更全量重绘（文本/状态/尺寸变化）→ output_lock 串行化
  - 纯光标移动轻量路径 → 无锁直写 ANSI 序列（GIL + 幂等性保证安全）

拆分为多个子模块：
  - _bottom_bar_theme    — ANSI 颜色常量 + 占位符 + 布局配置
  - _bottom_bar_status   — 状态行格式化 + 工具计数（_StatusMixin）
  - _bottom_bar_completion — 补全弹窗（_CompletionPopup 独立类）
  - _bottom_bar_selection  — run_bottom_bar_selection() 交互选择

终端控制策略：
  - 光标定位、清行使用 Blessed Terminal
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
from ._lock import _try_acquire_output_lock


# ── Blessed 辅助函数 ─────────────────────────────────────
# 将常用的 ANSI 序列（光标定位、清行、保存/恢复光标）封装为
# Blessed 调用，带 try/except 回退到原始 ANSI。


def _blessed_move_clear(row: int) -> str:
    """生成移到指定行并清行的 ANSI 序列。

    通过 Blessed Terminal.move_xy + clear_eol 生成，
    Blessed 不可用时回退到原始 ANSI。

    Args:
        row: 1-based 行号。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        return term.move_xy(0, row - 1) + term.clear_eol()
    except Exception:
        return f"\033[{row};1H\033[K"


def _blessed_cursor_goto(row: int, col: int) -> str:
    """生成移到指定行列的 ANSI 序列。

    通过 Blessed Terminal.move_xy 生成。
    Blessed 使用 0-based 坐标。

    Args:
        row: 1-based 行号。
        col: 1-based 列号。

    Returns:
        ANSI 序列字符串。
    """
    try:
        term = get_terminal()
        return term.move_xy(col - 1, row - 1)
    except Exception:
        return f"\033[{row};{col}H"

# ── Blessed 光标保存/恢复辅助函数 ─────────────────────
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





_logger = logging.getLogger(__name__)


class _BottomBar(_StatusMixin):
    """终端底部固定输入栏，流式输出期间始终可见。

    通过"清除旧区域 → 命令渲染 → 重绘底部栏"的简化流水线实现：
    先清除旧底部栏区域，再渲染命令输出，最后在底部重绘分隔线+状态行+输入区。

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

    def __init__(self):
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
        # ── 布局/光标 ──
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._cached_wrapped_for: str = ""
        self._cached_wrapped_width: int = 0
        self._cached_wrapped_lines: list[str] | None = None
        self._cached_input_rows: int = _MIN_INPUT_ROWS
        self._last_rendered_text: str = ""
        self._last_height: int = 0  # 哨兵值，首次 force_redraw() 必然触发全量重绘（终端高度始终 ≥1）
        # ── 补全弹窗组合对象 ──
        self._completion = _CompletionPopup()

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
        """当前底部栏总行数（分隔线 + 状态行 + 输入行 + 1 行余量），根据输入内容动态计算。"""
        return 2 + self._compute_input_rows() + 1

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

    def get_bottom_start(self) -> int:
        """获取底部栏起始行号（1-based），即内容区与底部栏的分界线。

        供 ParallelDisplay 等外部模块计算面板定位使用。
        基于 _last_height 和 _bottom_lines 计算，不可用时返回 0。
        """
        if self._last_height < 1:
            return 0
        return self._last_height - self._bottom_lines + 1

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
        total_bottom = self._bottom_lines  # 与 force_redraw 布局一致（含 +1 余量）
        popup_offset = self._completion.height
        r_cursor = max(1, h - total_bottom + 3 + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    def clear_old_bottom(self) -> None:
        """清除旧底部栏区域（在渲染命令前调用，释放空间）。

        基于 _last_height 和 _last_bottom_lines 计算旧底部栏位置，
        逐行清除后光标停在旧底部栏起始行，准备渲染命令输出。

        调用方须持有 output_lock。
        """
        if not self._active:
            return
        old_start = self._last_height - self._last_bottom_lines + 1
        if old_start < 1:
            old_start = 1
        out = sys.__stdout__
        for r in range(old_start, self._last_height + 1):
            out.write(_blessed_move_clear(r))
        # 光标停在清除区域起始行，准备渲染命令
        out.write(_blessed_cursor_goto(old_start, 1))
        out.flush()

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

    def ensure_cursor_in_lower(self) -> None:
        """渲染完成后将光标移回下屏输入行末尾（含动态拆行，最少3行输入区）。

        只做光标跳转，不重绘输入行（避免覆盖用户通过
        左右键移动光标后的位置）。光标停在输入文本末尾。
        超长文本会自动拆行，光标位于最后一行末尾。
        空输入时光标位于输入区第一行（> 提示符行）。
        制表符按内部默认宽度展开为空格。
        终端高度过小时将光标放在最后一行。
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
        r_cursor = height - total + 3 + self._completion.height + vis_row
        r_cursor = max(1, min(r_cursor, height))
        col = min(3 + vis_col, term_w)
        sys.__stdout__.write(_blessed_cursor_goto(r_cursor, col))

    # ── 生命周期 ──────────────────────────────────────────

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        """设置输入文本和光标位置（线程安全，仅更新状态，不直接 I/O）。

        由 ChatUIConsumer.refresh_bottom_bar() 调用，替代直接访问
        私有属性 _last_text 和 _input_cursor_pos 的模式。
        """
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def setup(self) -> None:
        """启用底部栏：绘制初始底部栏。

        终端高度不足 _MIN_HEIGHT 时静默跳过，不做任何操作。
        幂等：已激活时重复调用无效果。
        """
        if self._active:
            return
        height = self._term_height()
        if height < self._MIN_HEIGHT:
            return
        self._active = True

        with _try_acquire_output_lock(name="bottom_bar.setup", timeout=1.0) as locked:
            if locked:
                self._last_text = ""
                self._last_bottom_lines = self._bottom_lines
                self._last_height = height
                out = sys.__stdout__
                self._draw_all_locked(out, height)
                out.write(_blessed_cursor_goto(height, 1))
                out.flush()
            else:
                sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
                sys.__stdout__.flush()

    def teardown(self) -> None:
        """停用底部栏：清理底部残留。

        使用 \0337/\0338 保存/恢复光标，确保不干扰内容区光标位置。
        幂等：未激活时重复调用无效果。
        """
        if not self._active:
            return
        self._active = False

        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
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

    # ── 刷新 ──────────────────────────────────────────────

    def force_redraw(self, from_content_end: bool = False) -> None:
        """无条件重绘全部底部栏（绕过节流和变更检测），超长文本自动拆行。

        所有共享可变状态在 output_lock 保护下更新。
        可被任何线程安全调用。

        内置快速路径：状态行文本、输入文本、底部行数三者均未变化时
        跳过全量重绘，仅更新 _last_refresh 时间戳。

        Args:
            from_content_end: True 时从命令输出末尾的下一行开始绘制
                              （\\0338 恢复 _phase_render 中保存的光标），
                              避免底部栏覆盖思考/回答/工具输出。
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
                                and height == self._last_height)
            if layout_unchanged:
                new_status = self._format_status()
                if new_status == self._last_status:
                    self._last_refresh = time.monotonic()
                    self._last_cursor_pos = self._input_cursor_pos
                    return
            else:
                new_status = self._format_status()

            self._last_refresh = time.monotonic()
            self._last_status = new_status
            self._last_bottom_lines = total

            out = sys.__stdout__

            if from_content_end:
                # ★ 恢复 _phase_render 中保存的光标（命令输出末尾），
                #    清除整行 + 屏幕末尾，直接绘制底部栏。
                #    使用相对定位（\\n 换行），避免覆盖命令输出。
                out.write("\0338")
                out.write("\r\033[K")  # 回行首 + 清除当前行
                out.write("\033[J")    # 清除光标到屏幕末尾

                tw = self._term_width()
                sep_len = min(tw - 2, 40)
                sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len

                # 分隔线
                out.write("  " + sep + "\n")
                # 状态行
                out.write(self._last_status + "\n")
                # 输入区（相对绘制）
                self._draw_input_lines_relative(out, text, tw)
                # 清除底部残留
                out.write("\033[J")
            else:
                # 无命令渲染（纯输入刷新）：清除旧底部栏 + 从终端底部定位绘制
                old_bottom_start = self._last_height - self._last_bottom_lines + 1
                if old_bottom_start < 1:
                    old_bottom_start = 1
                for r in range(old_bottom_start, self._last_height + 1):
                    out.write(_blessed_move_clear(r))

                # 终端高度太小时清全屏
                if height - total < 1:
                    for r in range(1, height + 1):
                        out.write(_blessed_move_clear(r))
                    out.write(_blessed_cursor_goto(height, 1))
                    out.flush()
                    self._last_cursor_pos = self._input_cursor_pos
                    self._last_height = height
                    return

                r1 = height - total + 1
                r2 = r1 + 1
                tw = self._term_width()
                sep_len = min(tw - 2, 40)
                sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
                out.write(_blessed_move_clear(r1) + "  " + sep)
                out.write(_blessed_move_clear(r2) + self._last_status)
                self._draw_input_lines_locked(out, text, r2 + 1, tw)
                input_rows = self._cached_input_rows
                for r in range(r2 + 1 + input_rows, height + 1):
                    out.write(_blessed_move_clear(r))

            out.flush()
            self._last_cursor_pos = self._input_cursor_pos
            self._last_height = height

    def _draw_input_lines_relative(self, out, text: str, term_width: int) -> None:
        """相对定位绘制输入行（from_content_end 路径），用 \\n 换行。

        与 _draw_input_lines_locked 的区别：不使用绝对行号定位，
        适用于光标已在目标位置、只需向下输出的场景。
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

        for i, segment in enumerate(wrapped):
            if i == 0:
                if text:
                    out.write(f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET} {segment}")
                else:
                    if self._status_active:
                        ph = _PLACEHOLDER_STREAMING
                        out.write(f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET} {_COLOR_DIM}{ph}{_COLOR_RESET}")
                    else:
                        ph = _PLACEHOLDER_COMPACT if self._completion.is_visible else _PLACEHOLDER_TEXT
                        out.write(f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET} {_COLOR_DIM}{ph}{_COLOR_RESET}")
            else:
                out.write(f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
            out.write("\n")

        # 填充最少 3 行输入区
        text_start_lines = len(wrapped) if wrapped else 1
        for _ in range(text_start_lines, 3):
            out.write("  \n")

    # ── 内部绘制 ──────────────────────────────────────────

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
        # ★ 填充剩余空白行，确保输入区至少 3 行
        for r in range(text_start + len(wrapped), text_start + 3):
            out.write(_blessed_move_clear(r) + "  ")

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
        r2 = r1 + 1

        for r in range(r1, height + 1):
            out.write(_blessed_move_clear(r))

        tw = self._term_width()
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        out.write(_blessed_cursor_goto(r1, 1) + "  " + sep)

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

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None,
                         start_pos: int = 0, orig_prefix: str = "",
                         title: str = "补全") -> None:
        """在输入区内部绘制无边框扁平补全弹窗，自动扩大输入区域。

        弹窗视觉（无边框扁平样式）：
          {title} (N项)            ← 标题行
            ▶ 选中项              ← ▶ 指示器 + 高亮背景
              普通项              ← 缩进对齐
          ↑↓/Enter/Esc            ← 快捷键提示

        弹窗作为输入区的一部分绘制，弹出时输入区域自动扩大。

        Args:
            items: 显示文本列表。
            selected_idx: 初始选中索引。
            texts: 替换文本列表（与 items 一一对应），默认同 items。
            start_pos: 替换起始位置（相对光标）。
            orig_prefix: 原始前缀。
            title: 弹窗标题前缀（如"补全"、"选择"），显示在标题行。
        """
        if not items or not self._active:
            return

        # ★ 空间检查（基于终端总高度）
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

        with _try_acquire_output_lock(name="bottom_bar.comp_show", timeout=1.0) as locked:
            if not locked:
                return

            # 设置弹窗状态
            self._completion._popup_height = popup_height
            self._completion._visible = True
            self._completion._title = title
            self._completion._is_selection = (title != "补全")
            self._completion._items = list(visible_items)
            self._completion._texts = list(texts) if texts is not None else list(visible_items)
            self._completion._idx = selected_idx
            self._completion._start_pos = start_pos
            self._completion._orig_prefix = orig_prefix

            out = sys.__stdout__
            height = self._term_height()
            total = self._bottom_lines

            # 清除旧底部栏区域
            old_bottom_start = self._last_height - self._last_bottom_lines + 1
            if old_bottom_start < 1:
                old_bottom_start = 1
            for r in range(old_bottom_start, self._last_height + 1):
                out.write(_blessed_move_clear(r))

            self._last_bottom_lines = total

            if height - total < 1:
                for r in range(1, height + 1):
                    out.write(_blessed_move_clear(r))
                out.write(_blessed_cursor_goto(height, 1))
                self._last_height = height
                out.flush()
                return

            r1 = height - total + 1
            r2 = r1 + 1
            tw_s = self._term_width()
            sep_len_s = min(tw_s - 2, 40)
            sep_s = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len_s
            out.write(_blessed_move_clear(r1) + "  " + sep_s)
            out.write(_blessed_move_clear(r2))
            text = self._last_text or ""
            self._draw_input_lines_locked(out, text, r2 + 1, tw_s)
            status = self._format_status()
            if status:
                out.write(_blessed_move_clear(r2) + status)
            self._last_status = status

            # 光标定位
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, self._input_cursor_pos, max(1, self._term_width() - 4),
            )
            r_cursor = r2 + 1 + self._completion.height + vis_row
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(_blessed_cursor_goto(r_cursor, cursor_col))
            self._last_height = height
            out.flush()

    def hide_completions(self) -> None:
        """清除补全弹窗，缩小输入区域恢复原状。

        幂等：弹窗未显示时无效果。
        """
        if not self._completion.is_visible or not self._active:
            return

        with _try_acquire_output_lock(name="bottom_bar.comp_hide", timeout=1.0) as locked:
            if not locked:
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

            out = sys.__stdout__
            height = self._term_height()
            total = self._bottom_lines

            # 清除旧底部栏区域
            old_bottom_start = self._last_height - self._last_bottom_lines + 1
            if old_bottom_start < 1:
                old_bottom_start = 1
            for r in range(old_bottom_start, self._last_height + 1):
                out.write(_blessed_move_clear(r))

            self._last_bottom_lines = total

            if height - total < 1:
                for r in range(1, height + 1):
                    out.write(_blessed_move_clear(r))
                out.write(_blessed_cursor_goto(height, 1))
                self._last_height = height
                out.flush()
                return

            r1 = height - total + 1
            r2 = r1 + 1
            tw_h = self._term_width()
            sep_len_h = min(tw_h - 2, 40)
            sep_h = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len_h
            out.write(_blessed_move_clear(r1) + "  " + sep_h)
            out.write(_blessed_move_clear(r2))
            text = self._last_text or ""
            self._draw_input_lines_locked(out, text, r2 + 1, tw_h)
            status = self._format_status()
            if status:
                out.write(_blessed_move_clear(r2) + status)
            self._last_status = status

            # 光标定位
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, self._input_cursor_pos, max(1, self._term_width() - 4),
            )
            r_cursor = r2 + 1 + vis_row
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(_blessed_cursor_goto(r_cursor, cursor_col))
            self._last_height = height
            out.flush()

    def cycle_completion(self, delta: int = 1) -> int:
        """循环切换补全选中项，更新弹窗高亮和 footer 位置信息。

        Args:
            delta: +1 下一项，-1 上一项。

        Returns:
            新的选中索引。
        """
        if not self._completion.is_visible or not self._completion._items:
            return 0

        self._completion.cycle(delta)

        with _try_acquire_output_lock(name="bottom_bar.comp_cycle", timeout=1.0) as locked:
            if not locked:
                return self._completion._idx
            out = sys.__stdout__
            out.write(_blessed_save_cursor())
            height = self._term_height()
            total = self._bottom_lines
            popup_start = height - total + 3
            tw = self._term_width()

            self._completion.render_cycle_update(out, popup_start, tw)

            out.write(_blessed_restore_cursor())
            vis_row, vis_col = _compute_cursor_visual_pos(
                self._last_text if self._last_text else "", self._input_cursor_pos,
                max(1, self._term_width() - 4),
            )
            r_cursor = height - total + 3 + self._completion.height + vis_row
            r_cursor = max(1, min(r_cursor, height))
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(_blessed_cursor_goto(r_cursor, cursor_col))
            self._last_height = height
            out.flush()

        return self._completion._idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        return self._completion.get_selected()
