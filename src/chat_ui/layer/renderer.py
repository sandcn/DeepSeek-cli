"""TUI 层级渲染系统 — 增量渲染器。

接收合并后的帧行列表，比较与上一帧的差异，仅输出变化的行到终端。
使用 ANSI 控制序列定位光标并逐行更新。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IncrementalLayerRenderer:
    """增量层级渲染器。

    缓存上一帧内容和行数，比较差异后仅输出变化行。
    行数变化占比超过阈值时全量刷新。
    """

    # 全量刷新阈值：变化行数 / 总行数 > 此值时全量刷新
    _DIFF_THRESHOLD: float = 0.5

    def __init__(
        self,
        output_adapter: object,
        diff_threshold: Optional[float] = None,
    ) -> None:
        """初始化增量渲染器。

        Args:
            output_adapter: 终端输出适配器，需支持 write_raw(str) 和 flush() 方法
            diff_threshold: 全量刷新阈值，None 使用默认值 0.5
        """
        self._output = output_adapter
        if diff_threshold is not None:
            self._DIFF_THRESHOLD = diff_threshold

        # 上一帧状态
        self._last_lines: list[str] = []
        self._last_count: int = 0  # 峰值行数（防残留）
        self._is_first_frame: bool = True

    def render(self, lines: list[str]) -> int:
        """渲染帧到终端（增量输出）。

        比较当前 lines 与 _last_lines，仅输出变化的行。

        Args:
            lines: 当前帧行列表（纯 str，无换行符）

        Returns:
            当前帧行数（供后续帧回退使用）
        """
        if not lines:
            # 空帧：清除所有旧内容
            return self._render_empty()

        if self._is_first_frame:
            return self._render_first_frame(lines)

        return self._render_incremental(lines)

    def reset(self) -> None:
        """重置状态（强制下一帧全量输出）。"""
        self._last_lines = []
        self._last_count = 0
        self._is_first_frame = True

    # ── 内部渲染方法 ─────────────────────────────────────

    def _render_first_frame(self, lines: list[str]) -> int:
        """首次渲染：全量输出所有行。"""
        total = len(lines)

        for line in lines:
            self._output.write_raw(f"\r\033[K{line}\n")

        # ANSI SCOSC: 保存光标位置，供下一帧回退
        self._output.write_raw("\033[s")
        self._output.flush()

        self._last_lines = list(lines)
        self._last_count = total
        self._is_first_frame = False

        return total

    def _render_empty(self) -> int:
        """渲染空帧：清除所有旧内容。"""
        if self._last_count > 0:
            if not self._is_first_frame:
                # 恢复光标 + 上移
                self._output.write_raw(f"\033[u\033[{self._last_count}A")
            # 逐行清空
            for _ in range(self._last_count):
                self._output.write_raw("\r\033[K\n")
            # 回到顶部
            self._output.write_raw(f"\033[{self._last_count}A")
            self._output.flush()

        self._last_lines = []
        self._last_count = 0
        self._is_first_frame = False

        return 0

    def _render_incremental(self, lines: list[str]) -> int:
        """增量渲染：仅输出变化的行。"""
        total = len(lines)
        old_total = len(self._last_lines)

        # 计算差异
        changed_rows: list[int] = []
        min_len = min(total, old_total)
        for i in range(min_len):
            if lines[i] != self._last_lines[i]:
                changed_rows.append(i)

        # 新增的行
        if total > old_total:
            for i in range(old_total, total):
                changed_rows.append(i)

        # 检查是否需要全量刷新
        diff_ratio = len(changed_rows) / max(total, 1)
        if diff_ratio > self._DIFF_THRESHOLD or (changed_rows == [] and total != old_total):
            return self._render_full(lines)

        if not changed_rows:
            # 无变化，不输出
            return self._last_count

        # ── 增量更新变化行 ──
        self._output.write_raw(f"\033[u\033[{self._last_count}A")

        current_row = 0
        for i in range(total):
            if i in changed_rows:
                # 定位到行 i
                if i > current_row:
                    self._output.write_raw(f"\033[{i - current_row}B")
                elif i < current_row:
                    self._output.write_raw(f"\033[{current_row - i}A")
                self._output.write_raw(f"\r\033[K{lines[i]}")
                current_row = i + 1

        # 清除多余行（帧缩小）
        if old_total > total:
            extra = old_total - total
            for _ in range(extra):
                self._output.write_raw("\n\033[K")
            self._output.write_raw(f"\033[{extra}A")

        # 移到帧末尾
        if total > current_row:
            self._output.write_raw(f"\033[{total - current_row}B")

        # SCOSC: 保存光标
        self._output.write_raw("\033[s")
        self._output.flush()

        self._last_lines = list(lines)
        self._last_count = max(self._last_count, total)

        return self._last_count

    def _render_full(self, lines: list[str]) -> int:
        """全量刷新渲染。"""
        total = len(lines)

        if self._last_count > 0 and not self._is_first_frame:
            self._output.write_raw(f"\033[u\033[{self._last_count}A")

        for line in lines:
            self._output.write_raw(f"\r\033[K{line}\n")

        # 清除多余行
        if self._last_count > total:
            extra = self._last_count - total
            for _ in range(extra):
                self._output.write_raw("\n\033[K")
            self._output.write_raw(f"\033[{extra}A")

        self._output.write_raw("\033[s")
        self._output.flush()

        self._last_lines = list(lines)
        self._last_count = max(self._last_count, total)

        return self._last_count

    @property
    def output_adapter(self):
        """获取输出适配器（用于 flush 等操作）。"""
        return self._output
