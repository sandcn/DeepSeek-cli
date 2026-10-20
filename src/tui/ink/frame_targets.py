"""FrameTargets — 历史段 / live 段渲染目标（P1-3）。

背景：``InkRenderer`` 之前每帧对整份文档做行级 diff（虽有 stable_prefix /
history_ref 快路径，但决策逻辑散落在 diff 与 renderer 中）。本模块把渲染
文档拆成两个显式目标：

  - ``HistoryTarget``：已提交内容区（committed 区）——只有 history 引用/
    长度/偏移变化时才视为脏；
  - ``LiveTarget``：状态栏/输入区/流式尾部——从 history 末尾开始比较，历史
    未变时**完全不扫描历史行**。

``FrameTargets.plan(prev, new)`` 产出 ``RenderPlan``（history_changed /
live_changed / first_diff / history_end），``InkRenderer`` 据此决定从哪一行
开始写、是否整段跳过。写终端仍由 InkRenderer 统一执行（终端光标/滚动需要
单一写者），但「历史 vs live」的脏判定与扫描范围已完全分离。
"""

from __future__ import annotations

from dataclasses import dataclass

from .diff import first_diff_line
from .output import Frame


@dataclass(frozen=True)
class RenderPlan:
    """本帧渲染计划。"""

    history_changed: bool
    live_changed: bool
    first_diff: int
    history_end: int

    @property
    def changed(self) -> bool:
        return self.history_changed or self.live_changed


class HistoryTarget:
    """历史段（committed 区）脏判定。"""

    def is_changed(self, prev: Frame, new: Frame) -> bool:
        if prev is None:
            return True
        if prev._history_len <= 0 and new._history_len <= 0:
            return False
        return not prev.history_unchanged(new)


class LiveTarget:
    """live 段（status/input/stream tail）脏判定与首差异行。"""

    def first_diff(self, prev: Frame, new: Frame, start: int) -> int:
        if prev is None:
            return 0
        return first_diff_line(prev, new, start=start)


class FrameTargets:
    """历史段 + live 段组合目标。"""

    def __init__(self) -> None:
        self.history = HistoryTarget()
        self.live = LiveTarget()

    def plan(self, prev: Frame | None, new: Frame) -> RenderPlan:
        history_changed = self.history.is_changed(prev, new)
        history_end = new._history_offset + new._history_len
        if prev is None:
            return RenderPlan(True, True, 0, history_end)
        # 历史未变：从历史末尾开始扫描 live 段；历史变化：从 0 扫描。
        start = 0 if history_changed else history_end
        first = self.live.first_diff(prev, new, start)
        live_changed = first >= 0
        if not live_changed:
            first = len(new.lines)
        return RenderPlan(history_changed, live_changed, first, history_end)


__all__ = ["FrameTargets", "RenderPlan", "HistoryTarget", "LiveTarget"]
