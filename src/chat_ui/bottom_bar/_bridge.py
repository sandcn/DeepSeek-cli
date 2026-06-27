"""BottomBarBridge — DECSTBM 管理层，替换 _BottomBar 的滚动区域管理。

保留 ScrollRegionManager + _StdoutLineTracker + CursorTracker 组合，
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
import sys

from ._scroll_region import (
    ScrollRegionManager,
    blessed_save_cursor,
    blessed_restore_cursor,
    blessed_set_scroll_region,
    blessed_reset_scroll_region,
    blessed_move_clear,
    blessed_cursor_goto,
    _term_height as _sr_term_height,
    _term_width as _sr_term_width,
)
from ._stdout_tracker import _StdoutLineTracker
from ._theme import _BOTTOM_MIN_HEIGHT, _MIN_INPUT_ROWS
from ..infrastructure.cursor_tracker import CursorTracker
from ...ui._lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)

__all__ = ["BottomBarBridge"]


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
        from ._cursor import _expand_tabs, _wrap_by_width
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
        return _sr_term_height()

    def _term_width(self) -> int:
        return _sr_term_width()

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
        from ._cursor import _expand_tabs, _wrap_by_width, _compute_cursor_visual_pos

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
        """设置 SubAgent 槽位数据，预计算行数（仅内存）。

        每个 slot 1 行 + model_phase 最多 1 行 + tool_history 最多 3 行。
        行数供 _bottom_lines 计算使用，标记 _subagent_slots_dirty
        通知 VNode 渲染路径需要重新渲染。
        """
        if slots == self._subagent_slots:
            return
        self._subagent_slots = slots
        if not slots:
            self._subagent_line_count = 0
            self._subagent_slots_dirty = True
            return
        count = 0
        for slot in slots.values():
            count += 1  # 主行
            # 模型阶段状态行（思考中/回答中/接收工具参数中 + 耗时）
            if slot.get("model_phase", ""):
                count += 1
            tool_history = slot.get("tool_history", [])
            if tool_history:
                count += min(len(tool_history), 3)  # 最多 3 条历史
        self._subagent_line_count = count
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
            total = self._bottom_lines
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
