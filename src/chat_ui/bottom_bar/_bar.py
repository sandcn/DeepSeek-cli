"""_BottomBar — 流式输出期间固定底部输入栏（动态拆行）。

在终端底部使用 ANSI DECSTBM 滚动区域创建固定区域：
上方内容区正常滚动，底部固定显示（分隔线 + 状态行 + 输入区）。

线程安全（分两级）：
  - 内容变更全量重绘（文本/状态/尺寸变化）→ output_lock 串行化
  - 纯光标移动轻量路径 → 无锁直写 ANSI 序列（GIL + 幂等性保证安全）

拆分为多个子模块：
  - _theme              — ANSI 颜色常量 + 占位符 + 布局配置
  - _status             — 状态行格式化 + 工具计数（_StatusMixin）
  - _completion_popup   — 补全弹窗（_CompletionPopup 独立类）
  - _bottom_bar_selection — run_bottom_bar_selection() 交互选择（仍在 ui/ 层）

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

from ...ui._blessed import get_terminal
from ._completion_popup import _CompletionPopup
from ...ui._bottom_bar_selection import run_bottom_bar_selection  # noqa: F401 — 重导出保持兼容
from ._status import _StatusMixin, _get_snapshot, _TOKEN_SPEED_SNAPSHOT  # noqa: F401 — 重导出供测试 patch
from ._stdout_tracker import _StdoutLineTracker
from ._theme import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _COLOR_DEEP_CYAN,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _MIN_INPUT_ROWS,
    _PLACEHOLDER_TEXT,
)
from ..infrastructure.cursor_tracker import CursorTracker
from ...ui._lock import _try_acquire_output_lock

# ── 新模块导入（BottomBar 拆解） ──
from ._scroll_region import (
    ScrollRegionManager,
    blessed_save_cursor,
    blessed_restore_cursor,
    blessed_set_scroll_region,
    blessed_reset_scroll_region,
    blessed_move_clear,
    blessed_cursor_goto,
    blessed_scroll_up,
    blessed_scroll_down,
    _term_height as _sr_term_height,
    _term_width as _sr_term_width,
)
from ._input_renderer import InputRenderer

# ── 兼容别名（旧 _blessed_* 函数仍可从 _bottom_bar 导入） ──
_blessed_move_clear = blessed_move_clear
_blessed_cursor_goto = blessed_cursor_goto
_blessed_save_cursor = blessed_save_cursor
_blessed_restore_cursor = blessed_restore_cursor
_blessed_scroll_up = blessed_scroll_up
_blessed_scroll_down = blessed_scroll_down
_blessed_set_scroll_region = blessed_set_scroll_region
_blessed_reset_scroll_region = blessed_reset_scroll_region

# ── 显式 __all__ 以确保 `from ... import *` 正确重导出 _ 前缀名称 ──
__all__ = [
    "_BottomBar",
    "_CompletionPopup",
    "_StatusMixin",
    "_get_snapshot",
    "_TOKEN_SPEED_SNAPSHOT",
    "run_bottom_bar_selection",
    "ScrollRegionManager",
    "InputRenderer",
    "get_terminal",
    "blessed_save_cursor",
    "blessed_restore_cursor",
    "blessed_set_scroll_region",
    "blessed_reset_scroll_region",
    "blessed_move_clear",
    "blessed_cursor_goto",
    "blessed_scroll_up",
    "blessed_scroll_down",
    "_blessed_move_clear",
    "_blessed_cursor_goto",
    "_blessed_save_cursor",
    "_blessed_restore_cursor",
    "_blessed_scroll_up",
    "_blessed_scroll_down",
    "_blessed_set_scroll_region",
    "_blessed_reset_scroll_region",
]

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
        # ── 布局/光标 ──
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._last_scroll_end: int = 0
        self._last_height: int = 0  # 哨兵值，首次 force_redraw() 必然触发全量重绘（终端高度始终 ≥1）
        self._last_sync_height: int = 0  # sync_bottom_lines() 中用于检测终端 resize
        # ── 补全弹窗组合对象 ──
        self._completion = _CompletionPopup(cursor_tracker=cursor_tracker)
        # ── stdout 行追踪器 ──
        self._tracker: _StdoutLineTracker | None = None
        # ── 光标坐标追踪器（全局共享实例） ──
        self._cursor_tracker = cursor_tracker or CursorTracker()
        # ── 新拆解模块（Phase 2） ──
        self._scroll = ScrollRegionManager(self._cursor_tracker)
        self._input = InputRenderer()

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
        return self._input.bottom_lines(
            self._last_text or "", self._term_width(), self._completion.height,
        )

    def _compute_input_rows(self) -> int:
        """根据当前输入文本计算所需的输入行数（最少 3 行 + 补全弹窗高度）。"""
        return self._input.compute_input_rows(
            self._last_text or "", self._term_width(), self._completion.height,
        )

    # ── 终端尺寸查询（通过 Blessed Terminal） ──────────

    def _term_height(self) -> int:
        return _sr_term_height()

    def _term_width(self) -> int:
        return _sr_term_width()

    # ── 光标定位相关 ──────────────────────────────────

    def get_scroll_end(self) -> int:
        """获取当前滚动区域底部行号（1-based）。

        供 RenderEngine 在 resize 处理时保存旧 scroll_end 使用。
        """
        return self._last_scroll_end

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        """获取光标定位所需数据：文本、光标位置、终端高度、终端宽度。

        供 RenderEngine.position_cursor 使用，避免直接访问私有属性。
        """
        return self._input.get_cursor_info(
            self._last_text,
            self._input_cursor_pos,
            self._term_height(),
            self._term_width(),
        )

    def compute_cursor_position(
        self, text: str, cursor_pos: int, h: int, w: int,
    ) -> tuple[int, int]:
        """计算光标在底部栏中的终端行号和列号（公开 API）。

        委托给 InputRenderer.compute_cursor_position。
        """
        return self._input.compute_cursor_position(
            text, cursor_pos, h, w, self._completion.height,
        )

    def _cursor_visual_pos_from_cache(
        self, text: str, cursor_pos: int, max_width: int,
    ) -> tuple[int, int]:
        """从缓存的拆行结果计算光标视觉位置。委托给 InputRenderer。"""
        return self._input._cursor_visual_pos_from_cache(text, cursor_pos, max_width)

    def sync_bottom_lines(self) -> None:
        """同步当前 DECSTBM 滚动区域与 _bottom_lines 缓存值。

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
        """将光标移回下屏输入行末尾。委托给 ScrollRegionManager。"""
        self._scroll.ensure_cursor_in_lower(
            self._active, self._last_text, self._input_cursor_pos,
            self._last_bottom_lines, self._completion.height,
        )

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

            layout_unchanged = (text == self._input.last_rendered_text
                                and total == self._last_bottom_lines
                                and height == self._last_height)
            if layout_unchanged:
                new_status = self._format_status()
                if new_status == self._last_status:
                    self._last_refresh = time.monotonic()
                    self._last_cursor_pos = self._input_cursor_pos
                    return
                # ★ 仅状态行变化 → 单行重写（避免全量重绘）
                self._last_status = new_status
                self._last_refresh = time.monotonic()
                self._last_cursor_pos = self._input_cursor_pos
                out = sys.__stdout__
                status_row = height - total + 2
                out.write(_blessed_move_clear(status_row))
                out.write(f"\r\033[K{new_status}")
                out.flush()
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

            out = sys.__stdout__
            out.write(_blessed_save_cursor())

            # ★ 底部栏扩大时不执行 SU（Scroll Up）上滚旧内容区。
            #    SU 在 DECSTBM 区域内无 scrollback 缓冲，滚出顶部的行永久丢失。
            #    新划入底部栏的区域（原内容区底部行）会被后续的
            #    _draw_input_lines_locked() 覆盖，不影响上屏顶部内容。

            out.write(_blessed_reset_scroll_region())

            self._last_bottom_lines = total

            # ★ 底部栏扩大时（delta > 0），在上屏内容被底部栏覆盖前，
            #    保存被覆盖行的内容到 ring buffer，供缩小后恢复使用。
            if delta > 0 and self._tracker is not None:
                self._tracker.save_rows_to_restore(delta)

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
            r2 = r1 + 1

            tw = self._term_width()
            sep_len = min(tw - 2, 40)
            sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
            out.write(_blessed_move_clear(r1) + "  " + sep)
            # ★ force_redraw 中的 tracker.set 是近似值，仅记录当前绘制行号。
            #    最终光标位置在方法末尾 set(scroll_end, 1) 处修正。
            self._cursor_tracker.set(r1, 3)  # 分隔线从第3列开始
            out.write(_blessed_move_clear(r2) + self._last_status)
            self._cursor_tracker.set(r2, 1)

            self._draw_input_lines_locked(out, text, r2 + 1, tw)
            input_rows = self._input.cached_input_rows
            for r in range(r2 + 1 + input_rows, height + 1):
                out.write(_blessed_move_clear(r))
            self._cursor_tracker.set(height, 1)

            self._last_scroll_end = scroll_end
            if self._tracker is not None:
                self._tracker.set_scroll_end(scroll_end)
            out.write(f"{_blessed_set_scroll_region(1, scroll_end)}")
            # ★ 底部栏缩小时（delta < 0），恢复之前保存的上屏内容行，
            #    确保上屏内容在弹窗弹出/缩回后保持原样。
            if delta < 0 and old_scroll_end > 0:
                saved = self._tracker.get_saved_rows() if self._tracker is not None else None
                if saved:
                    n_rows = min(-delta, scroll_end - old_scroll_end)
                    for i, line in enumerate(saved[:n_rows]):
                        r = old_scroll_end + 1 + i
                        if r <= height:
                            out.write(_blessed_move_clear(r) + line.rstrip('\n'))
                    self._cursor_tracker.set(min(old_scroll_end + n_rows, scroll_end), 1)
                    if self._tracker is not None:
                        self._tracker.clear_saved()
                else:
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
        """绘制输入行（需持有 output_lock）。委托给 InputRenderer。

        Args:
            out: stdout 文件对象（未使用，由 InputRenderer 内部写入 sys.__stdout__）。
            text: 输入文本。
            r_start: 第一行输入区的行号。
            term_width: 当前终端宽度。
        """
        self._input.draw_input_lines(
            self._completion, text, self._status_active,
            r_start, term_width, self._cursor_tracker,
        )

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

    def _apply_completion_show(self, items: list[str], selected_idx: int,
                                texts: list[str] | None = None,
                                start_pos: int = 0, orig_prefix: str = "",
                                title: str = "补全") -> None:
        """线程安全的状态设置 — 仅更新 _completion 状态，无 I/O。

        由 render 线程在 output_lock 内调用。
        """
        total_items = len(items)
        h_items = min(total_items, _CompletionPopup._COMPLETION_MAX_ITEMS)
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

    def _apply_completion_hide(self) -> None:
        """线程安全的状态清除 — 仅清零 _completion 状态，无 I/O。

        由 render 线程在 output_lock 内调用。
        """
        self._completion._popup_height = 0
        self._completion._visible = False
        self._completion._title = "补全"
        self._completion._is_selection = False
        self._completion._items = []
        self._completion._texts = []
        self._completion._idx = 0
        self._completion._start_pos = 0
        self._completion._orig_prefix = ""

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
                         title: str = "补全") -> None:
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
