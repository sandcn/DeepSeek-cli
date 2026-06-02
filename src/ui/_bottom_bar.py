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
"""

from __future__ import annotations

import logging
import shutil
import sys
import time
from typing import Optional

from wcwidth import wcswidth

from ._bottom_bar_completion import _CompletionPopup
from ._bottom_bar_selection import run_bottom_bar_selection  # noqa: F401 — 重导出保持兼容
from ._bottom_bar_status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT  # noqa: F401 — 重导出供测试 patch
from ._bottom_bar_theme import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _BOTTOM_REFRESH_MS,
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
        self._last_scroll_end: int = 0
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
        """当前底部栏总行数（分隔线 + 状态行 + 输入行），根据输入内容动态计算。"""
        return 2 + self._compute_input_rows()

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

    # ── 终端尺寸查询 ──────────────────────────────────

    def _term_height(self) -> int:
        """获取终端高度，实时查询。"""
        _w, _h = shutil.get_terminal_size()
        return _h

    def _term_width(self) -> int:
        """获取终端宽度，实时查询。"""
        _w, _h = shutil.get_terminal_size()
        return _w

    # ── 已废弃存根（待 engine 清理后移除） ─────────────

    @property
    def is_resize_pending(self) -> bool:
        """（已禁用）始终返回 False。
        TODO: engine 清理冗余调用后移除此 property。"""
        return False

    def check_resize(self) -> bool:
        """（已禁用）始终返回 False。
        TODO: engine 清理冗余调用后移除此方法。"""
        return False

    # ── 光标定位相关 ──────────────────────────────────

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        """获取光标定位所需数据：文本、光标位置、终端高度、终端宽度。

        供 RenderEngine.position_cursor 使用，避免直接访问私有属性。
        返回值: (last_text, cursor_pos, term_height, term_width)
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
        total_bottom = max(5, 2 + self._compute_input_rows())  # 至少 2 分隔线+状态行 + 3 最少输入行
        popup_offset = self._completion.height
        r_cursor = max(1, h - total_bottom + 3 + popup_offset + vis_row)
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
        if scroll_end == self._last_scroll_end:
            return
        if scroll_end < 1:
            scroll_end = height
        self._last_scroll_end = scroll_end
        out = sys.__stdout__
        out.write(f"\033[1;{scroll_end}r")
        out.write(f"\033[{scroll_end};1H\033[s")
        out.flush()

    def ensure_cursor_in_upper(self) -> None:
        """将光标移到上屏内容区底部（滚动区域内），准备渲染内容。

        渲染内容前调用：确保 renderer 写入内容时光标在正确区域，
        避免内容误写入底部固定栏（下屏）。
        使用 _last_scroll_end 缓存值，保证光标定位与当前 DECSTBM 一致，
        避免底部行数变化（补全弹窗/输入文本变化）时光标位置偏移导致覆盖旧内容。
        终端高度过小时将光标放在最后一行。
        """
        if not self._active:
            return
        scroll_end = self._last_scroll_end
        if scroll_end < 1:
            scroll_end = self._term_height()
        sys.__stdout__.write(f"\033[{scroll_end};1H")

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
        sys.__stdout__.write(f"\033[{r_cursor};{col}H")

    # ── 生命周期 ──────────────────────────────────────────

    def setup(self) -> None:
        """启用底部栏：设置滚动区域 + 绘制初始底部栏。

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
                scroll_end = height - self._bottom_lines
                self._last_scroll_end = scroll_end
                out = sys.__stdout__
                out.write("\0337")
                out.write(f"\033[1;{scroll_end}r")
                self._draw_all_locked(out, height)
                out.write("\0338")
                out.write(f"\033[{scroll_end};1H\033[s")
                out.write(f"\033[{height};1H")
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

        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                out.write("\033[r")
                out.write("\0337")
                height = self._term_height()
                start_row = max(1, height - self._last_bottom_lines + 1)
                for r in range(start_row, height + 1):
                    out.write(f"\033[{r};1H\033[K")
                out.write("\0338")
                out.write("\033[s")
                out.flush()
        self._last_bottom_lines = _BOTTOM_MIN_LINES

    # ── 刷新 ──────────────────────────────────────────────

    def redraw(self) -> None:
        """重绘全部底部栏（不改变滚动区域），超长文本自动拆行。

        用于 prompt_toolkit 等外部组件覆盖底部栏后的恢复。
        仅在已激活时有效。

        resize 检测由 _drain_queue() Stage 0 统一处理，此方法不重复检测。
        """
        if not self._active:
            return

        with _try_acquire_output_lock(name="bottom_bar.redraw", timeout=1.0) as locked:
            if not locked:
                return
            height = self._term_height()
            total = self._bottom_lines
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines
            delta = total - old_bottom_lines
            old_scroll_end = height - old_bottom_lines
            out = sys.__stdout__
            out.write("\0337")

            self._apply_scroll_delta(out, delta, old_scroll_end)

            out.write("\033[r")

            self._last_bottom_lines = total

            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(f"\033[{r};1H\033[K")
                out.write("\0338")
                out.write(f"\033[{height};1H\033[s")
                out.flush()
                return

            if delta < 0:
                clear_start = old_scroll_end + 1
                clear_end = scroll_end
                for r in range(clear_start, clear_end + 1):
                    out.write(f"\033[{r};1H\033[K")
            else:
                clear_start = old_scroll_end + 1
                for r in range(clear_start, height + 1):
                    out.write(f"\033[{r};1H\033[K")

            self._draw_all_locked(out, height)

            self._last_scroll_end = scroll_end
            out.write(f"\033[1;{scroll_end}r")
            self._reclaim_scroll_back(out, delta, scroll_end)
            out.write("\0338")
            out.write(f"\033[{scroll_end};1H\033[s")
            out.flush()

    def force_redraw(self) -> None:
        """无条件重绘全部底部栏（绕过节流和变更检测），超长文本自动拆行。

        所有共享可变状态在 output_lock 保护下更新，与 refresh()
        串行化，消除跨线程竞争。可被任何线程安全调用。

        内置快速路径：状态行文本、输入文本、底部行数三者均未变化时
        跳过全量重绘，仅更新 _last_refresh 时间戳。避免流式输出期间
        高频 CONTENT chunk 触发的冗余终端 I/O（~5 行写入→0 行）。

        ★ 性能优化：先检查文本和布局是否变化，确认需要重绘后再
        调用 _format_status()（含 shutil 系统调用），避免不必要开销。

        resize 检测由 _drain_queue() Stage 0 统一处理，此方法不重复检测。
        """
        if not self._active:
            return

        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.force_redraw", timeout=1.0) as locked:
            if not locked:
                return
            text = self._last_text
            total = self._bottom_lines

            layout_unchanged = text == self._last_rendered_text and total == self._last_bottom_lines
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
            old_scroll_end = height - old_bottom_lines
            self._last_refresh = time.monotonic()
            self._last_status = new_status

            out = sys.__stdout__
            out.write("\0337")

            self._apply_scroll_delta(out, delta, old_scroll_end)

            out.write("\033[r")

            self._last_bottom_lines = total

            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(f"\033[{r};1H\033[K")
                out.write("\0338")
                out.write(f"\033[{height};1H\033[s")
                out.flush()
                self._last_cursor_pos = self._input_cursor_pos
                return

            if delta < 0:
                clear_start = old_scroll_end + 1
                clear_end = scroll_end
                for r in range(clear_start, clear_end + 1):
                    out.write(f"\033[{r};1H\033[K")
            else:
                clear_start = old_scroll_end + 1
                for r in range(clear_start, height + 1):
                    out.write(f"\033[{r};1H\033[K")

            r1 = height - total + 1
            r2 = r1 + 1

            tw = self._term_width()
            sep_len = min(tw - 2, 40)
            sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
            out.write(f"\033[{r1};1H  {sep}")
            out.write(f"\033[{r2};1H\033[K{self._last_status}")

            self._draw_input_lines_locked(out, text, r2 + 1, tw)
            input_rows = self._cached_input_rows
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(f"\033[{r};1H\033[K")

            self._last_scroll_end = scroll_end
            out.write(f"\033[1;{scroll_end}r")
            self._reclaim_scroll_back(out, delta, scroll_end)
            out.write("\0338")
            out.write(f"\033[{scroll_end};1H\033[s")
            out.flush()
            self._last_cursor_pos = self._input_cursor_pos

    def refresh(self, text: str, cursor_pos: int = -1) -> None:
        """刷新底部栏全部行（分隔线/状态行/输入行一起刷新），超长文本自动拆行。

        节流 50ms：高频键入时合并刷新，减少锁竞争。

        resize 检测由 _drain_queue() Stage 0 统一处理，此方法不重复检测。

        Args:
            text: 当前输入文本（空字符串则只显示 > 提示符）。
            cursor_pos: 光标在输入文本中的偏移（0=第一个字符后），
                        -1=不定位光标（放在文本末尾）。
        """
        if not self._active:
            return

        now = time.monotonic()
        text_changed = text != self._last_text
        cursor_changed = cursor_pos >= 0 and cursor_pos != self._last_cursor_pos
        if not text_changed and not cursor_changed and now - self._last_refresh < _BOTTOM_REFRESH_MS:
            return

        new_status = self._format_status()
        status_changed = new_status != self._last_status

        if not text_changed and not status_changed and not cursor_changed:
            return

        # ── 轻量光标路径 ──
        if not text_changed and not status_changed and cursor_changed:
            self._last_cursor_pos = cursor_pos
            self._input_cursor_pos = cursor_pos
            term_w = self._term_width()
            term_h = self._term_height()
            max_input = max(1, term_w - 4)
            vis_row, vis_col = self._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
            total = (2 + self._cached_input_rows
                     if (self._cached_wrapped_for == text
                         and self._cached_wrapped_width == max_input)
                     else self._bottom_lines)
            r_cursor = term_h - total + 3 + self._completion.height + vis_row
            r_cursor = max(1, min(r_cursor, term_h))
            cursor_col = min(3 + vis_col, term_w)
            sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
            sys.__stdout__.flush()
            return

        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.refresh", timeout=1.0) as locked:
            if not locked:
                return
            if text_changed:
                self._last_text = text
                self._input_cursor_pos = cursor_pos
                self._last_refresh = now
            total = self._bottom_lines
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines
            delta = total - old_bottom_lines
            old_scroll_end = height - old_bottom_lines
            if status_changed:
                self._last_status = new_status
            if cursor_pos >= 0:
                if self._input_cursor_pos != cursor_pos:
                    _logger.debug(
                        "_input_cursor_pos 更新: %d→%d, "
                        "text_changed=%s, status_changed=%s",
                        self._input_cursor_pos, cursor_pos,
                        text_changed, status_changed,
                    )
                self._input_cursor_pos = cursor_pos
            self._last_cursor_pos = self._input_cursor_pos
            out = sys.__stdout__
            out.write("\0337")

            self._apply_scroll_delta(out, delta, old_scroll_end)

            out.write("\033[r")

            self._last_bottom_lines = total

            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(f"\033[{r};1H\033[K")
                out.write("\0338")
                out.write(f"\033[{height};1H\033[s")
                out.write(f"\033[{height};1H")
                out.flush()
                return

            if delta < 0:
                clear_start = old_scroll_end + 1
                clear_end = scroll_end
                for r in range(clear_start, clear_end + 1):
                    out.write(f"\033[{r};1H\033[K")
            else:
                clear_start = old_scroll_end + 1
                for r in range(clear_start, height + 1):
                    out.write(f"\033[{r};1H\033[K")

            r1 = height - total + 1
            tw = self._term_width()
            sep_len = min(tw - 2, 40)
            sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
            out.write(f"\033[{r1};1H  {sep}")

            r2 = r1 + 1
            out.write(f"\033[{r2};1H\033[K{new_status}")

            self._draw_input_lines_locked(out, text, r2 + 1, tw)
            input_rows = self._cached_input_rows
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(f"\033[{r};1H\033[K")

            vis_row, vis_col = _compute_cursor_visual_pos(
                text, cursor_pos, max(1, tw - 4),
            )
            r_cursor = r2 + 1 + self._completion.height + vis_row
            r_cursor = max(1, min(r_cursor, height))
            cursor_col = 3 + vis_col
            cursor_col = min(cursor_col, self._term_width())

            self._last_scroll_end = scroll_end
            out.write(f"\033[1;{scroll_end}r")
            self._reclaim_scroll_back(out, delta, scroll_end)
            out.write("\0338")
            out.write(f"\033[{scroll_end};1H\033[s")
            out.write(f"\033[{r_cursor};{cursor_col}H")
            out.flush()

    # ── 内部绘制 ──────────────────────────────────────────

    def _apply_scroll_delta(self, out, delta: int, old_scroll_end: int) -> None:
        """根据底部栏行数变化调整上屏内容滚动位置。

        delta > 0（底部栏扩大）：向上滚动内容腾出空间（SU），避免面板覆盖上屏。
        delta <= 0 或 old_scroll_end < 1：无操作。

        参数:
            out: sys.__stdout__ 或等价的可写文件对象（TextIO）。
            delta: 底部栏行数变化量（新值 - 旧值）。
            old_scroll_end: 旧的 DECSTBM 滚动区域底部行号。
        """
        if delta <= 0 or old_scroll_end < 1:
            return
        out.write(f"\033[{old_scroll_end};1H")
        out.write(f"\033[{delta}S")

    @staticmethod
    def _reclaim_scroll_back(out, delta: int, scroll_end: int) -> None:
        """缩小后在新 DECSTBM 内下滚内容以消除空白间隙。

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
        out.write(f"\033[{scroll_end};1H")
        out.write(f"\033[{n}T")
        # 清除 SD 下滚后在滚动区顶部产生的 n 行空行
        for r in range(1, min(n, scroll_end) + 1):
            out.write(f"\033[{r};1H\033[K")

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
                    out.write(f"\033[{r};1H\033[K"
                              f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                              f" {segment}")
                else:
                    if self._status_active:
                        ph = _PLACEHOLDER_STREAMING
                        out.write(f"\033[{r};1H\033[K"
                                  f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
                    else:
                        ph = _PLACEHOLDER_COMPACT if self._completion.is_visible else _PLACEHOLDER_TEXT
                        out.write(f"\033[{r};1H\033[K"
                                  f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET}"
                                  f" {_COLOR_DIM}{ph}{_COLOR_RESET}")
            else:
                out.write(f"\033[{r};1H\033[K{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")
        # ★ 填充剩余空白行，确保输入区至少 3 行
        for r in range(text_start + len(wrapped), text_start + 3):
            out.write(f"\033[{r};1H\033[K  ")

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
            out.write(f"\033[{r};1H\033[K")

        tw = self._term_width()
        sep_len = min(tw - 2, 40)
        sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
        out.write(f"\033[{r1};1H  {sep}")

        status = self._format_status()
        self._last_status = status
        if status:
            out.write(f"\033[{r2};1H\033[K{status}")

        text = self._last_text or ""
        self._draw_input_lines_locked(out, text, r2 + 1, tw)

    def _draw_status_locked(self, out, height: int) -> None:
        """仅重绘状态行（需持有 output_lock，在 \033[r 之后调用）。"""
        status = self._format_status()
        if status == self._last_status:
            return
        self._last_status = status
        total = self._bottom_lines
        status_row = height - total + 2
        out.write(f"\033[{status_row};1H\033[K{status}")

    def refresh_status_only(self) -> None:
        """仅刷新状态行（reader 线程 drain 后调用，10Hz）。

        始终刷新（包含模型名），不再以 _status_active 为门控。
        终端高度不足以容纳底部栏时跳过。
        """
        if not self._active:
            return
        height = self._term_height()

        with _try_acquire_output_lock(name="bottom_bar.status", timeout=1.0) as locked:
            if not locked:
                return
            scroll_end = height - self._bottom_lines
            out = sys.__stdout__
            if scroll_end < 1:
                return
            out.write("\0337")
            out.write("\033[r")
            self._draw_status_locked(out, height)
            self._last_scroll_end = scroll_end
            out.write(f"\033[1;{scroll_end}r")
            out.write("\0338")
            out.write(f"\033[{scroll_end};1H\033[s")
            out.flush()
            self._last_refresh = time.monotonic()

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
            out.write("\0337")
            height = self._term_height()
            total = self._bottom_lines
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines
            delta = total - old_bottom_lines
            old_scroll_end = height - old_bottom_lines

            self._apply_scroll_delta(out, delta, old_scroll_end)

            out.write("\033[r")

            self._last_bottom_lines = total

            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(f"\033[{r};1H\033[K")
                out.write("\0338")
                out.write(f"\033[{height};1H\033[s")
                out.flush()
                return

            # 修复：delta > 0 时显式清除旧滚动区与新滚动区之间的残留行
            if delta > 0 and scroll_end >= 1:
                for r in range(scroll_end + 1, min(old_scroll_end, height) + 1):
                    out.write(f"\033[{r};1H\033[K")

            if delta < 0:
                clear_start = old_scroll_end + 1
                clear_end = scroll_end
                for r in range(clear_start, clear_end + 1):
                    out.write(f"\033[{r};1H\033[K")
            else:
                clear_start = old_scroll_end + 1
                for r in range(clear_start, height + 1):
                    out.write(f"\033[{r};1H\033[K")

            r1 = height - total + 1
            r2 = r1 + 1
            tw_s = self._term_width()
            sep_len_s = min(tw_s - 2, 40)
            sep_s = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len_s
            out.write(f"\033[{r1};1H  {sep_s}")
            out.write(f"\033[{r2};1H\033[K")
            text = self._last_text or ""
            self._draw_input_lines_locked(out, text, r2 + 1, tw_s)
            status = self._format_status()
            if status:
                out.write(f"\033[{r2};1H\033[K{status}")
            self._last_status = status

            self._last_scroll_end = scroll_end
            out.write(f"\033[1;{scroll_end}r")
            self._reclaim_scroll_back(out, delta, scroll_end)
            out.write("\0338")
            out.write(f"\033[{scroll_end};1H\033[s")
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, self._input_cursor_pos, max(1, self._term_width() - 4),
            )
            r_cursor = r2 + 1 + self._completion.height + vis_row
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(f"\033[{r_cursor};{cursor_col}H")
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
            out.write("\0337")
            height = self._term_height()
            total = self._bottom_lines
            scroll_end = height - total
            old_bottom_lines = self._last_bottom_lines
            delta = total - old_bottom_lines
            old_scroll_end = height - old_bottom_lines

            self._apply_scroll_delta(out, delta, old_scroll_end)

            out.write("\033[r")

            self._last_bottom_lines = total

            if scroll_end < 1:
                for r in range(1, height + 1):
                    out.write(f"\033[{r};1H\033[K")
                out.write("\0338")
                out.write(f"\033[{height};1H\033[s")
                out.flush()
                return

            if delta < 0:
                clear_start = old_scroll_end + 1
                clear_end = scroll_end
                for r in range(clear_start, clear_end + 1):
                    out.write(f"\033[{r};1H\033[K")
            else:
                clear_start = old_scroll_end + 1
                for r in range(clear_start, height + 1):
                    out.write(f"\033[{r};1H\033[K")

            r1 = height - total + 1
            r2 = r1 + 1
            tw_h = self._term_width()
            sep_len_h = min(tw_h - 2, 40)
            sep_h = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len_h
            out.write(f"\033[{r1};1H  {sep_h}")
            out.write(f"\033[{r2};1H\033[K")
            text = self._last_text or ""
            self._draw_input_lines_locked(out, text, r2 + 1, tw_h)
            status = self._format_status()
            if status:
                out.write(f"\033[{r2};1H\033[K{status}")
            self._last_status = status

            self._last_scroll_end = scroll_end
            out.write(f"\033[1;{scroll_end}r")
            self._reclaim_scroll_back(out, delta, scroll_end)
            out.write("\0338")
            out.write(f"\033[{scroll_end};1H\033[s")
            vis_row, vis_col = _compute_cursor_visual_pos(
                text, self._input_cursor_pos, max(1, self._term_width() - 4),
            )
            r_cursor = r2 + 1 + vis_row
            cursor_col = min(3 + vis_col, self._term_width())
            out.write(f"\033[{r_cursor};{cursor_col}H")
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
            out.write("\0337")
            height = self._term_height()
            total = self._bottom_lines
            popup_start = height - total + 3
            tw = self._term_width()

            self._completion.render_cycle_update(out, popup_start, tw)

            out.write("\0338")
            scroll_end = height - self._bottom_lines
            if scroll_end >= 1:
                out.write(f"\033[{scroll_end};1H\033[s")
                vis_row, vis_col = _compute_cursor_visual_pos(
                    self._last_text if self._last_text else "", self._input_cursor_pos,
                    max(1, self._term_width() - 4),
                )
                r_cursor = height - total + 3 + self._completion.height + vis_row
                r_cursor = max(1, min(r_cursor, height))
                cursor_col = min(3 + vis_col, self._term_width())
                out.write(f"\033[{r_cursor};{cursor_col}H")
            out.flush()

        return self._completion._idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。

        Returns:
            (replacement_text, start_pos, orig_prefix) 三元组。
        """
        return self._completion.get_selected()
