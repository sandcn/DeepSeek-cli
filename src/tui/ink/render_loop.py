"""RenderLoop — 渲染线程主循环（P2-2 会话组合拆分）。

从 ``InkSession`` 抽出的渲染线程职责：

  - 渲染线程生命周期内的循环（调度器等待 → 帧处理 → 崩溃恢复）；
  - 稳定运行后复位崩溃恢复计数；
  - 退出时保留内容命令并释放高分辨率定时器；
  - 动画状态同步给调度器。

``InkSession`` 保留 ``_render`` / ``_sync_scheduler_state`` 门面委托，
测试与既有调用面不变。
"""

from __future__ import annotations

import logging
import time

from src.tui._const import ANSI_EMERGENCY_RED, ANSI_EMERGENCY_RESET
from . import hooks as _hooks

_logger = logging.getLogger(__name__)

#: 崩溃恢复后视为「稳定」的最小运行时长（秒）
_RECOVER_STABLE_SECS = 60.0


class RenderLoop:
    """渲染线程主循环（组合对象，持有 session 引用）。"""

    def __init__(self, session) -> None:
        self._session = session

    def run(self) -> None:
        """渲染线程入口（原 ``InkSession._render`` 逐字迁移）。"""
        s = self._session
        from .timer_resolution import (
            acquire_high_resolution_timer as _acquire_high_resolution_timer,
            release_high_resolution_timer as _release_high_resolution_timer,
        )

        entry_version = s._render_version
        _acquire_high_resolution_timer()
        ctx = getattr(s, "_hook_ctx", None)
        if ctx is not None:
            _hooks.push_context(ctx)
        try:
            while s._render_running:
                scheduler = getattr(s, "_scheduler", None)
                if scheduler is not None:
                    if not scheduler.wait():
                        break
                else:  # 兼容 stub（无调度器）——退化为固定节拍
                    time.sleep(s._config.render_interval)
                if not s._render_running:
                    break
                try:
                    s._drain_queue()
                    s._cmd_event.clear()
                    if s._bottom_redraw_requested.is_set():
                        s._cmd_event.set()
                    if s._recover_attempts > 0 and s._last_recover_time > 0:
                        if time.monotonic() - s._last_recover_time >= _RECOVER_STABLE_SECS:
                            s._recover_attempts = 0
                            s._last_recover_time = 0.0
                    if s._exit_requested:
                        s._render_running = False
                        break
                    self.sync_scheduler_state()
                except Exception as exc:
                    if s._handle_render_crash(exc):
                        return
                    break
        finally:
            if ctx is not None:
                _hooks.pop_context()
            _release_high_resolution_timer()
            if s._render_version != entry_version:
                _logger.debug("render 线程版本已更新（新线程已启动），跳过排空")
            else:
                dropped = s._drain_queue_safe(keep_content=True)
                _logger.debug("render 线程 finally 排空 %d 条命令", dropped)
                if dropped > 0:
                    s._write_emergency(
                        f"{ANSI_EMERGENCY_RED}[ChatUI] render 线程已终止，"
                        f"丢弃 {dropped} 条待处理命令{ANSI_EMERGENCY_RESET}\n",
                        stream="stderr",
                    )

    def sync_scheduler_state(self) -> None:
        """把本拍动画状态同步给调度器并推进节流窗口（P0-1）。"""
        s = self._session
        ctx = getattr(s, "_hook_ctx", None)
        try:
            from ._animation import has_active_animations

            animating = bool(has_active_animations(ctx=ctx))
        except Exception:
            animating = False
        s._animating = animating
        scheduler = getattr(s, "_scheduler", None)
        if scheduler is not None:
            scheduler.set_animating(animating)
            scheduler.on_frame()


__all__ = ["RenderLoop"]
