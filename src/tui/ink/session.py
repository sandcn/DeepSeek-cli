"""InkSession — Ink 渲染会话：PriorityQueue + render 线程 + 生命周期。

移植 TuiEngine 的队列/优先级/批处理/崩溃恢复逻辑，替换其 Rich 渲染
管线的渲染目标为：RenderCmd → AppModel 状态变更 → 组件树 → Frame →
InkRenderer 非全屏输出。

关键不变式（与 TuiEngine 一致）：
  - 命令优先级（_CRITICAL_CMDS / _STREAM_CMDS / ...）+ seq 保插入序
  - 关键命令（PhaseDone/ToolSummary/工具计数/主阶段/品牌屏）push 阻塞语义
  - 命令批量处理（max_batch_size）
  - 单 render 线程模型 + 崩溃自动恢复（max_recover_attempts）
  - render 循环：drain → clear → wait（event 清-wait 顺序防忙等）
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
from src.tui._screen import TerminalWidthCache, _get_terminal_size
from .reconciler import Reconciler
from .renderer import InkRenderer
from . import components as _components
from src.tui._input import _compute_cursor_visual_pos

if TYPE_CHECKING:
    from src.tui.events.event_bus import DisplayEventBus

_logger = logging.getLogger(__name__)

# ── 内容命令集合（真源在 _const.CONTENT_COMMANDS） ──────────
_CONTENT_COMMANDS = CONTENT_COMMANDS

# ── 命令优先级（值越小越优先） ──────────────────────────────
_CMD_PRIORITY_CRITICAL = 0
_CMD_PRIORITY_HIGH = 1
_CMD_PRIORITY_NORMAL = 2
_CMD_PRIORITY_LOW = 3

_CRITICAL_CMDS = frozenset({
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.TOOL_COUNT_INC,
    RenderCommand.TOOL_COUNT_DEC,
    RenderCommand.TOOL_FAIL_INC,
    RenderCommand.MAIN_PHASE,
    RenderCommand.SPLASH,
    RenderCommand.TOOL_OPEN,
    RenderCommand.TOOL_CLOSE,
})
_STREAM_CMDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    # 工具输出与 Open/Close（prio0）同序——否则 Close 先于 Output 出队，
    # 输出落到无名新 box（每工具 box 增量刷新依赖此顺序）。
    RenderCommand.TOOL_OUTPUT,
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


def _get_cmd_id(cmd: RenderCmd) -> int:
    return cmd.cid


def _get_cmd_priority(cmd: RenderCmd) -> int:
    """获取命令优先级（与 TuiEngine._get_cmd_priority 语义一致）。"""
    cid = cmd.cid
    if cid in _CRITICAL_CMDS or cid in _STREAM_CMDS:
        return _CMD_PRIORITY_CRITICAL
    if cid in _HIGH_CMDS:
        return _CMD_PRIORITY_HIGH
    if cid in _NORMAL_CMDS:
        return _CMD_PRIORITY_NORMAL
    return _CMD_PRIORITY_LOW


def _cmd_name(cid: int) -> str:
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


class InkSession:
    """Ink 渲染会话。

    Args:
        model: AppModel 实例。
        apply_cmd: ``(model, RenderCmd) -> None`` 命令应用函数（app.apply）。
        build_tree: ``(model) -> Element`` 组件树构建函数（app.app）。
        width_cache: 终端宽度缓存。
        config: TuiConfig。
        stream: 输出流（InkRenderer 用）。
    """

    def __init__(
        self,
        model,
        apply_cmd: Callable | None = None,
        build_tree: Callable | None = None,
        width_cache: TerminalWidthCache | None = None,
        config: TuiConfig | None = None,
        stream=None,
    ):
        self._model = model
        self._apply_fn = apply_cmd
        self._build_tree = build_tree
        self._config: TuiConfig = config or TuiConfig.defaults()
        self._width_cache = width_cache or TerminalWidthCache.get_default()

        self._reconciler = Reconciler(schedule_callback=self._schedule_render)
        self._root_fiber = self._reconciler.create_root()
        self._ink_renderer = InkRenderer(stream=stream)

        # ── 队列 / 线程 ──
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
        # ★ 脏标记：模型有变更（命令应用/输入/重绘请求）时置位，
        #   空闲时跳过渲染（避免 10Hz 全量重建整棵树 → CPU 100%）
        self._dirty: bool = False
        self._recover_attempts: int = 0
        self._recovering_event: threading.Event = threading.Event()
        self._render_version: int = 0
        self._cmd_seq = itertools.count()
        self._input = None  # Phase F 接线注入

    # ── 注入 ─────────────────────────────────────────

    def set_input(self, input_instance) -> None:
        """注入 Input 实例（render 循环输入分发）。"""
        self._input = input_instance

    def set_model(self, model) -> None:
        """替换模型（测试用）。"""
        self._model = model

    def set_build_tree(self, fn: Callable | None) -> None:
        self._build_tree = fn

    # ── 紧急输出 ─────────────────────────────────────

    def _write_emergency(self, text: str, stream: str = "stderr") -> None:
        try:
            f = sys.__stderr__ if stream == "stderr" else sys.__stdout__
            f.write(text)
            f.flush()
        except (OSError, ValueError):
            pass

    # ── 命令入队 ─────────────────────────────────────

    def push_cmd(self, cmd: RenderCmd) -> None:
        """入队渲染命令（阻塞语义与 TuiEngine.push_cmd 一致）。"""
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
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            cmd_id = _get_cmd_id(cmd)
            _logger.warning(
                "渲染命令队列已满（%s 条），丢弃命令: %s (优先级=%d)",
                self._cmd_queue.qsize(), _cmd_name(cmd_id), priority,
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

    # ── 公开访问器 ───────────────────────────────────

    @property
    def render_crashed(self) -> bool:
        return self._render_crashed.is_set()

    def is_render_running(self) -> bool:
        return self._render_running

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._panel_refresh_cb = callback

    def request_bottom_redraw(self) -> None:
        self._bottom_redraw_requested.set()
        self._dirty = True
        self._cmd_event.set()
        # ★ render 线程停止时（suspend 期间交互工具如 user_select），
        #   同步渲染一帧使补全弹窗立即可见——否则模型更新无人渲染。
        if not self._render_running:
            try:
                self._render_frame()
            except Exception:
                _logger.debug("request_bottom_redraw 同步渲染异常", exc_info=True)

    # ── 输入更新（echo 回调） ─────────────────────────

    def update_input(self, text: str, cursor_pos: int = -1) -> None:
        """更新模型输入状态并请求重渲染（Input echo 回调）。"""
        if self._model is None:
            return
        self._model.input_text = text
        self._model.input_cursor = len(text) if cursor_pos < 0 else cursor_pos
        self._request_render()

    def _request_render(self) -> None:
        """请求下一帧渲染（标记脏 + 唤醒循环）。"""
        self._bottom_redraw_requested.set()
        self._dirty = True
        self._cmd_event.set()

    def _schedule_render(self) -> None:
        """hooks 状态更新回调：请求重渲染。"""
        self._request_render()

    # ── 生命周期 ─────────────────────────────────────

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
        # 请求首帧渲染：resume 后立即重绘（prev 已重置 → 全量写入）
        self._bottom_redraw_requested.set()
        self._dirty = True
        self._cmd_event.set()
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
        # 重置渲染器状态（suspend/resume 路径：下次 start 全量重绘）
        self._ink_renderer.suspend()
        self._drain_queue_safe()

    def flush(self, timeout: float | None = 5.0) -> None:
        """等待队列处理完成（超时后排空）。"""
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

    def suspend(self) -> None:
        """暂停渲染（供交互工具独占终端）。

        先 flush 提交 live 区，再停止 render 线程并重置渲染器状态，
        使工具独占终端（非全屏模型：文档已是 scrollback 行，无 DECSTBM）。

        suspend 后交互工具（user_select）经 request_bottom_redraw 触发
        **同步渲染**（render 线程已停）显示补全弹窗——光标先定位到终端底部，
        使同步渲染从干净位置开始。
        """
        try:
            self.flush()
        except Exception:
            _logger.debug("suspend flush 异常", exc_info=True)
        self._render_running = False
        if self._render_thread is not None:
            self._render_thread.join(timeout=2.0)
        self._ink_renderer.suspend()
        self._drain_queue_safe()
        # 定位光标到终端底部：交互工具同步渲染弹窗的起点
        try:
            from src.tui._screen import cursor_goto, _get_terminal_size
            _, h = _get_terminal_size()
            self._ink_renderer._stream.write(cursor_goto(max(1, h), 1))
            self._ink_renderer._stream.flush()
        except Exception:
            _logger.debug("suspend 光标定位异常", exc_info=True)

    def resume(self) -> None:
        """恢复渲染：重置渲染器后重新渲染组件树。"""
        if self._render_running:
            return
        self._render_running = True
        self._ink_renderer.reset()
        # 立即渲染一帧（从当前位置重绘文档）
        try:
            self._render_frame()
        except Exception:
            _logger.debug("resume 立即渲染异常", exc_info=True)
        self._render_thread = threading.Thread(target=self._render, daemon=True)
        self._render_thread.start()

    def ensure_cursor_upper(self) -> None:
        """非全屏模型无 DECSTBM：no-op（保留 API 兼容）。"""
        pass

    # ── 渲染循环 ─────────────────────────────────────

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

    def _drain_queue(self) -> bool:
        """单帧处理：输入分发 → 面板刷新 → 排空命令 → 应用 → 渲染 → 光标。"""
        self._phase_process_input()
        self._phase_pre_update_panels()
        commands: list = []
        with _try_acquire_output_lock(name="ink_session.drain_queue", timeout=self._config.drain_lock_timeout) as locked:
            if not locked:
                return False
            while len(commands) < self._config.max_batch_size:
                try:
                    _, _, cmd = self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                    commands.append(cmd)
                except queue.Empty:
                    break
            changed = bool(commands)
            if commands:
                self._apply_commands(commands)
            if self._should_render(changed):
                try:
                    self._render_frame()
                except Exception:
                    _logger.warning("渲染帧失败", exc_info=True)
            return changed

    def _should_render(self, changed: bool) -> bool:
        """是否需渲染本帧：脏标记 + 10Hz 拍批处理。

        事件（命令/重绘请求）标记脏并唤醒循环，但不立即渲染——与下一个
        10Hz 拍一起渲染（批处理）。**空闲（无脏）时跳过渲染**，避免固定
        10Hz 全量重建整棵聊天树（大历史下 CPU 100%）。

        Returns:
            True — 本拍渲染（脏且 render_interval 已到期）。
        """
        now = time.monotonic()
        force = self._bottom_redraw_requested.is_set()
        self._bottom_redraw_requested.clear()
        if changed:
            self._dirty = True  # 本批命令已应用 → 标记脏
        if not (self._dirty or force):
            return False  # 空闲且无变化：跳过渲染（CPU ~0）
        if now - self._last_bottom_redraw >= self._config.render_interval:
            self._dirty = False
            self._last_bottom_redraw = now
            return True
        return False

    def _apply_commands(self, commands: list) -> None:
        """批量应用命令到模型。"""
        if self._apply_fn is None:
            return
        for cmd in commands:
            try:
                self._apply_fn(self._model, cmd)
            except Exception:
                _logger.warning("应用命令 %s 失败", _cmd_name(_get_cmd_id(cmd)), exc_info=True)

    def _render_frame(self) -> None:
        """构建组件树 → 调和 → 渲染 → 输出 → 光标。"""
        if self._build_tree is None:
            return
        width = self._width_cache.get_width()
        if self._model is not None:
            self._model.width = width  # 渲染器 TOC 边框宽度
        element = self._build_tree(self._model, width)
        self._reconciler.render(self._root_fiber, element, width, self._width_cache.get_height())
        frame = _components.render_frame(self._root_fiber, width)
        self._ink_renderer.render(frame)
        self._position_cursor()

    # ── 阶段 ─────────────────────────────────────────

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

    # ── 光标 ─────────────────────────────────────────

    def _position_cursor(self) -> None:
        """渲染后定位输入光标（从文档底部相对移动）。"""
        if self._model is None:
            return
        fiber = self._find_input_fiber(self._root_fiber)
        if fiber is None:
            return
        box = fiber.layout_box
        if box is None:
            return
        text = str(fiber.props.get("text", ""))
        cursor_pos = int(fiber.props.get("cursor_pos", -1))
        prompt = str(fiber.props.get("prompt", "> "))
        completion = fiber.props.get("completion")
        popup_height = 0
        if completion is not None and getattr(completion, "visible", False) and getattr(completion, "items", None):
            popup_height = len(completion.items) + 2
        max_input = max(1, box.w - len(prompt))
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        # 输入文本起始行 = box.y + popup_height + 1（上分隔线之后）
        row = box.y + popup_height + 1 + vis_row + 1
        col = box.x + len(prompt) + vis_col + 1
        try:
            self._ink_renderer.place_cursor(row, col)
        except Exception:
            _logger.debug("place_cursor 异常", exc_info=True)

    def _find_input_fiber(self, root_fiber):
        """在 host 树中查找输入区 fiber（type == 'input-area'）。"""
        from .fiber import Fiber

        def walk(f: Fiber | None):
            f2 = f
            while f2 is not None:
                if f2.is_host and f2.type == "input-area":
                    return f2
                r = walk(f2.child)
                if r is not None:
                    return r
                f2 = f2.sibling
            return None

        return walk(root_fiber)

    # ── 崩溃恢复 ─────────────────────────────────────

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


__all__ = [
    "InkSession",
    "_get_cmd_priority",
    "_get_cmd_id",
    "_CRITICAL_CMDS",
    "_STREAM_CMDS",
    "_CONTENT_COMMANDS",
]
