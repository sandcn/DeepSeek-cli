"""统一渲染器 — TuiEngine + TuiRenderer + EventDispatcher（精简单文件）。

合并旧 engine/engine.py、engine/renderer.py、engine/renderer_base.py、
engine/dispatcher.py 到统一模块，使用 ``_screen.py`` 纯 ANSI 序列替代 blessed，
使用 dict 分发替代 ComponentRegistry。

设计模式:
  - 观察者（Observer）— EventDispatcher 订阅 DisplayEventBus，转换为 RenderCommand 入队
  - 策略（Strategy）— ``render()`` 通过命令 ID 字典分发到不同 ``_do_*`` 策略方法
"""

from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Callable

from src.tui._const import (
    RenderCommand,
    _CLEAR_PARSE_LINE,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
from src.tui._config import TuiConfig
from src.tui._locks import _try_acquire_output_lock
from src.tui._screen import cursor_goto, write_stdout

if TYPE_CHECKING:
    from src.renderer.output import OutputAdapter
    from src.tui._bottom_bar import _BottomBar
    from src.tui._input import Input
    from src.tui.consumer.chat_config import ChatConfig
    from src.tui.events.event_types import (
        ContentChunkEvent,
        DisplayEvent,
        ModelPhaseEvent,
        OutputEvent,
        ParseInfoDoneEvent,
        ParseInfoEvent,
        PhaseDoneEvent,
        ReasoningChunkEvent,
        ToolDoneEvent,
        ToolOutputChunkEvent,
        ToolParsingEvent,
        ToolStartedEvent,
        ToolSummaryEvent,
    )
    from src.tui.state.render_state import ChatRenderState
    from src.tui._cursor_tracker import CursorTracker

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 工具函数（内联自 engine/utils.py）
# ═══════════════════════════════════════════════════════════

def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。"""
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


def _emergency_write(text: str, stream: str = "stdout") -> None:
    """紧急输出 — 绕过 OutputAdapter 直写终端。

    用于 render 线程崩溃、队列满等无法通过正常路径输出的场景。
    """
    f = sys.__stdout__ if stream == "stdout" else sys.__stderr__
    f.write(text)
    f.flush()


# ═══════════════════════════════════════════════════════════
# TuiEngine — 渲染引擎
# ═══════════════════════════════════════════════════════════

class TuiEngine:
    """渲染引擎 — render 线程 + Queue 命令队列 + 四阶段渲染循环。

    崩溃自动恢复：render 线程异常崩溃时自动重建（最多 3 次）。
    """

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

    def __init__(
        self,
        renderer: "TuiRenderer",
        bottom_bar: "_BottomBar",
        cursor_tracker: "CursorTracker | None" = None,
        input_instance: "Input | None" = None,
    ):
        self._renderer = renderer
        self._bb = bottom_bar
        self._cursor_tracker = cursor_tracker
        self._input = input_instance
        self._config: TuiConfig = TuiConfig.defaults()
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
        # 原子性检查：_render_running 已为 True 说明线程已在运行，防止竞态重复启动
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
                # 检查是否有新线程（崩溃恢复后）替代了旧线程
                if self._render_version != version:
                    self._render_running = False
                    continue  # 重新尝试 join 新线程
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
        """处理输入事件（在 render_lock 外调用，可安全执行 I/O 操作）。

        此方法由 _drain_queue() 在 with _try_acquire_output_lock 块外调用，
        确保 Input.process_events() 不持 render_lock，避免锁顺序反转死锁。
        """
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
        # 按命令类型分批渲染：收集连续可批处理命令 → batch_write，不可批处理命令单独执行
        i = 0
        while i < len(commands):
            cmd = commands[i]
            if cmd and self._renderer._is_batchable(cmd[0]):
                # 收集连续的可批处理命令
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
            if cmd and cmd[0] in TuiEngine._CONTENT_COMMANDS:
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
                    # ★ 始终在 wait 前 clear event，防止 _phase_pre_update_panels() 在 drain 过程中
                    #   推入 SUBAGENT_FRAME 后 set 了 event，导致 wait() 立即返回形成忙等循环
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


# ═══════════════════════════════════════════════════════════
# TuiRenderer — 内容渲染器（dict 分发，替代 ComponentRegistry）
# ═══════════════════════════════════════════════════════════

class TuiRenderer:
    """聊天域内容渲染器 — 执行 RenderCommand 并直接输出。

    使用 dict 分发命令 ID 到对应的 _do_* 方法，
    替代已删除的 ComponentRegistry 装饰器模式。
    """

    def __init__(
        self,
        rs: "ChatRenderState",
        output_adapter: "OutputAdapter",
        bottom_bar: "_BottomBar",
        on_display_messages: Callable[..., None] | None = None,
        cursor_tracker: "CursorTracker | None" = None,
    ):
        self._rs = rs
        self._adapter = output_adapter
        self._bb = bottom_bar
        self._on_display_messages = on_display_messages
        self._tracker = cursor_tracker
        self._in_tool_group = False

        # 命令分发字典 (替代 ComponentRegistry)
        self._handlers: dict[int, Callable] = {
            RenderCommand.NOTIFICATION: self._do_notification,
            RenderCommand.WRITE_LINE: self._do_write_line,
            RenderCommand.ERROR: self._do_error,
            RenderCommand.SPLASH: self._do_splash,
            RenderCommand.SUBAGENT_FRAME: self._do_subagent_frame,
            RenderCommand.REASONING: self._do_reasoning,
            RenderCommand.CONTENT: self._do_content,
            RenderCommand.PHASE_DONE: self._do_phase_done,
            RenderCommand.TOOL_COUNT_INC: self._do_tool_count_inc,
            RenderCommand.TOOL_COUNT_DEC: self._do_tool_count_dec,
            RenderCommand.TOOL_FAIL_INC: self._do_tool_fail_inc,
            RenderCommand.MAIN_PHASE: self._do_main_phase,
            RenderCommand.TOOL_OUTPUT: self._do_tool_output,
            RenderCommand.TOOL_SUMMARY: self._do_tool_summary,
            RenderCommand.PARSE_INFO: self._do_parse_info,
            RenderCommand.USER_MSG: self._do_user_message,
            RenderCommand.DISPLAY_MSGS: self._do_display_messages,
        }

    # ── 批量渲染支持 ──────────────────────────────

    # 可批量渲染的命令集合：它们都使用 Text.from_ansi() + self._adapter.write() 模式
    _BATCHABLE_COMMANDS = frozenset({
        RenderCommand.NOTIFICATION,
        RenderCommand.WRITE_LINE,
        RenderCommand.ERROR,
        RenderCommand.TOOL_OUTPUT,
        RenderCommand.TOOL_SUMMARY,
        RenderCommand.USER_MSG,
    })

    def _is_batchable(self, cid: int) -> bool:
        """判断命令 ID 是否可批量渲染。"""
        return cid in self._BATCHABLE_COMMANDS

    def render_batch(self, commands: list[tuple]) -> None:
        """批量渲染连续的可批处理命令 — 锁外预渲染 + 锁内合并写入。

        将多个可批处理命令的 renderable 在锁外逐个渲染为 ANSI 字符串，
        然后通过 adapter.batch_write() 在单次锁获取内合并写入 + flush。
        保持命令渲染顺序与输入一致，并正确驱动 _in_tool_group 状态机。
        """
        from rich.text import Text

        renderables: list = []
        for cmd in commands:
            if not cmd:
                continue
            cid = cmd[0]

            if cid == RenderCommand.NOTIFICATION:
                text = cmd[1]
                renderables.append(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
                self._record_lines(1)

            elif cid == RenderCommand.WRITE_LINE:
                text = cmd[1]
                renderables.append(Text.from_ansi(text))
                self._record_lines(1)

            elif cid == RenderCommand.ERROR:
                message = cmd[1]
                renderables.append(Text.from_ansi(
                    f"  \033[1;38;5;196m!\033[0m \033[38;5;196m{message}\033[0m"
                ))
                self._record_lines(1)

            elif cid == RenderCommand.TOOL_OUTPUT:
                text = cmd[1]
                if not self._in_tool_group:
                    self._in_tool_group = True
                    renderables.append(Text.from_ansi(
                        f"  \033[38;5;23m\u256d\u2500\u2500 工具调用 \u2500\u2500\u256e\033[0m"
                    ))
                renderables.append(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
                self._record_lines(1)

            elif cid == RenderCommand.TOOL_SUMMARY:
                successful = cmd[1]
                failed = cmd[2]
                if self._in_tool_group:
                    self._in_tool_group = False
                    renderables.append(Text.from_ansi(
                        f"  \033[38;5;23m\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f\033[0m"
                    ))
                self._record_lines(1)

            elif cid == RenderCommand.USER_MSG:
                text = cmd[1]
                renderables.append(Text.from_ansi(
                    f"\n  \033[1;38;5;81m>\033[0m \033[38;5;252m{text}\033[0m\n"
                ))
                self._record_lines(2)

        if renderables:
            self._adapter.batch_write(renderables)

    @property
    def output_adapter(self) -> "OutputAdapter":
        return self._adapter

    def _record_lines(self, n: int) -> None:
        if self._tracker is not None:
            self._tracker.record_newlines(n)

    def render(self, cmd: tuple) -> None:
        if not cmd:
            return
        cid = cmd[0]
        handler = self._handlers.get(cid)
        if handler is None:
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return
        try:
            handler(*cmd[1:])
        except TypeError:
            _logger.error(
                "渲染命令 %s 参数错误: 期望签名与传入参数不匹配",
                _cmd_name(cid),
            )

    # ═══════════════════════════════════════════════════════
    # 框架级命令
    # ═══════════════════════════════════════════════════════

    def _do_notification(self, text: str) -> None:
        """渲染通用通知消息。"""
        from rich.text import Text
        self._adapter.write(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
        self._record_lines(1)

    def _do_write_line(self, text: str) -> None:
        """直接写入一行文本。"""
        from rich.text import Text
        self._adapter.write(Text.from_ansi(text))
        self._record_lines(1)

    def _do_error(self, message: str) -> None:
        """渲染系统错误消息。"""
        from rich.text import Text
        self._adapter.write(Text.from_ansi(
            f"  \033[1;38;5;196m!\033[0m \033[38;5;196m{message}\033[0m"
        ))
        self._record_lines(1)

    def _do_splash(self) -> None:
        """渲染启动品牌屏。"""
        from rich.text import Text
        model_name = getattr(self._bb, '_model_name', '')
        splash_text = f"\n  \033[1;38;5;45mDeepSeek CLI\033[0m"
        if model_name:
            splash_text += f" \033[38;5;242m· {model_name}\033[0m"
        splash_text += "\n"
        self._adapter.write(Text.from_ansi(splash_text))
        self._record_lines(2)

    def _do_subagent_frame(self, frame_lines: tuple) -> None:
        # 空列表 = 清除面板
        if isinstance(frame_lines, list) and not frame_lines:
            if hasattr(self._bb, 'set_subagent_frame'):
                self._bb.set_subagent_frame([])
            return
        if not frame_lines:
            return
        # 兼容两种格式：
        #   旧: (lines, scroll_end, last_lines, clear_eol) — 4 元组
        #   新: list[str] — 直接提供行列表
        if isinstance(frame_lines, (list, tuple)) and frame_lines and isinstance(frame_lines[0], str):
            # 新格式: 直接是字符串列表
            lines = frame_lines
        elif len(frame_lines) >= 4 and isinstance(frame_lines[0], (list, tuple)):
            # 旧格式: 4 元组，第一个元素是行列表
            lines = frame_lines[0]
        else:
            return
        if hasattr(self._bb, 'set_subagent_frame'):
            self._bb.set_subagent_frame(list(lines))

    # ═══════════════════════════════════════════════════════
    # 聊天域命令
    # ═══════════════════════════════════════════════════════

    def _do_reasoning(self, text: str) -> None:
        """推理内容流式块。"""
        rr = self._rs.get_reasoning()
        if rr is not None:
            rr.write(text)
            self._record_lines(0)

    def _do_content(self, text: str) -> None:
        """回答内容流式块。"""
        cr = self._rs.get_content()
        if cr is not None:
            cr.write(text)
            self._record_lines(0)

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    def _do_tool_count_inc(self) -> None:
        self._bb.increment_tool()

    def _do_tool_count_dec(self) -> None:
        self._bb.decrement_tool()

    def _do_tool_fail_inc(self) -> None:
        self._bb.increment_tool_fail()

    def _do_main_phase(self, phase: str) -> None:
        if phase == "thinking":
            self._rs.reopen_reasoning()
        self._bb.set_main_phase(phase)

    def _do_tool_output(self, text: str) -> None:
        from rich.text import Text
        if not self._in_tool_group:
            self._in_tool_group = True
            self._adapter.write(Text.from_ansi(
                f"  \033[38;5;23m\u256d\u2500\u2500 工具调用 \u2500\u2500\u256e\033[0m"
            ))
            self._record_lines(1)
        self._adapter.write(Text.from_ansi(f"  \033[38;5;242m\u2502\033[0m {text}"))
        self._record_lines(1)

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        from rich.text import Text
        if self._in_tool_group:
            self._in_tool_group = False
            self._adapter.write(Text.from_ansi(
                f"  \033[38;5;23m\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f\033[0m"
            ))
            self._record_lines(1)

    def _do_parse_info(self, tool_names: str, tokens, elapsed: float) -> None:
        if tokens == _CLEAR_PARSE_LINE:
            _emergency_write("\n")
            self._record_lines(1)
            return
        if isinstance(tokens, (int, float)):
            tokens_str = f"{tokens}t" if math.isfinite(tokens) else "?"
        else:
            tokens_str = str(tokens)
        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        _emergency_write(output)

    def _do_user_message(self, text: str) -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(
            f"\n  \033[1;38;5;81m>\033[0m \033[38;5;252m{text}\033[0m\n"
        ))
        self._record_lines(2)

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        if self._on_display_messages is not None:
            self._on_display_messages(messages, speed=speed)
        self._record_lines(1)


# ═══════════════════════════════════════════════════════════
# EventDispatcher — 事件→命令映射
# ═══════════════════════════════════════════════════════════

class EventDispatcher:
    """DisplayEvent → RenderCommand 过滤+入队。

    将 12 种 DisplayEvent 类型映射到对应的 RenderCommand 并推入命令队列。
    所有事件经过 label/source 过滤后才入队，非主 Agent 事件被丢弃。
    """

    def __init__(self, push_cmd: Callable[[tuple], None], config: "ChatConfig | None" = None):
        self._push_cmd = push_cmd
        self._config = config
        self._max_error_length = TuiConfig.defaults().max_error_length
        self._custom_handlers: dict[type, Callable] = {}

    def _is_agent_source(self, source: str | None) -> bool:
        if source is None:
            return False
        if self._config:
            main_source = self._config.main_source
        else:
            from src.tui.consumer.chat_config import ChatConfig
            main_source = ChatConfig.defaults().main_source
        return source == main_source or source.startswith("agent-")

    def register_handler(self, event_type: type, handler_method: Callable) -> None:
        self._custom_handlers[event_type] = handler_method

    def list_handlers(self) -> dict[type, Callable]:
        from src.tui.events import event_types as _ET
        result: dict[type, Callable] = {
            _ET.ReasoningChunkEvent: self._on_reasoning_chunk,
            _ET.ContentChunkEvent: self._on_content_chunk,
            _ET.PhaseDoneEvent: self._on_phase_done,
            _ET.ToolParsingEvent: self._on_tool_parsing,
            _ET.ToolStartedEvent: self._on_tool_started,
            _ET.ToolDoneEvent: self._on_tool_done,
            _ET.ToolOutputChunkEvent: self._on_tool_output,
            _ET.ParseInfoEvent: self._on_parse_info,
            _ET.ParseInfoDoneEvent: self._on_parse_info_done,
            _ET.OutputEvent: self._on_output,
            _ET.ModelPhaseEvent: self._on_model_phase,
            _ET.ToolSummaryEvent: self._on_tool_summary,
        }
        result.update(self._custom_handlers)
        return result

    # ── 事件处理器 ────────────────────────────────

    def _on_reasoning_chunk(self, event: "ReasoningChunkEvent") -> None:
        if self._config:
            main_label = self._config.main_label
        else:
            from src.tui.consumer.chat_config import ChatConfig
            main_label = ChatConfig.defaults().main_label
        if event.label != main_label:
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event: "ContentChunkEvent") -> None:
        if self._config:
            main_label = self._config.main_label
        else:
            from src.tui.consumer.chat_config import ChatConfig
            main_label = ChatConfig.defaults().main_label
        if event.label != main_label:
            return
        if not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_tool_parsing(self, event: "ToolParsingEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.MAIN_PHASE, "parsing"))

    def _on_phase_done(self, event: "PhaseDoneEvent") -> None:
        if self._config:
            main_label = self._config.main_label
        else:
            from src.tui.consumer.chat_config import ChatConfig
            main_label = ChatConfig.defaults().main_label
        if event.label != main_label:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    @staticmethod
    def _is_subagent_label(label: str) -> bool:
        """检查 label 是否属于 SubAgent（label 形如 agent-1, agent-2）。"""
        return bool(label and label.startswith("agent-"))

    def _on_tool_started(self, event: "ToolStartedEvent") -> None:
        if not self._is_agent_source(event.source) and not self._is_subagent_label(event.label):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event: "ToolDoneEvent") -> None:
        if not self._is_agent_source(event.source) and not self._is_subagent_label(event.label):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))
        else:
            self._push_cmd((RenderCommand.TOOL_COUNT_DEC,))

    def _on_tool_output(self, event: "ToolOutputChunkEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event: "ParseInfoEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event: "ParseInfoDoneEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event: "OutputEvent") -> None:
        if not event.text:
            return
        self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event: "ModelPhaseEvent") -> None:
        if self._config:
            main_label = self._config.main_label
        else:
            from src.tui.consumer.chat_config import ChatConfig
            main_label = ChatConfig.defaults().main_label
        if event.label != main_label:
            return
        if event.phase != "error":
            self._push_cmd((RenderCommand.MAIN_PHASE, event.phase))
            return
        if not event.info:
            return
        # 内联截断（text_utils 已删除）
        _info = event.info
        if len(_info) > self._max_error_length:
            _info = _info[:self._max_error_length] + "..."
        info = _info
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event: "ToolSummaryEvent") -> None:
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))
