"""渲染引擎模块 — TuiEngine render 线程 + Queue 命令队列 + 四阶段渲染循环。

从 ``_renderer.py`` 提取为独立子模块。
"""

from __future__ import annotations

import itertools
import logging
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Callable

from src.tui._const import (
    RenderCommand,
    RenderCmd,
    CONTENT_COMMANDS,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
from src.tui._config import TuiConfig
from src.renderer._locks import _try_acquire_output_lock
from src.tui._screen import cursor_goto
from src.tui._renderer._renderer import _cmd_name, _emergency_write

if TYPE_CHECKING:
    from src.tui._renderer._renderer import TuiRenderer
    from src.tui._bottom_bar import _BottomBar
    from src.tui._input import Input
    from src.tui._cursor_tracker import CursorTracker
    from src.tui._output import RenderOutput

_logger = logging.getLogger(__name__)

# ── 内容命令集合（真源在 _const.CONTENT_COMMANDS，此处保留别名兼容 re-export） ──

_CONTENT_COMMANDS = CONTENT_COMMANDS

# ── 命令优先级（值越小越优先） ──

_CMD_PRIORITY_CRITICAL = 0   # PhaseDone, ToolSummary, TOOL_COUNT_INC/DEC, TOOL_FAIL_INC, MAIN_PHASE, SPLASH + 流式内容命令 REASONING/CONTENT（_STREAM_CMDS）
_CMD_PRIORITY_HIGH = 1       # SUBAGENT_FRAME, ERROR
_CMD_PRIORITY_NORMAL = 2     # TOOL_OUTPUT, USER_MSG, PARSE_INFO, NOTIFICATION
_CMD_PRIORITY_LOW = 3        # WRITE_LINE, DISPLAY_MSGS

_CRITICAL_CMDS = frozenset({
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.TOOL_COUNT_INC,
    RenderCommand.TOOL_COUNT_DEC,
    RenderCommand.TOOL_FAIL_INC,
    RenderCommand.MAIN_PHASE,
    RenderCommand.SPLASH,
})
# 流式内容命令（REASONING/CONTENT）— 与 PhaseDone 同级优先级（0），
# 通过 PriorityQueue 的 seq 序号保持插入序，确保同批内内容命令先于完成命令
# 出队（修复优先级反转竞态：PhaseDoneCmd 不再先于同批 ReasoningCmd/ContentCmd）。
# 注意：**不加入 _CRITICAL_CMDS** —— push_cmd 的阻塞判定（_get_cmd_id(cmd)
# in _CRITICAL_CMDS）不变，内容命令保持 block=False 非阻塞（避免极端拥塞时
# 阻塞流式发布者）。
_STREAM_CMDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
})
_HIGH_CMDS = frozenset({
    RenderCommand.SUBAGENT_FRAME,
    RenderCommand.ERROR,
})
_NORMAL_CMDS = frozenset({
    RenderCommand.TOOL_OUTPUT,
    RenderCommand.USER_MSG,
    RenderCommand.PARSE_INFO,
    RenderCommand.NOTIFICATION,
})
_LOW_CMDS = frozenset({
    RenderCommand.WRITE_LINE,
    RenderCommand.DISPLAY_MSGS,
})


def _get_cmd_priority(cmd: RenderCmd) -> int:
    """获取命令优先级（值越小越优先）。

    REASONING/CONTENT（_STREAM_CMDS）与 PhaseDone 等关键命令同为优先级 0：
    同批命令经 PriorityQueue 的 seq 序号保插入序，使流式内容命令先于
    完成命令（PhaseDoneCmd）出队；阻塞语义由 _CRITICAL_CMDS 独立判定。
    """
    cid = cmd.cid
    if cid in _CRITICAL_CMDS or cid in _STREAM_CMDS:
        return _CMD_PRIORITY_CRITICAL
    if cid in _HIGH_CMDS:
        return _CMD_PRIORITY_HIGH
    if cid in _NORMAL_CMDS:
        return _CMD_PRIORITY_NORMAL
    return _CMD_PRIORITY_LOW


def _get_cmd_id(cmd: RenderCmd) -> int:
    """从 RenderCmd 数据类中提取命令 ID。"""
    return cmd.cid


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
        render_output: "RenderOutput | None" = None,
    ):
        self._renderer = renderer
        self._bb = bottom_bar
        self._cursor_tracker = cursor_tracker
        self._input = input_instance
        self._config: TuiConfig = config or TuiConfig.defaults()
        # 统一输出端口（可选）：队列满/崩溃紧急路径走 RenderOutput.write_emergency
        self._render_output = render_output
        self._cmd_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=self._config.cmd_queue_maxsize)
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
        self._cmd_seq = itertools.count()

    def _write_emergency(self, text: str, stream: str = "stderr") -> None:
        """紧急输出统一出口。

        优先走 RenderOutput.write_emergency（受控 + 限频）；
        未注入 render_output 时回退旧 _emergency_write（兼容测试/降级）。
        """
        if self._render_output is not None:
            self._render_output.write_emergency(text, stream=stream)
        else:
            _emergency_write(text, stream=stream)

    def push_cmd(self, cmd: RenderCmd) -> None:
        """入队渲染命令。

        关键命令（``_CRITICAL_CMDS`` 集合：PhaseDone/ToolSummary/工具计数/
        主阶段/品牌屏）保留阻塞语义（block=True timeout=0.1，尽力不丢，
        非无限阻塞避免卡死发布者）；其余命令非阻塞入队，队列满时丢弃计数。

        方向D 步骤8（2026-07-31）+ P1-2 修复：高优先级命令（SUBAGENT_FRAME/
        ERROR）不再短时阻塞发布者线程（原 block=True timeout=0.1 → 统一
        block=False），与低优先级一致；REASONING/CONTENT 优先级提至 0（与
        PhaseDone 同级，PriorityQueue 以 seq 保插入序 → 同批内容命令先于
        完成命令出队，修复尾部 token 竞态），但仍保持非阻塞（不加入
        _CRITICAL_CMDS）。极端拥塞（队列满 10000 条）时
        丢弃并计数（_cmd_queue_dropped / _consecutive_full 递增 + warning 日志 +
        连续满阈值触发 _write_emergency）。**关键命令（PhaseDone/ToolSummary 等）
        在 push_cmd 内保留阻塞语义**——push_cmd_critical 无生产调用方，
        EventDispatcher 注入的是本方法（push_cmd）；若关键命令走非阻塞路径，
        极端拥塞时丢弃 PhaseDoneCmd 使 ``_rs.close_reasoning()`` 不执行、
        丢弃 ToolSummaryCmd 使 ``_in_tool_group`` 不闭合。
        ``push_cmd_critical``（block=True timeout=1.0）保留供测试/未来调用方使用。
        """
        priority = _get_cmd_priority(cmd)
        blocking = _get_cmd_id(cmd) in _CRITICAL_CMDS
        try:
            if blocking:
                self._cmd_queue.put(
                    (priority, next(self._cmd_seq), cmd),
                    block=True, timeout=0.1,
                )
            else:
                self._cmd_queue.put(
                    (priority, next(self._cmd_seq), cmd), block=False,
                )
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            # P3-10：_consecutive_full / _cmd_queue_dropped 无锁自增——
            # 统计日志不影响渲染正确性（仅用于拥塞诊断），GIL 下自增
            # 原子性足够，不引入额外锁开销。
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            cmd_id = _get_cmd_id(cmd)
            cmd_name = _cmd_name(cmd_id)
            _logger.warning(
                "渲染命令队列已满（%s 条），丢弃命令: %s (优先级=%d)",
                self._cmd_queue.qsize(), cmd_name, priority,
            )
            if self._consecutive_full >= self._config.consecutive_full_threshold:
                _logger.error("渲染输出管线持续拥堵（%d 次连续满队列）", self._consecutive_full)
                if self._consecutive_full % self._config.consecutive_full_threshold == 0:
                    self._write_emergency(
                        f"{ANSI_EMERGENCY_RED}[ChatUI] 渲染队列已满，已丢弃 "
                        f"{self._cmd_queue_dropped} 条命令{ANSI_EMERGENCY_RESET}\n",
                        stream="stderr",
                    )

    def push_cmd_critical(self, cmd: RenderCmd) -> None:
        """入队关键命令 — 阻塞等待以确保绝不丢失。"""
        priority = _CMD_PRIORITY_CRITICAL
        self._cmd_queue.put((priority, next(self._cmd_seq), cmd), block=True, timeout=1.0)
        self._consecutive_full = 0
        self._cmd_event.set()

    @property
    def render_crashed(self) -> bool:
        return self._render_crashed.is_set()

    def is_render_running(self) -> bool:
        """返回 render 线程是否正在运行（公开访问器，收敛私有字段读取）。"""
        return self._render_running

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

    def _phase_render(self, commands: list) -> None:
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
            cmd_id = _get_cmd_id(cmd)
            if self._renderer._is_batchable(cmd):
                batch_end = i + 1
                while batch_end < len(commands):
                    if not self._renderer._is_batchable(commands[batch_end]):
                        break
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
                        "渲染命令 %s 失败", _cmd_name(cmd_id), exc_info=True,
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
    def _has_content_command(commands: list) -> bool:
        for cmd in commands:
            if not cmd:
                continue
            cmd_id = _get_cmd_id(cmd)
            if cmd_id in _CONTENT_COMMANDS:
                return True
        return False

    def _drain_queue(self) -> bool:
        commands: list = []
        # ★ 确认（2026-07-31 方向D）：输入分发（_phase_process_input）与
        #   面板刷新（_phase_pre_update_panels，SubAgentPanel）均在输出锁获取
        #   （drain 阶段锁区间起点）之前执行 —— 面板刷新移出锁外，无锁内
        #   面板刷新阻塞；若未来需快照式渲染再评估。
        self._phase_process_input()
        self._phase_pre_update_panels()
        with _try_acquire_output_lock(name="drain_queue", timeout=self._config.drain_lock_timeout) as locked:
            if not locked:
                return False
            while len(commands) < self._config.max_batch_size:
                try:
                    _, _, cmd = self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                    commands.append(cmd)
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
                _, _, cmd = self._cmd_queue.get_nowait()
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
            self._write_emergency(
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
                self._write_emergency(
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
