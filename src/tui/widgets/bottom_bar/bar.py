"""_BottomBar — 流式输出期间 inline 底部输入栏（动态拆行）。

【inline 模式 · 2026-07-16 重构】

使用 ``\r\033[K`` 逐行清行覆盖的 inline 模式，替代 DECSTBM 全屏滚动区域。
终端保持正常全屏滚动模式，底部栏通过绝对光标定位（CUP）在终端底部渲染。

### 新布局（从终端底部向上）

  输入区域（动态行数 + 补全弹窗）
    ─ 上分割线（━，深灰237，行尾含 CPU · MEM 信息）
    > 输入文本...（或占位提示符，超长自动拆行）
    · 续行...
    ─ 下分割线（━，深灰237，行尾含时间戳）
  状态行（模型名·耗时·令牌数·工具计数）
  分隔线（渐变，左青右灰，支持 aurora+shimmer 动效）

### force_redraw() 渲染流程

  1. 推进动画时钟 + 更新系统统计
  2. 计算本次底部栏总行数（total）
  3. 使用 CUP 跳转到底部栏起始行（height - total + 1）
  4. 逐行写入：``\r\033[K`` + 行内容 + ``\n``
  5. 若帧缩小，清除多余行
  6. 定位光标到输入行末尾
  7. flush

### 移除的 DECSTBM 相关功能

  - ``sync_bottom_lines()`` → 空操作（无 DECSTBM 需同步）
  - ``get_scroll_end()`` → 始终返回 0
  - ``ensure_cursor_upper()`` → 空操作（inline 模式内容直接输出）
  - ``_StdoutLineTracker`` → 不再安装（inline 模式无需行追踪）
  - DECSTBM / SCOSC / DECRC / SU / SD ANSI 序列全部移除

### 线程安全

  所有终端 I/O 在 output_lock 保护下执行。

### 终端控制策略

  仅使用基本 ANSI 序列：``\r\033[K``（清行）、``\033[{r};{c}H``（CUP）。

重组为 bottom_bar 子包：
  - bar.py     — _BottomBar 主类（本文件）
  - theme      — ANSI 颜色常量 + 占位符 + 布局配置
  - status     — 状态行格式化 + 工具计数（_StatusMixin）
  - completion — 补全弹窗（_CompletionPopup 独立类）
  - selection  — run_bottom_bar_selection() 交互选择
  - draw       — 绘制函数
  - blessed    — ANSI/Blessed 辅助函数（精简版）
  - cursor     — 光标定位计算

性能优化 — 终端尺寸缓存：
  实例级缓存 + TTL（0.1s）消峰，通过 force_refresh_dimensions()
  在 SIGWINCH/resize 检测路径中立即刷新缓存。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from ...terminal.blessed import get_terminal
from ...core.animator import AnimatorContext, BreathPalette
from .completion import _CompletionPopup
from .status import _StatusMixin
from .theme import (
    _BOTTOM_MIN_HEIGHT,
    _BOTTOM_MIN_LINES,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SEP,
    _MIN_INPUT_ROWS,
    _PLACEHOLDER_COMPACT,
    _PLACEHOLDER_STREAMING,
    _PLACEHOLDER_TEXT,
)
from .cursor import (
    _compute_cursor_visual_pos,
)
from ..cursor_tracker import CursorTracker
from ..lock import _try_acquire_output_lock
from ...terminal.adapter import register_sigwinch_callback, unregister_sigwinch_callback
from ...core.system_monitor import _SystemMonitor
from .blessed import (
    _blessed_move_clear,
    _blessed_cursor_goto,
)
from .draw import (
    _draw_input_lines_locked as _draw_impl_input_lines,
    _draw_all_locked as _draw_impl_all,
    _redraw_cycle_only as _draw_impl_redraw_cycle,
    _build_sep_with_system_stats,
    _build_status_text,
    _visible_width,
)
from ...consumer.utils import _emergency_write


_logger = logging.getLogger(__name__)


class _BottomBar(_StatusMixin):
    """终端底部 inline 输入栏，流式输出期间始终可见。

    使用 ``\r\033[K`` 逐行清行覆盖的 inline 模式。
    终端保持正常全屏滚动，底部栏通过 CUP 绝对定位在底部渲染。

    视觉风格（优雅信息风）：
      - 分隔线：蓝灰 ``━`` 实线做内容区与输入区边界
      - 状态行：多色分层（· 模型名·耗时·令牌数·工具计数）
      - 输入区：亮青 ``❯`` 提示符，空输入时显示灰色占位提示
      - 补全弹窗：无边框扁平样式（标题行 + ▶ 指示器高亮 + 快捷键提示）

    线程安全：
      所有终端 I/O 在 output_lock 保护下执行。
    """

    _MIN_HEIGHT = _BOTTOM_MIN_HEIGHT

    def __init__(self, cursor_tracker: CursorTracker | None = None):
        self._active = False
        self._last_text = ""
        self._last_status = ""
        # ── _StatusMixin 依赖字段 ──
        self._status_active: bool = False
        self._model_name: str = ""
        self._tool_count: int = 0
        self._tool_fail_count: int = 0
        self._tool_total: int = 0
        self._subagent_lines: list[str] = []
        self._last_subagent_lines: list[str] = []
        # ── 主Agent阶段状态 ──
        self._main_phase: str = ""
        self._main_phase_start: float = 0.0
        # ── 工具调用阶段开始时间 ──
        self._tool_phase_start: float = 0.0
        # ── 布局/光标 ──
        self._last_rendered_total_lines: int = 0  # inline: 光标到栏顶距离（上行步数）
        self._bar_total_lines: int = 0            # inline: 全栏高度（清除步数）
        self._bar_cleared: bool = False           # inline: prepare_for_content 已清除旧栏
        self._input_base_rows: int = 1            # inline: 栏底到首行输入行的回退行数
        self._input_cursor_col: int = 2           # inline: 输入行光标列（从1计数）
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._cached_wrapped_for: str = ""
        self._cached_wrapped_width: int = 0
        self._cached_wrapped_lines: list[str] | None = None
        self._cached_input_rows: int = _MIN_INPUT_ROWS
        self._last_rendered_text: str = ""
        self._last_height: int = 0
        # ── 补全弹窗组合对象 ──
        self._completion = _CompletionPopup(cursor_tracker=cursor_tracker)
        # ── 光标坐标追踪器（全局共享实例） ──
        self._cursor_tracker = cursor_tracker or CursorTracker()
        # ── 统一动画时钟（AnimatorContext 单例） ──
        self._animator = AnimatorContext.get_default()
        # ── 终端尺寸缓存（性能优化，避免高频 ioctl） ──
        self._cached_height: int = 0
        self._cached_width: int = 0
        self._last_dimension_refresh: float = 0.0
        self._DIMENSION_TTL: float = 0.1
        self._sigwinch_cb: Any = None
        self._needs_full_repaint: bool = False
        # ── 系统监控（CPU/内存） ──
        self._system_monitor: _SystemMonitor | None = None
        self._cached_cpu_percent: float = 0.0
        self._cached_mem_percent: float = 0.0
        self._last_system_stats_time: float = 0.0
        self._SYSTEM_STATS_INTERVAL: float = 1.0

    # ── 活跃状态 property ──────────────────────────────────

    @property
    def is_active(self) -> bool:
        """底部栏是否已激活（setup 后为 True，teardown 后为 False）。"""
        return self._active

    # ── 补全弹窗兼容 property ──

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
        """当前底部栏总行数（分隔线 + subagent面板行 + 状态行 + 输入区域）。"""
        return 2 + len(self._subagent_lines) + self._compute_input_rows()

    def _compute_input_rows(self) -> int:
        """根据当前输入文本计算所需的输入行数（最少 _MIN_INPUT_ROWS 行 + 补全弹窗高度）。"""
        text = self._last_text or ""
        if not text:
            base = _MIN_INPUT_ROWS
        else:
            from .cursor import _expand_tabs, _wrap_by_width
            max_input = max(1, self._term_width() - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(_MIN_INPUT_ROWS, len(wrapped))
        return 2 + base + self._completion.height  # +2 顶底分割线

    def _compute_bottom_lines_for(self, text: str, term_width: int) -> int:
        """计算给定文本对应的底部栏总行数（纯计算方法）。

        与 _compute_input_rows() 不同，本方法接受 text 参数而非访问 self._last_text，
        用于 compute_cursor_position() 中确保数据源一致。
        """
        if not text:
            base = _MIN_INPUT_ROWS
        else:
            from .cursor import _expand_tabs, _wrap_by_width
            max_input = max(1, term_width - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(_MIN_INPUT_ROWS, len(wrapped))
        return 4 + len(self._subagent_lines) + base + self._completion.height

    # ── 系统监控（CPU/内存） ─────────────────────────────

    def _update_system_stats(self) -> None:
        """更新 CPU 和内存使用率缓存（1 秒间隔消峰）。"""
        now = time.monotonic()
        if now - self._last_system_stats_time < self._SYSTEM_STATS_INTERVAL:
            return
        self._last_system_stats_time = now

        if self._system_monitor is None:
            self._system_monitor = _SystemMonitor()

        try:
            cpu_pct, mem_pct = self._system_monitor.get_cpu_and_mem()
            self._cached_cpu_percent = cpu_pct
            self._cached_mem_percent = mem_pct
        except Exception:
            pass

    # ── 终端尺寸查询（缓存版本） ──────────────────────────

    def _refresh_dimensions(self) -> None:
        """刷新终端尺寸缓存（带 TTL 消峰）。"""
        now = time.monotonic()
        if now - self._last_dimension_refresh < self._DIMENSION_TTL:
            return
        self._last_dimension_refresh = now
        try:
            term = get_terminal()
            self._cached_height = term.height
            self._cached_width = term.width
        except Exception:
            import shutil
            try:
                sz = shutil.get_terminal_size()
                self._cached_height = sz.lines
                self._cached_width = sz.columns
            except Exception:
                pass

    def set_full_repaint_needed(self) -> None:
        """标记需要全屏重建（仅在 resize 后调用）。"""
        self._needs_full_repaint = True

    def force_refresh_dimensions(self) -> None:
        """强制刷新终端尺寸缓存，绕过 TTL。"""
        self._last_dimension_refresh = 0.0
        self._refresh_dimensions()
        self.set_full_repaint_needed()

    def _term_height(self) -> int:
        """获取终端高度（缓存版本）。"""
        self._refresh_dimensions()
        return self._cached_height or 24

    def _term_width(self) -> int:
        """获取终端宽度（缓存版本）。"""
        self._refresh_dimensions()
        return self._cached_width or 80

    # ── 光标定位相关 ──────────────────────────────────

    def get_scroll_end(self) -> int:
        """inline 模式下无滚动区域，始终返回 0。

        保持公开接口兼容性（caller 使用返回值判断）。
        """
        return 0

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        """获取光标定位所需数据：文本、光标位置、终端高度、终端宽度。

        供 RenderEngine.position_cursor 使用。
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
        """计算光标在底部栏中的终端行号和列号（inline 模式简化版）。

        纯计算函数，不执行终端 I/O。
        """
        max_input = max(1, w - 4)
        vis_row, vis_col = self._cursor_visual_pos_from_cache(
            text, cursor_pos, max_input,
        )
        total_bottom = max(5, self._compute_bottom_lines_for(text, w))
        popup_offset = self._completion.height
        subagent_offset = len(self._subagent_lines)
        r_cursor = max(1, h - total_bottom + 4 + subagent_offset + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    def _cursor_visual_pos_from_cache(
        self, text: str, cursor_pos: int, max_width: int,
    ) -> tuple[int, int]:
        """从缓存的拆行结果计算光标视觉位置。"""
        from .cursor import _expand_tabs, _tab_pos_to_expanded, _wrap_by_width
        from wcwidth import wcswidth
        if (self._cached_wrapped_for != text
                or self._cached_wrapped_width != max_width
                or self._cached_wrapped_lines is None):
            expanded = _expand_tabs(text)
            self._cached_wrapped_lines = _wrap_by_width(expanded, max_width)
            self._cached_wrapped_for = text
            self._cached_wrapped_width = max_width
        abs_cursor = len(text) if cursor_pos < 0 else cursor_pos
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
        """inline 模式下无 DECSTBM 需同步，空操作。

        保持公开接口兼容性。
        """
        pass

    def set_subagent_frame(self, lines: list[str]) -> None:
        """设置 subagent 面板行数据（仅写内存，由 force_redraw() 消费）。"""
        self._subagent_lines = list(lines)

    def ensure_cursor_in_upper(self) -> None:
        """inline 模式下内容直接输出到终端，无需光标定位。

        保持公开接口兼容性，空操作。
        """
        pass

    def ensure_cursor_in_lower(self) -> None:
        """渲染完成后将光标移回输入行（inline 模式，纯相对定位）。

        从栏底（force_redraw 写入终点）上行 _input_base_rows 到输入行，
        右移到 _input_cursor_col 列。与 force_redraw 末尾定位逻辑一致。
        """
        if not self._active:
            return
        cursor_row_up = self._input_base_rows
        cursor_col = max(2, self._input_cursor_col)
        if cursor_row_up <= 0:
            return
        with _try_acquire_output_lock(name="bottom_bar.ensure_cursor_in_lower", timeout=0.3) as locked:
            if not locked:
                return
            out = sys.__stdout__
            out.write(f"\033[{cursor_row_up}A\r\033[{cursor_col - 1}C")
            out.flush()

    # ── 生命周期 ──────────────────────────────────────────

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        """设置输入文本和光标位置（线程安全，仅更新状态，不直接 I/O）。"""
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def set_main_phase(self, phase: str) -> None:
        """设置主Agent的模型阶段。"""
        if phase != self._main_phase:
            self._main_phase_start = time.monotonic()
        self._main_phase = phase

    def setup(self) -> None:
        """启用底部栏：状态初始化 + SIGWINCH 注册（inline 模式无 DECSTBM）。

        终端高度不足 _MIN_HEIGHT 时静默跳过。
        幂等：已激活时重复调用无效果。

        inline 模式：不再安装 _StdoutLineTracker（全屏滚动无需行追踪），
        不再设置 DECSTBM 滚动区域。
        """
        if self._active:
            return
        height = self._term_height()
        if height < self._MIN_HEIGHT:
            return
        self._active = True

        # ── 注册 SIGWINCH 回调 ──
        def _on_sigwinch(cols: int, rows: int) -> None:
            self._last_dimension_refresh = 0.0
            self._cached_height = rows
            self._cached_width = cols
            self._needs_full_repaint = True
        self._sigwinch_cb = _on_sigwinch
        register_sigwinch_callback(self._sigwinch_cb)

        # inline 模式不安装 stdout 行追踪器
        # inline 模式不设置 DECSTBM

        with _try_acquire_output_lock(name="bottom_bar.setup", timeout=1.0) as locked:
            if locked:
                self._last_text = ""
                self._last_rendered_total_lines = self._bottom_lines
            else:
                sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
                sys.__stdout__.flush()

    def teardown(self) -> None:
        """停用底部栏：清理底部残留 + 注销 SIGWINCH（inline 模式无 DECSTBM 重置）。

        幂等：未激活时重复调用无效果。
        """
        if not self._active:
            return
        self._active = False

        # ── 注销 SIGWINCH 回调 ──
        if self._sigwinch_cb is not None:
            try:
                unregister_sigwinch_callback(self._sigwinch_cb)
            except Exception:
                pass
            self._sigwinch_cb = None

        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                # inline 模式：上行清除最后 _last_rendered_total_lines 行
                n = self._last_rendered_total_lines
                if n > 0:
                    _buf = [f"\033[{n}A"]
                    for _ in range(n):
                        _buf.append("\r\033[K\n")
                    out.write(''.join(_buf))
                    out.flush()
        self._last_rendered_total_lines = 0
        self._bar_total_lines = 0
        self._bar_cleared = False
        self._last_height = 0

    # ── 刷新（inline 模式核心） ──────────────────────────

    def prepare_for_content(self) -> None:
        """在渲染内容前清除旧底部栏（inline 模式，DL 消除闪烁）。

        引擎在 _drain_queue() 的 _phase_render 之前调用。
        使用 ``\033[{n}M``（Delete Line）整行删除旧栏，无逐行清空闪烁。
        删除后光标留在栏顶，内容渲染从此位置开始覆盖。

        使用 _last_rendered_total_lines 上行（光标→栏顶距离），
        使用 _bar_total_lines 确定删除行数（全栏高度）。
        """
        if not self._active:
            return
        n_up = self._last_rendered_total_lines  # 从光标到栏顶
        n_del = self._bar_total_lines            # 全栏高度（删除行数）
        if n_del <= 0:
            return
        with _try_acquire_output_lock(name="bottom_bar.prepare_for_content", timeout=0.5) as locked:
            if not locked:
                return
            try:
                out = sys.__stdout__
                # 上行到栏顶 → DL 整行删除 → 无闪烁
                out.write(f"\033[{n_up}A\033[{n_del}M")
                out.flush()
                self._bar_cleared = True
            except (OSError, ValueError, IOError) as exc:
                _logger.warning("prepare_for_content 失败: %s", exc)

    def force_redraw(self) -> None:
        """无条件全量重绘全部底部栏内容（10Hz 调度，inline 模式）。

        inline 渲染流程（相对定位）：
          1. 推进动画时钟 + 更新系统统计
          2. 构建所有底部栏行
          3. 若 _bar_cleared 跳过上行，否则 ``\033[{n}A`` 上行到栏顶
          4. 逐行 ``\r\033[K`` + 内容 + ``\n``
          5. 光标定位到输入行
          6. flush，更新 _bar_total_lines / _last_rendered_total_lines

        _last_rendered_total_lines = 光标到栏顶距离（用于下次上行）
        _bar_total_lines = 全栏高度（用于 prepare_for_content 清除）
        """
        if not self._active:
            return

        self._animator.tick()
        self._update_system_stats()

        t_start = time.monotonic()

        with _try_acquire_output_lock(name="bottom_bar.force_redraw", timeout=1.0) as locked:
            if not locked:
                return

            try:
                tw = self._term_width()
                height = self._term_height()

                new_status = self._format_status()
                self._last_status = new_status
                self._last_subagent_lines = list(self._subagent_lines)
                self._needs_full_repaint = False

                lines: list[str] = self._build_all_lines(tw, height)
                total = len(lines)
                out = sys.__stdout__
                _buf: list[str] = []

                # ── 上行到栏顶 ──
                if self._bar_cleared:
                    self._bar_cleared = False
                elif self._last_rendered_total_lines > 0:
                    _buf.append(f"\033[{self._last_rendered_total_lines}A")

                # ── 逐行写入 ──
                for line in lines:
                    _buf.append(f"\r\033[K{line}\n")

                # ── 清除多余行（帧缩小时） ──
                extra = self._bar_total_lines - total
                if extra > 0:
                    for _ in range(extra):
                        _buf.append("\r\033[K\n")
                    _buf.append(f"\033[{extra}A")

                # ── 光标定位到输入行 ──
                cursor_row_up = self._input_base_rows
                cursor_col = max(2, self._input_cursor_col)
                if cursor_row_up > 0:
                    _buf.append(f"\033[{cursor_row_up}A")
                    _buf.append(f"\r\033[{cursor_col - 1}C")

                # flush
                out.write(''.join(_buf))
                out.flush()

                # ── 更新状态（两个维度） ──
                self._bar_total_lines = total                              # 全栏高度
                self._last_rendered_total_lines = total - cursor_row_up    # 光标到栏顶距离

                elapsed = (time.monotonic() - t_start) * 1000
                _logger.debug(
                    "force_redraw: frame=%d lines=%d elapsed=%.2fms",
                    self._animator.frame, total, elapsed,
                )
            except (OSError, ValueError, IOError) as exc:
                _logger.warning("force_redraw ANSI 写入失败（终端可能已断开）: %s", exc)
                try:
                    _emergency_write(
                        f"\n[ChatUI] 底部栏渲染失败: {exc}\n",
                        stream="stderr",
                    )
                except Exception:
                    pass
            except Exception:
                _logger.warning("force_redraw 异常", exc_info=True)

    def _build_all_lines(self, tw: int, height: int) -> list[str]:
        """构建底部栏全部行（inline 模式核心）。

        从顶部到底部的布局顺序：
          1. 分隔线（渐变 + 状态文本）
          2. subagent 面板行
          3. 状态行（模型名·耗时·令牌数·工具计数）
          4-N. 输入区域（上分割线 + 输入行 + 下分割线 + 补全弹窗）

        Args:
            tw: 终端宽度。
            height: 终端高度。

        Returns:
            底部栏行列表（每行为含 ANSI 样式的字符串）。
        """
        from ...terminal.terminal import is_narrow as _is_narrow_fn
        lines: list[str] = []

        # ── 1. 分隔线 ──
        sep_start = 45
        if self._animator.breath_frame > 0:
            sep_start = self._animator.sine_color(40, 45, 10)
        status_text = _build_status_text(self) if self._status_active else ""
        sep = _build_sep_with_system_stats(
            tw, sep_start,
            self._cached_cpu_percent,
            self._cached_mem_percent,
            bar=self,
            breath_frame=self._animator.breath_frame,
            status_text=status_text,
            status_active=self._status_active,
            narrow=_is_narrow_fn(),
        )
        lines.append(sep)

        # ── 2. subagent 面板行 ──
        for sub_line in self._subagent_lines:
            lines.append(sub_line)

        # ── 3. 状态行 ──
        lines.append(self._last_status)

        # ── 4. 输入区域（委托 _draw_all_locked 构建） ──
        # 收集输入区域行（上分割线 + 输入行 + 下分割线 + 补全弹窗）
        input_lines = self._build_input_area_lines(tw)
        lines.extend(input_lines)

        return lines

    def _build_input_area_lines(self, tw: int) -> list[str]:
        """构建输入区域行（上分割线 + 输入行 + 下分割线 + 补全弹窗）。

        Returns:
            输入区域行列表（不含 ANSI 清行前缀，纯内容字符串）。
        """
        from ...terminal.terminal import is_narrow as _is_narrow_fn
        from .cursor import _expand_tabs, _wrap_by_width
        from .theme import (
            _COLOR_ACCENT, _COLOR_DEEP_CYAN, _COLOR_SPEED,
            get_prompt_breath_color, make_sep_gradient,
        )
        from wcwidth import wcswidth

        lines: list[str] = []

        text = self._last_text or ""
        max_input = max(1, tw - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        self._cached_wrapped_for = text
        self._cached_wrapped_width = max_input
        self._cached_wrapped_lines = wrapped
        base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
        self._cached_input_rows = base_rows + self._completion.height + 2
        self._last_rendered_text = text

        # ── 补全弹窗 ──
        popup_lines = self._completion.render_to_lines(tw)
        popup_height = len(popup_lines)
        lines.extend(popup_lines)

        # ── 上分割线（行尾带 CPU · MEM 信息） ──
        cpu_int = max(0, min(100, round(self._cached_cpu_percent)))
        mem_int = max(0, min(100, round(self._cached_mem_percent)))
        cpu_mem_info = (
            f" {_COLOR_ACCENT}CPU:{_COLOR_RESET}"
            f" {_COLOR_SPEED}{cpu_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
            f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
            f"{_COLOR_ACCENT}MEM:{_COLOR_RESET}"
            f" {_COLOR_SPEED}{mem_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
        )
        cpu_mem_w = _visible_width(cpu_mem_info)
        top_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, tw - cpu_mem_w) + cpu_mem_info
        lines.append(top_sep)

        # ── 输入文本行 ──
        for i, segment in enumerate(wrapped):
            if i == 0:
                if _is_narrow_fn():
                    prompt_prefix = f"{_COLOR_DEEP_CYAN}>{_COLOR_RESET} "
                else:
                    prompt_color = get_prompt_breath_color(self._animator.breath_frame)
                    prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
                if text:
                    lines.append(prompt_prefix + segment)
                else:
                    if _is_narrow_fn():
                        placeholder_color = _COLOR_DIM
                    else:
                        import re
                        from ...core.text_utils import build_glow_ansi
                        from ...core.theme import THEME as _BOTTOM_THEME
                        glow_str = _BOTTOM_THEME.get('placeholder_glow', '')
                        m = re.search(r"38;5;(\d+)", glow_str)
                        if m:
                            base = int(m.group(1))
                            placeholder_color = build_glow_ansi(self._animator.breath_frame, base, 12)
                        else:
                            placeholder_color = _COLOR_DIM
                    if self._status_active:
                        ph = _PLACEHOLDER_STREAMING
                        lines.append(prompt_prefix + f"{placeholder_color}{ph}\033[0m")
                    else:
                        ph = _PLACEHOLDER_COMPACT if self._completion.is_visible else _PLACEHOLDER_TEXT
                        lines.append(prompt_prefix + f"{placeholder_color}{ph}\033[0m")
            else:
                lines.append(f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")

        # ── 填充空行（补齐到 base_rows） ──
        for _ in range(len(wrapped), base_rows):
            lines.append("  ")

        # ── 存储光标定位信息（供 force_redraw 使用） ──
        self._input_base_rows = base_rows + 1  # 从栏底下一行（\n 后）上行到首行输入行
        if text:
            cursor_pos = min(self._input_cursor_pos, len(text))
        else:
            cursor_pos = 0
        vis_row, vis_col = self._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
        # 光标列 = "> " 宽度(2) + 文本内视觉列偏移 + 1（定位到字符之后）
        self._input_cursor_col = 3 + (vis_col if vis_row == 0 else 0)

        # ── 下分割线（行尾带时间戳） ──
        now_local = time.localtime()
        ts = (
            f"{now_local.tm_year}-{now_local.tm_mon:02d}-"
            f"{now_local.tm_mday:02d} {now_local.tm_hour:02d}:"
            f"{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
        )
        time_info = f" {_COLOR_DIM}{ts}{_COLOR_RESET}"
        time_w = _visible_width(time_info)
        bottom_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, tw - time_w) + time_info
        lines.append(bottom_sep)

        return lines

    # ── 内部绘制（保留接口兼容） ──────────────────────────

    def _draw_input_lines_locked(self, out, text: str, r_start: int, term_width: int, breath_frame: int = 0) -> None:
        """绘制输入行（需持有 output_lock），超长文本自动拆行。

        保留接口兼容性，inline 模式由 _build_input_area_lines 统一处理。
        """
        _draw_impl_input_lines(self, out, text, r_start, term_width, breath_frame)

    def _draw_all_locked(self, out, height: int, breath_frame: int = 0) -> None:
        """绘制全部底部行（需持有 output_lock），超长文本自动拆行。

        保留接口兼容性，inline 模式由 _build_all_lines 统一处理。
        """
        _draw_impl_all(self, out, height, breath_frame)

    # ── 补全弹窗（委托 _CompletionPopup） ──────────────────

    @property
    def is_completion_visible(self) -> bool:
        """补全弹窗是否可见。"""
        return self._completion.is_visible

    def _redraw_cycle_only(self) -> None:
        """仅重绘补全弹窗高亮变化（轻量路径，调用方须持有 output_lock）。

        inline 模式：重新渲染弹窗行并 CUP 定位写入。
        """
        _draw_impl_redraw_cycle(self)

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None,
                         start_pos: int = 0, orig_prefix: str = "",
                         title: str = "补全",
                         types: list[str] | None = None,
                         match_prefix: str = "") -> None:
        """设置补全弹窗状态并触发全量重绘。"""
        if not items or not self._active:
            return

        total_items = len(items)
        h_items = min(total_items, _CompletionPopup._COMPLETION_MAX_ITEMS)
        popup_height = h_items + 2
        max_avail = self._term_height() - 7
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

        # 不直接调 force_redraw — 由引擎 drain 周期统一驱动，避免重复渲染
        self._needs_full_repaint = True

    def hide_completions(self) -> None:
        """清除补全弹窗状态并触发全量重绘。"""
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

        self._needs_full_repaint = True

    def cycle_completion(self, delta: int = 1) -> int:
        """切换补全选中项并触发重绘。

        只更新状态，不直接 force_redraw — 由引擎 drain 周期统一驱动，
        避免与渲染线程竞争导致重复渲染。
        """
        if not self._completion.is_visible or not self._completion._items:
            return 0
        self._completion.cycle(delta)
        self._needs_full_repaint = True
        return self._completion._idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        """获取当前选中补全项的数据。"""
        return self._completion.get_selected()
