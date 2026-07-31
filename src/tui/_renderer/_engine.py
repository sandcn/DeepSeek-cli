"""渲染引擎模块 — TuiEngine render 线程 + Queue 命令队列 + 四阶段渲染循环。

从 ``_renderer.py`` 提取为独立子模块。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Callable

from src.tui._const import (
    RenderCommand,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
from src.tui._config import TuiConfig
from src.tui._locks import _try_acquire_output_lock
from src.tui._screen import cursor_goto
from src.tui._renderer._renderer import _cmd_name, _emergency_write

if TYPE_CHECKING:
    from src.tui._renderer._renderer import TuiRenderer
    from src.tui._bottom_bar import _BottomBar
    from src.tui._input import Input
    from src.tui._cursor_tracker import CursorTracker

_logger = logging.getLogger(__name__)

# ── 内容命令集合（供 _has_content_command 共用） ──

_CONTENT_COMMANDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_OUTPUT,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.PARSE_INFO,
    RenderCommand.USER_MSG,
    RenderCommand.ERROR,
    RenderCommand.WRITE_LINE,
    RenderCommand.NOTIFICATION,
    RenderCommand.DISPLAY_MSGS,
    RenderCommand.SPLASH,
})


# ═══════════════════════════════════════════════════════════
# TuiEngine — 渲染引擎
# ═══════════════════════════════════════════════════════════

class TuiEngine:
    """渲染引擎 — render 线程 + Queue 命令队列 + 四阶段渲染循环。

    崩溃自动恢复：render 线程异常崩溃时自动重建（最多 3 次）。
    """

    def __init__(
        self,
        renderer: "TuiRenderer",
        bottom_bar: "_BottomBar",
        cursor_tracker: "CursorTracker | None" = None,
        input_instance: "Input | None" = None,
        config: TuiConfig | None = None,
    ):
        self._renderer = renderer
        self._bb = bottom_bar
        self._cursor_tracker = cursor_tracker
        self._input = input_instance
        self._config: TuiConfig = config or TuiConfig.defaults()
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=self._config.cmd_queue_maxsize)
        self._cmd_event = threading.Event()
        self._render_thread: threading.Thread | None = None
        self._render_running = False
        self._consecutive_full = 0
        self._bottom_redraw_requested = threading.Event()
        self._panel_refresh_cb: Callable[[], None] | None = None
        self._cmd_queue_dropped: int = 0
        self._render_crashed: threading.Event = threading.Event()
        self._last_bottom_redraw: float = 0.0
        self._recover_attempts: int = 0
        self._recovering_event: threading.Event = threading.Event()
        self._render_version: int = 0

    def push_cmd(self, cmd: tuple) -> None:
        try:
            self._cmd_queue.put(cmd, block=False)
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            _logger.warning(
                "渲染命令队列已满（%s 条），丢弃命令: %s",
                self._cmd_queue.qsize(), _cmd_name(cmd[0]),
            )
            if self._consecutive_full >= self._config.consecutive_full_threshold:
                _logger.error("渲染输出管线持续拥堵（%d 次连续满队列）", self._consecutive_full)
                if self._consecutive_full % self._config.consecutive_full_threshold == 0:
                    _emergency_write(
                        f"{ANSI_EMERGENCY_RED}[ChatUI] 渲染队列已满，已丢弃 "
                        f"{self._cmd_queue_dropped} 条命令{ANSI_EMERGENCY_RESET}\n",
                        stream="stderr",
                    )

    @property
    def render_crashed(self) -> bool:
        return self._render_crashed.is_set()

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._panel_refresh_cb = callback

    def request_bottom_redraw(self) -> None:
        self._bottom_redraw_requested.set()
        self._cmd_event.set()

    def start(self) -> None:
        if self._render_running:
            _logger.warning("start() 被重复调用，render 线程仍在运行，跳过")
            return
        if self._render_thread is not None:
            if self._render_thread.is_alive():
                _logger.warning("start() 被重复调用，render 线程仍在运行，跳过")
                return
            self._render_thread.join()
        self._render_running = True
        self._render_thread = threading.Thread(target=self._render, daemon=True)
        self._render_thread.start()

    def stop(self) -> None:
        self._render_running = False
        if self._render_thread is not None:
            max_retries = 2
            for attempt in range(max_retries):
                thread = self._render_thread
                version = self._render_version
                if thread is None:
                    break
                thread.join(timeout=2.0)
                if not thread.is_alive():
                    break
                if self._render_version != version:
                    self._render_running = False
                    continue
                break
        self._drain_queue_safe()

    def flush(self, timeout: float | None = 5.0) -> None:
        if self._render_thread is None or not self._render_thread.is_alive():
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        task_done = threading.Thread(target=self._cmd_queue.join, daemon=False)
        task_done.start()
        task_done.join(timeout=timeout)
        if task_done.is_alive():
            self._drain_queue_safe()
            task_done.join(timeout=1.0)

    def ensure_cursor_upper(self) -> None:
        try:
            self._bb.ensure_cursor_in_upper()
        except Exception:
            _logger.debug("ensure_cursor_in_upper 异常", exc_info=True)

    # ── 四阶段流水线 ──────────────────────────────

    def _phase_process_input(self) -> None:
        if self._input is not None:
            try:
                self._input.process_events()
            except Exception:
                _logger.warning("_phase_process_input 异常", exc_info=True)

    def _phase_pre_update_panels(self) -> None:
        if self._panel_refresh_cb is not None:
            try:
                self._panel_refresh_cb()
            except Exception:
                _logger.warning("panel_refresh_cb 异常", exc_info=True)

    def _phase_render(self, commands: list[tuple]) -> None:
        try:
            self._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        if self._has_content_command(commands):
            try:
                self.ensure_cursor_upper()
            except Exception:
                _logger.debug("phase_render ensure_cursor_upper 异常", exc_info=True)
        i = 0
        while i < len(commands):
            cmd = commands[i]
            if cmd and self._renderer._is_batchable(cmd[0]):
                batch_end = i + 1
                while batch_end < len(commands) and self._renderer._is_batchable(commands[batch_end][0]):
                    batch_end += 1
                try:
                    self._renderer.render_batch(commands[i:batch_end])
                except Exception:
                    _logger.warning(
                        "批量渲染 %d 条命令失败", batch_end - i, exc_info=True,
                    )
                i = batch_end
            else:
                try:
                    self._renderer.render(cmd)
                except Exception:
                    _logger.warning(
                        "渲染命令 %s 失败", _cmd_name(cmd[0]) if cmd else '?', exc_info=True,
                    )
                i += 1

    def _phase_redraw_bottom(self) -> None:
        now = time.monotonic()
        force = self._bottom_redraw_requested.is_set()
        self._bottom_redraw_requested.clear()
        if force or now - self._last_bottom_redraw >= self._config.bottom_redraw_interval:
            self._last_bottom_redraw = now
            try:
                self._bb.force_redraw()
            except Exception:
                _logger.debug("force_redraw 异常", exc_info=True)
            try:
                self._position_cursor()
            except Exception:
                _logger.debug("position_cursor 异常", exc_info=True)

    @staticmethod
    def _has_content_command(commands: list[tuple]) -> bool:
        for cmd in commands:
            if cmd and cmd[0] in _CONTENT_COMMANDS:
                return True
        return False

    def _drain_queue(self) -> bool:
        commands: list[tuple] = []
        self._phase_process_input()
        self._phase_pre_update_panels()
        with _try_acquire_output_lock(name="drain_queue", timeout=self._config.drain_lock_timeout) as locked:
            if not locked:
                return False
            while len(commands) < self._config.max_batch_size:
                try:
                    commands.append(self._cmd_queue.get_nowait())
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            has_content = bool(commands)
            if commands:
                self._phase_render(commands)
            self._phase_redraw_bottom()
            return has_content

    def _drain_queue_safe(self) -> int:
        dropped = 0
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
                dropped += 1
            except queue.Empty:
                break
        if self._cmd_queue_dropped > 0:
            _logger.info("render 线程终止，共丢弃 %d 条命令", self._cmd_queue_dropped)
        return dropped

    def _handle_render_crash(self, exc: Exception) -> bool:
        self._render_crashed.set()
        try:
            _logger.critical("cmd_queue.qsize=%d", self._cmd_queue.qsize())
            _logger.critical("render 线程异常崩溃", exc_info=True)
            _emergency_write(
                f"{ANSI_EMERGENCY_RED}[ChatUI] render 线程异常终止: "
                f"{type(exc).__name__}: {exc}{ANSI_EMERGENCY_RESET}\n",
                stream="stderr",
            )
        except Exception:
            pass
        self._recover_attempts += 1
        if self._render_running and self._recover_attempts <= self._config.max_recover_attempts:
            _logger.info("render 线程将在 %.1f 秒后自动恢复 (第 %d/%d 次)",
                         self._config.recover_delay, self._recover_attempts,
                         self._config.max_recover_attempts)
            time.sleep(self._config.recover_delay)
            self._drain_queue_safe()
            self._render_version += 1
            self._recovering_event.set()
            self._render_thread = threading.Thread(target=self._render, daemon=True)
            self._render_thread.start()
            _logger.info("render 线程已自动恢复 (第 %d/%d 次)",
                         self._recover_attempts, self._config.max_recover_attempts)
            return True
        else:
            self._render_running = False
            self._cmd_event.set()
            return False

    def _render(self) -> None:
        entry_version = self._render_version
        try:
            while self._render_running:
                try:
                    has_content = self._drain_queue()
                    self._cmd_event.clear()
                    timeout = self._config.render_interval
                    self._cmd_event.wait(timeout=timeout)
                except Exception as exc:
                    if self._handle_render_crash(exc):
                        return
                    else:
                        break
        finally:
            if self._render_version != entry_version:
                _logger.debug("render 线程版本已更新（新线程已启动），跳过排空")
                return
            dropped = self._drain_queue_safe()
            _logger.debug("render 线程 finally 排空 %d 条命令", dropped)
            if dropped > 0:
                _emergency_write(
                    f"{ANSI_EMERGENCY_RED}[ChatUI] render 线程已终止，"
                    f"丢弃 {dropped} 条待处理命令{ANSI_EMERGENCY_RESET}\n",
                    stream="stderr",
                )

    def _position_cursor(self) -> None:
        """唯一的光标位置写入者。单向光标流。"""
        if not self._bb.is_active:
            return
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        r_cursor, cursor_col = self._bb.compute_cursor_position(text, cursor_pos, h, w)
        adapter = self._renderer.output_adapter
        if adapter is not None:
            try:
                adapter.write_raw(cursor_goto(r_cursor, cursor_col))
                adapter.flush()
            except Exception:
                _logger.debug("position_cursor adapter 路径异常", exc_info=True)
        else:
            try:
                sys.__stdout__.write(cursor_goto(r_cursor, cursor_col))
                sys.__stdout__.flush()
            except Exception:
                _logger.debug("position_cursor stdout 路径异常", exc_info=True)
        if self._cursor_tracker is not None:
            self._cursor_tracker.set(r_cursor, cursor_col)


__all__ = ["TuiEngine", "_CONTENT_COMMANDS"]
