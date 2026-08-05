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

import heapq
import itertools
import logging
import queue
import sys
import threading
import time
from typing import Callable

from src.tui._const import (
    RenderCommand,
    RenderCmd,
    CONTENT_COMMANDS,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
from src.tui._config import TuiConfig
from src.renderer._locks import _try_acquire_output_lock
from src.tui._screen import (
    TerminalWidthCache,
    _get_terminal_size,
    cursor_goto,
    process_sigwinch,
)
from .reconciler import Reconciler
from .renderer import InkRenderer
from . import components as _components
from . import hooks as _hooks
from .element import Element
from src.tui._input import _compute_input_layout, _cursor_visual_from_layout
from src.tui.app.input_area import (
    _completion_height,
    _is_search_active,
)

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
    # TOOL_OUTPUT 已在 _STREAM_CMDS（prio 0）——此处不再重复配置（方向3：
    # 重复配置误导——_get_cmd_priority 先查 STREAM，TOOL_OUTPUT 恒为 prio 0）。
    RenderCommand.USER_MSG,
    RenderCommand.PARSE_INFO,
    RenderCommand.NOTIFICATION,
})
_LOW_CMDS = frozenset({
    RenderCommand.WRITE_LINE,
    RenderCommand.DISPLAY_MSGS,
    # 与 WRITE_LINE 同优先级（低优先级批量投递，不抢占流式内容）
    RenderCommand.SUBAGENT_MARKDOWN,
})

#: BUG-39：崩溃恢复后视为「稳定」的最小运行时长（秒）——恢复成功且持续运行
#: 超过该阈值后复位 ``_recover_attempts``（防长时间运行后恢复预算耗尽）。
_RECOVER_STABLE_SECS = 60.0


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
        # ★ 增量渲染屏幕坐标（方向1）：渲染器接收终端屏幕高度——文档高于屏幕
        #   时按屏幕坐标跟踪物理光标（防 cursor_up 越出屏幕顶部错位）、可见区
        #   上方（滚动区）的行跳过重写（头部动画不再引发整帧重写）。
        self._ink_renderer = InkRenderer(
            stream=stream, height=self._width_cache.get_height(),
        )

        # ── 队列 / 线程 ──
        self._cmd_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=self._config.cmd_queue_maxsize)
        self._cmd_event = threading.Event()
        self._render_thread: threading.Thread | None = None
        self._render_running = False
        self._consecutive_full = 0
        # ★ 方向2 P7：连续渲染失败计数（_drain_queue 渲染异常指数退避基准；
        #   成功渲染后复位 0）
        self._consecutive_render_failures = 0
        # ★ BUG-39（review 方向）：上次崩溃恢复时间（monotonic）——恢复成功后
        #   置位；稳定运行超过 ``_RECOVER_STABLE_SECS`` 后复位 ``_recover_attempts``
        #   （修复前计数永不复位：进程运行数小时后的第 max_recover_attempts 次
        #   偶发崩溃将永久终止渲染线程，UI 冻结）。
        self._last_recover_time: float = 0.0
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
        # use_input router 发布缓存（_input 未注入时记录，set_input 后补发）
        self._pending_input_router = None
        _hooks.set_input_router_callback(self._on_input_router)
        # ★ useApp 控制（方向B 步骤10）：session 注入 exit/clear 回调
        self._exit_requested = False
        _hooks.set_app_control({"exit": self.request_exit, "clear": self.request_clear})
        # ★ useStdin/useStdout/useStderr（完善 react ink）：session 注入惰性
        #   访问器——stdin 为 Input 实例（set_input 后可用），stdout 为渲染器
        #   输出流，stderr 为 sys.__stderr__（紧急路径一致）。
        _hooks.set_std_accessors(
            lambda: self._input,
            lambda: getattr(self._ink_renderer, "_stream", None),
            lambda: sys.__stderr__,
        )
        # ★ React Ink v6 hooks（方向 E）：session 注入 window size accessor /
        #   cursor 定位 / 渲染 flush / 终端挂起 回调——useWindowSize/useCursor/
        #   useApp（waitUntilRenderFlush/suspendTerminal）读取。
        _hooks.set_window_size_accessor(
            lambda: (self._width_cache.get_width(), self._width_cache.get_height())
        )
        _hooks.set_cursor_position_fn(self._set_ink_cursor_position)
        _hooks.set_render_flush_fn(self._wait_render_flush)
        _hooks.set_suspend_terminal_fn(self._suspend_terminal)
        # ★ P5：input-area fiber 引用缓存（方向2 P5）——_render_frame 仅在失效时
        #   重建（None/deleted/类型不符），_position_cursor 复用（避免每帧全树
        #   递归查找 input-area）。
        self._input_fiber = None
        # ★ 方向6：上次渲染帧宽度（resize 后向开放通道 renderer 传播 set_width；
        #   初始 0 → 首帧必触发传播，renderer 创建时已用当前宽度，重复 set_width
        #   幂等无副作用）。
        self._last_render_width: int = 0
        # ★ 增量渲染屏幕高度（方向1）：上次传播给 InkRenderer 的高度（初始 0 →
        #   首帧必触发 set_height 同步，与 renderer 创建时已用当前高度幂等）。
        self._last_render_height: int = 0
        # ★ 方向3（resize 全量刷新）：终端尺寸变化（width/height 任一）置位，
        #   _render_frame 消费后重置渲染器 prev——resize 后全量重写而非增量 diff。
        self._resize_pending: bool = False
        # 系统监控（CPU/MEM；每 2 秒刷新输入区顶部分隔线显示）
        self._system_monitor = None
        self._last_sys_stats_time: float = 0.0
        self._sys_stats_interval: float = 2.0

    # ── 注入 ─────────────────────────────────────────

    def set_input(self, input_instance) -> None:
        """注入 Input 实例（render 循环输入分发）。"""
        self._input = input_instance
        # ★ use_input router 补发：构造期（_input 未注入）发布的 router 缓存于此，
        #   set_input 后补发最新 router，保证 useInput 钩子完整接线。
        if self._pending_input_router is not None and self._input is not None:
            try:
                self._input.set_input_hook_router(self._pending_input_router)
                self._pending_input_router = None
            except Exception:
                _logger.debug("set_input 补发 input router 异常", exc_info=True)

    def _on_input_router(self, router) -> None:
        """use_input composite router 发布回调（reconciler 每帧调用）。

        记录最新 router（供 set_input 补发）；_input 已注入时直接接线
        InputDispatcher.set_input_hook_router（消费端只读注入点）。
        """
        self._pending_input_router = router
        if self._input is not None:
            try:
                self._input.set_input_hook_router(router)
            except Exception:
                _logger.debug("_on_input_router 注入异常", exc_info=True)

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
            # ★ 方向4（队列满 LOW 优先丢弃）：新命令优先级高于 LOW 且队列中
            #   存在 LOW 命令（WRITE_LINE/DISPLAY_MSGS）时腾位——持 mutex 锁内
            #   遍历队列移除至多一个 LOW 项（记录 dropped + warning）后重试 put；
            #   新命令本身为 LOW 或队列无 LOW 项时保持现状丢弃（保护
            #   STREAM/CRITICAL 不丢）。
            evicted = False
            if priority < _CMD_PRIORITY_LOW:
                with self._cmd_queue.mutex:
                    for i, item in enumerate(self._cmd_queue.queue):
                        if item[0] >= _CMD_PRIORITY_LOW:
                            removed = self._cmd_queue.queue.pop(i)
                            # ★ BUG-31（review 方向）：``PriorityQueue`` 底层是
                            #   heapq 数组，任意下标 ``pop`` 后堆序被破坏——后续
                            #   ``heappush``/``heappop`` 在损坏堆上操作可能返回非
                            #   最小项 → 命令优先级/同批顺序错乱（如 PHASE_DONE
                            #   先于 CONTENT 出队致内容通道提前关闭、TOOL_CLOSE
                            #   先于 TOOL_OUTPUT 致输出落到无名新 box）。pop 后
                            #   ``heapq.heapify`` 恢复堆序（O(n)，仅队列满腾位
                            #   罕见路径触发，成本可接受）。
                            heapq.heapify(self._cmd_queue.queue)
                            self._cmd_queue_dropped += 1
                            _logger.warning(
                                "渲染命令队列已满，腾位移除 LOW 命令: %s",
                                _cmd_name(_get_cmd_id(removed[2])),
                            )
                            evicted = True
                            break
            if evicted:
                try:
                    self._cmd_queue.put(
                        (priority, next(self._cmd_seq), cmd), block=False,
                    )
                    self._consecutive_full = 0
                    self._cmd_event.set()
                    return
                except queue.Full:
                    pass  # 并发竞争仍满 → 保持丢弃（不无限循环）
            # 方向2（CRITICAL 不静默丢弃）：blocking（CRITICAL）命令腾位失败后
            # 改走 push_cmd_critical 紧急直写语义（_write_emergency 兜底，绝不
            # 静默丢弃）——PhaseDone/ToolClose 等通道关闭命令丢失会导致通道
            # 永不关闭。非 CRITICAL 保持既有丢弃语义（计数/日志不变）。
            if blocking:
                self.push_cmd_critical(cmd)
                return
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
        """入队关键命令 — 阻塞等待以确保绝不丢失。

        队列满（queue.Full）时**不抛异常**：经紧急路径直写 stderr 兜底
        （BUG-T2），保证"关键命令绝不丢失"语义（非静默丢弃）。
        """
        priority = _CMD_PRIORITY_CRITICAL
        cmd_id = _get_cmd_id(cmd)
        try:
            self._cmd_queue.put((priority, next(self._cmd_seq), cmd), block=True, timeout=1.0)
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            _logger.warning(
                "关键命令队列已满，紧急直写: %s (优先级=%d)",
                _cmd_name(cmd_id), priority,
            )
            self._write_emergency(
                f"{ANSI_EMERGENCY_RED}[ChatUI] 关键命令队列已满，紧急直写: "
                f"{_cmd_name(cmd_id)}{ANSI_EMERGENCY_RESET}\n",
                stream="stderr",
            )

    # ── 公开访问器 ───────────────────────────────────

    @property
    def render_crashed(self) -> bool:
        return self._render_crashed.is_set()

    @property
    def exit_requested(self) -> bool:
        """是否已请求退出（useApp().exit 置位）。"""
        return self._exit_requested

    def request_exit(self) -> None:
        """请求退出（useApp().exit 触发）。

        P3-2（渲染期死锁修复）：**渲染线程内**（组件渲染期调用 useApp().exit）
        仅置位 ``_exit_requested``、延迟到渲染循环本帧结束后退出——直接
        ``stop()`` 会 ``join(timeout=2.0)`` 自身（渲染线程）造成死锁；
        **非渲染线程**调用时同步 ``stop()``（幂等：render 线程未启动/已停止
        时安全返回）。
        """
        self._exit_requested = True
        if threading.current_thread() is self._render_thread:
            # 渲染线程内：仅置位 + 唤醒循环（下一帧退出）
            self._cmd_event.set()
            return
        self.stop()

    def clear_screen(self) -> None:
        """Ctrl+L 清屏（Claude TUI parity 步骤 3.1）。

        清空模型显示状态（``model.reset_display()``，保留 status/输入）→
        渲染器全帧清屏（``full_clear``）→ 立即重建空文档。scrollback 历史
        保留；会话消息内存不受影响。调用方须保证非流式（生成中忽略）。
        """
        if self._model is not None:
            try:
                self._model.reset_display()
            except Exception:
                _logger.debug("clear_screen reset_display 异常", exc_info=True)
        try:
            self._ink_renderer.full_clear()
        except Exception:
            _logger.debug("clear_screen full_clear 异常", exc_info=True)
        try:
            self._render_frame()
        except Exception:
            _logger.debug("clear_screen 重建空文档异常", exc_info=True)

    def request_clear(self) -> None:
        """请求全帧清屏重绘（useApp().clear 触发）。

        非全屏模型无 DECSTBM 清屏——实现为**先清屏再强制全量重绘**：
        ``full_clear()`` 写入 ``clear_screen()``（``\\033[2J\\033[H``）并重置
        渲染状态 → 下一帧从空文档全量写入。修复前仅 ``reset()``（置 prev=None）
        不清屏——新帧比旧文档短时旧行残留在可见区。``reset()`` 保留调用
        （测试契约：渲染器状态重置显式调用）。
        """
        try:
            self._ink_renderer.reset()
        except Exception:
            _logger.debug("request_clear reset 异常", exc_info=True)
        try:
            self._ink_renderer.full_clear()
        except Exception:
            _logger.debug("request_clear full_clear 异常", exc_info=True)
        self.request_bottom_redraw()

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
        #   方向1（suspend 同步渲染竞态修复）：同步渲染路径与 _drain_queue
        #   共用 _try_acquire_output_lock（同一 output lock）——避免 suspend
        #   期间同步渲染与外部输出竞争撕裂；locked=False（锁超时）时跳过
        #   并记 debug（弹窗显示延迟一帧可接受，与 _drain_queue 超时跳过一致）。
        if not self._render_running:
            with _try_acquire_output_lock(
                name="ink_session.sync_render",
                timeout=self._config.drain_lock_timeout,
            ) as locked:
                if not locked:
                    _logger.debug("request_bottom_redraw 同步渲染跳过（输出锁不可用）")
                    return
                try:
                    self._render_frame()
                except Exception:
                    _logger.debug("request_bottom_redraw 同步渲染异常", exc_info=True)

    # ── React Ink v6 hooks 回调（useCursor / useApp 扩展） ──

    def _set_ink_cursor_position(self, position) -> None:
        """useCursor().setCursorPosition：定位终端光标（相对 Ink 输出）。

        position 为 ``{"x": int, "y": int}``（0-based 列/行）——直接调用
        InkRenderer 光标定位（1-based 转换）。None 隐藏光标（简化：重置到
        文档底部——当前框架无 IME 组合光标隐藏协议，文档注明差异）。
        """
        if position is None:
            return
        try:
            col = int(position.get("x", 0))
            row = int(position.get("y", 0))
            self._ink_renderer.place_cursor(max(1, row + 1), max(1, col + 1))
        except Exception:
            _logger.debug("set_ink_cursor_position 异常", exc_info=True)

    def _wait_render_flush(self):
        """useApp().waitUntilRenderFlush：等待渲染 flush 的 awaitable。

        当前渲染模型为同步渲染线程处理命令队列——返回协程：等待队列排空
        且无脏标记（渲染完成）后返回。队列持续生产时最多等待（渲染线程
        停止则退出）。
        """
        async def _waiter():
            import asyncio
            try:
                import time as _t
                deadline = _t.monotonic() + 5.0
                while _t.monotonic() < deadline:
                    if (self._cmd_queue.empty() and not self._dirty) or not self._render_running:
                        break
                    await asyncio.sleep(0.01)
            except Exception:
                _logger.debug("wait_render_flush 异常", exc_info=True)
        return _waiter()

    def _suspend_terminal(self, callback=None):
        """useApp().suspendTerminal：终端挂起（近似）。

        React Ink 语义：将终端交给子进程（editor/less/fzf），结束后恢复并
        全量重绘。当前框架无通用挂起协议——callback 提供时同步执行并请求
        全量清屏重绘；无 callback 时返回 no-op 挂起对象（resume 同步请求
        重绘）。交互工具（user_select/editor）已有独立挂起流程，本方法为
        React Ink 生态组件提供近似入口。
        """
        if callback is not None:
            try:
                result = callback()
                import inspect as _i
                if _i.isawaitable(result):
                    import asyncio
                    asyncio.get_event_loop().run_until_complete(result)
            except Exception:
                _logger.debug("suspend_terminal callback 异常", exc_info=True)
            self.request_clear()
            return None
        return {"resume": self.request_clear}

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
                # BUG-T9：崩溃恢复可能已重启新线程——每轮重新捕获最新线程/版本，
                #   确保版本变化时仍能 join 到新线程（不提前返回）。
                thread = self._render_thread
                version = self._render_version
                if thread is None:
                    break
                thread.join(timeout=2.0)
                if not thread.is_alive():
                    break
                if self._render_version != version:
                    # 崩溃恢复重启了新线程 → 继续下一轮 join 新线程
                    self._render_running = False
                    continue
                break
            # 兜底：循环结束后线程仍存活 → 记 warning 并排空队列（不无限等待）
            if self._render_thread is not None and self._render_thread.is_alive():
                _logger.warning(
                    "stop() 等待 render 线程超时（版本=%d），排空队列后退出",
                    self._render_version,
                )
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
        # ★ PERF-8（防忙循环）：下次允许唤醒的时刻（monotonic）——subagent
        #   执行工具期间高频命令（ToolCountInc/DecCmd、SUBAGENT_FRAME、
        #   ParseInfoCmd 等）持续 ``_cmd_event.set()``：原 ``_cmd_event.wait
        #   (timeout=0.1)`` 被 set 立即唤醒 → 渲染循环失去 10Hz 节流变成
        #   忙循环（每轮循环固定开销 × 高频唤醒 → CPU 100%，实测 5ms 事件
        #   频率下 174Hz/27% CPU）。修复：节流时刻跨循环持久，wait 即使被
        #   唤醒也等到 ``next_loop``——高频命令在队列中批处理（每
        #   render_interval 一轮，堆积 ≤ max_batch_size），渲染循环稳定
        #   10Hz；用户输入/关键命令仍经 ``_drain_queue`` 每轮处理（延迟
        #   ≤ render_interval，与空闲 10Hz 行为一致）。
        next_loop: float = 0.0
        try:
            while self._render_running:
                # ── 节流等待（PERF-8）：等待到 next_loop 才处理 ──
                # event 被高频命令 set 唤醒后**消费并继续等剩余时间**（防
                # 忙循环）；已到 next_loop 或 _drain_queue 本身耗时超间隔时
                # 立即进入处理（渲染跟不上时保持连续运转，不额外等待）。
                now = time.monotonic()
                if now < next_loop:
                    remaining = next_loop - now
                    while remaining > 0:
                        self._cmd_event.wait(timeout=remaining)
                        self._cmd_event.clear()
                        now = time.monotonic()
                        remaining = next_loop - now
                next_loop = max(next_loop, time.monotonic()) + self._config.render_interval
                try:
                    # 方向5（死代码清理）：返回值未使用——has_content 删除。
                    self._drain_queue()
                    self._cmd_event.clear()
                    # ★ BUG-39：稳定运行复位崩溃恢复计数——上次恢复后持续运行
                    #   超过阈值视为已稳定（偶发崩溃重新从头计数），防长时间
                    #   运行后 max_recover_attempts 耗尽导致 UI 永久冻结。
                    if self._recover_attempts > 0 and self._last_recover_time > 0:
                        if time.monotonic() - self._last_recover_time >= _RECOVER_STABLE_SECS:
                            self._recover_attempts = 0
                            self._last_recover_time = 0.0
                    # ★ P3-2：渲染线程内 request_exit 的延迟退出语义——
                    #   仅置位后本帧结束检查此处退出（stop 由外部线程调用或
                    #   自然结束；渲染线程自身不 join）。
                    #   ★ BUG-12：exit 路径渲染线程自置 ``_render_running=False``
                    #   ——request_exit 在渲染线程内不调用 stop（防 join 自身
                    #   死锁），若不置位则线程退出后 ``_render_running`` 恒 True，
                    #   ``start()`` 判 ``_render_running`` 为 True 直接 return →
                    #   无法重启（exit 语义下用户不应重启，但状态一致性应保持：
                    #   exit 后 stop()/start() 行为与「线程已停止」一致）。
                    if self._exit_requested:
                        self._render_running = False
                        break
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
        """单帧处理：SIGWINCH 轮询 → 输入分发 → 面板刷新 → 系统监控 → 排空命令 → 应用 → 渲染 → 光标。"""
        self._phase_process_sigwinch()
        self._phase_process_input()
        self._phase_pre_update_panels()
        self._update_system_stats()
        commands: list = []
        changed = False
        with _try_acquire_output_lock(name="ink_session.drain_queue", timeout=self._config.drain_lock_timeout) as locked:
            if not locked:
                return False
            # 方向1 步骤4（渲染失败 sleep 出锁）：锁块内仅排空 + 应用命令——
            # 渲染与失败处理移出锁块（块内置 render_failed 标记？否——渲染在
            # 锁外直接执行，sleep 退避期间输出锁可被其他写入方获取）。
            while len(commands) < self._config.max_batch_size:
                try:
                    _, _, cmd = self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                    commands.append(cmd)
                except queue.Empty:
                    break
            changed = bool(commands)
            if commands:
                # 方向3（宽度源统一）：应用命令前刷新 model.width——committed 行
                # （按 model.width wrap）与 live 行（按渲染宽度 wrap）同源，避免
                # resize/首帧批次提交用陈旧宽度（_render_frame 中更新发生在命令
                # 应用之后）。幂等：_render_frame 的 ``model.width = width`` 保持。
                if self._model is not None and hasattr(self._model, "width"):
                    try:
                        self._model.width = self._width_cache.get_width()
                    except Exception:
                        _logger.debug("应用命令前刷新 model.width 异常", exc_info=True)
                self._apply_commands(commands)
        # 渲染与失败处理移出锁块（方向1 步骤4）：渲染失败退避 sleep 不再持有
        # 输出锁（修复前 sleep 在锁块内 → render_lock 阻塞其他写入方输出）。
        if self._should_render(changed):
            try:
                self._render_frame()
            except Exception:
                # P3-16（设计说明）：无 boundary 异常端到端不触发崩溃恢复——
                # reconciler 层对无边界异常 re-raise 保留，但 session 层在此
                # 吞掉仅记 warning：**单帧失败 + 10Hz 重试**（render 线程不
                # 崩溃不重启）。reconciler._handle_render_crash 崩溃恢复仅
                # 服务 render 循环级异常（队列/输入/面板回调等）。
                # P7（方向2）：持久性渲染异常从 10Hz 无限重试降为指数退避
                # （0.1→0.2→0.4→…→1.0 封顶，≤1Hz），日志刷屏缓解；正常
                # 路径无 sleep（渲染帧率 10Hz 不降低）。
                # 方向1（渲染失败帧不重试修复）：_should_render 已清
                # _dirty，失败帧若不补置脏标记下一拍不会重试（仅退避等待）
                # ——补置 _dirty = True，下一 10Hz 拍重试，配合既有指数
                # 退避防刷屏（退避封顶 1s，不会无限重试）。
                self._consecutive_render_failures += 1
                delay = min(0.1 * 2 ** (self._consecutive_render_failures - 1), 1.0)
                time.sleep(delay)
                self._dirty = True
                _logger.warning(
                    "渲染帧失败（连续 %d 次，退避 %.2fs）",
                    self._consecutive_render_failures,
                    delay,
                    exc_info=True,
                )
            else:
                # 成功渲染 → 复位连续失败计数（P7 退避语义）
                self._consecutive_render_failures = 0
        return changed

    def _needs_animation(self) -> bool:
        """是否存在活跃动画状态需要持续 10Hz 渲染（时间基动画推进）。

        工具运行（开放工具卡边框/● 呼吸）、流式生成（status_active → 状态栏
        spinner/模型名呼吸/输入区占位动画）、解析进行中（parse_line spinner）
        任一活跃时，即使无新命令也置脏渲染——修复「工具执行期间 TUI 其他
        部分冻结」：bash 等工具无实时输出时无命令驱动渲染循环，时间基动画
        （工具卡边框呼吸/状态栏呼吸/spinner）停摆。

        与 ``_subagent_panel._needs_animation``（面板控制器经 SUBAGENT_FRAME
        命令自行驱动渲染循环）互补：本方法覆盖主 agent 侧动画状态。
        空闲（全部非活跃）返回 False → 渲染循环跳过（CPU ~0），保持
        ``_should_render`` 空闲短路语义。

        线程安全：仅在 render 线程（``_should_render``）调用；属性读取为 GIL
        原子操作（status/tool_boxes/parse_line 赋值与读取均原子）。
        """
        model = getattr(self, "_model", None)
        if model is None:
            return False
        st = getattr(model, "status", None)
        if st is not None and getattr(st, "status_active", False):
            return True
        if getattr(model, "tool_boxes", None):
            return True
        if getattr(model, "parse_line", None) is not None:
            return True
        # ★ BUG-49（review 方向）：补全弹窗可见时推进呼吸动画——弹窗标题/
        #   选中高亮/提示文本的呼吸色是时间基（time_glow），但渲染循环空闲时
        #   跳过（_should_render 无脏返回 False）→ 弹窗打开且无其他动画状态时
        #   呼吸静止（标题/高亮颜色冻结）。补全弹窗可见时持续 10Hz 渲染推进。
        completion = getattr(model, "completion", None)
        if completion is not None and completion.visible and completion.items:
            return True
        # ★ BUG-49：反向历史搜索激活时推进（query 呼吸色 221↔232，8s 周期）
        search = getattr(model, "history_search", None)
        if search is not None and getattr(search, "active", False):
            return True
        # ★ BEAUTY-18（体验动效）：user_select 弹窗可见时推进呼吸——弹窗标题/
        #   选中高亮背景为时间基呼吸色（time_glow），渲染循环空闲跳过时呼吸
        #   静止。与补全弹窗（BUG-49）同语义：弹窗激活持续 10Hz 渲染推进。
        us = getattr(model, "user_select", None)
        if (
            us is not None
            and getattr(us, "visible", False)
            and not getattr(us, "done", False)
            and getattr(us, "options", None)
        ):
            return True
        return False

    def _should_render(self, changed: bool) -> bool:
        """是否需渲染本帧：脏标记 + 10Hz 拍批处理。

        事件（命令/重绘请求）标记脏并唤醒循环，但不立即渲染——与下一个
        10Hz 拍一起渲染（批处理）。**空闲（无脏）时跳过渲染**，避免固定
        10Hz 全量重建整棵聊天树（大历史下 CPU 100%）。

        ★ 动画保持：活跃动画状态（工具运行/流式/解析，见 ``_needs_animation``）
        时持续置脏——即使无新命令也按 10Hz 拍渲染，时间基动画平滑推进
        （工具执行期间 TUI 其他部分不冻结）。动画结束后回退空闲跳过（CPU ~0）。

        Returns:
            True — 本拍渲染（脏且 render_interval 已到期）。
        """
        now = time.monotonic()
        force = self._bottom_redraw_requested.is_set()
        self._bottom_redraw_requested.clear()
        if changed or force:
            self._dirty = True  # 本批命令已应用 / 底部重绘请求 → 标记脏
        # ★ 活跃动画状态 → 持续置脏（10Hz 拍推进时间基动画；空闲保持跳过）
        if not self._dirty and self._needs_animation():
            self._dirty = True
        if not self._dirty:
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
            # ★ 终端 resize：宽度变化时重排已提交历史（committed_lines 提交时
            #   按旧宽度 wrap，宽度变化后需按新宽度重建——重排产出新列表对象，
            #   前缀缓存自动失效）。幂等（宽度未变直接返回）；桩模型无
            #   reflow_committed 时跳过（兼容）。
            reflow = getattr(self._model, "reflow_committed", None)
            if reflow is not None:
                try:
                    reflow(width)
                except Exception:
                    _logger.debug("reflow_committed 异常", exc_info=True)
            self._model.width = width  # 渲染器 TOC 边框宽度
            # ★ 方向6（resize 后流式渲染宽度陈旧）：宽度变化时向开放通道
            #   renderer（AnsiStreamRenderer.set_width 已实现）传播新宽度——
            #   TOC 边框/表格宽度在 resize 后刷新；已关闭通道 renderer 为
            #   None 跳过。set_width 幂等（重复调用无副作用）。
            # ★ 方向3（resize 全量刷新）：宽度变化置 ``_resize_pending``——
            #   终端尺寸变化后旧帧与物理屏幕内容不对齐，须全量重写而非增量 diff。
            if width != self._last_render_width:
                for renderer in (
                    getattr(self._model, "reasoning_renderer", None),
                    getattr(self._model, "content_renderer", None),
                ):
                    if renderer is not None:
                        try:
                            renderer.set_width(width)
                        except Exception:
                            _logger.debug("set_width 传播异常", exc_info=True)
                self._last_render_width = width
                self._resize_pending = True
                # ★ React Ink useWindowSize（方向 E）：宽度变化通知订阅组件重渲染。
                try:
                    _hooks._notify_window_size()
                except Exception:
                    _logger.debug("notify_window_size 异常", exc_info=True)
            # ★ 增量渲染屏幕高度传播（方向1）：高度变化（resize）时更新
            #   InkRenderer.set_height——渲染器按新屏幕高度钳制光标/跳过不可达行。
            height = self._width_cache.get_height()
            if height != self._last_render_height:
                try:
                    self._ink_renderer.set_height(height)
                except Exception:
                    _logger.debug("set_height 传播异常", exc_info=True)
                self._last_render_height = height
                # 高度变化与宽度变化共用同一次重置（全量刷新标志由宽度/高度
                # 分支任一置位，下方消费）。
                self._resize_pending = True
        # ★ 方向3（resize 全量刷新消费）：尺寸变化后本帧即全量重建（不等待
        #   下一帧 diff）——重置渲染器 prev（full=True），使 render() 走全量
        #   写入路径。仅 resize 使用 full=True；其余路径均走增量 diff。
        if getattr(self, "_resize_pending", False):
            self._resize_pending = False
            self._ink_renderer.reset(full=True)
        element = self._build_tree(self._model, width)
        self._reconciler.render(self._root_fiber, element, width, self._width_cache.get_height())
        frame = _components.render_frame(self._root_fiber, width)
        self._ink_renderer.render(frame)
        # ★ P5：input-area fiber 缓存——仅在失效时重建（避免每帧全树递归查找）。
        #   调和器复用 fiber 时重置 deleted=False；input-area 被删除/替换（旧
        #   fiber 未复用 → deleted 保持 True）时缓存自动失效重建。
        #   ★ 标准 React Ink 组件化：InputArea 函数组件返回 Column（带
        #   dataInputArea 标记 + 透传 props）——查找条件兼容旧 host
        #   "input-area" 与标准组件容器。
        if (
            self._input_fiber is None
            or self._input_fiber.deleted
            or not (
                self._input_fiber.type == "input-area"
                or bool(self._input_fiber.props.get("dataInputArea"))
            )
        ):
            self._input_fiber = self._find_input_fiber(self._root_fiber)
        self._position_cursor()

    # ── 阶段 ─────────────────────────────────────────

    def _update_system_stats(self) -> None:
        """每 2 秒采集 CPU/MEM 写入模型并标记脏（输入区顶部分隔线显示）。

        空闲时也每 2 秒渲染一次（仅更新该值），CPU 开销可忽略。
        """
        now = time.monotonic()
        if now - self._last_sys_stats_time < self._sys_stats_interval:
            return
        self._last_sys_stats_time = now
        if self._model is None:
            return
        if self._system_monitor is None:
            from src.tui._system_monitor import _SystemMonitor
            self._system_monitor = _SystemMonitor()
        status = getattr(self._model, "status", None)
        if status is None:
            return  # 测试桩模型无 status
        try:
            cpu, mem = self._system_monitor.get_cpu_and_mem()
        except Exception:
            _logger.debug("系统监控采集异常", exc_info=True)
            return
        if int(cpu) != status.cpu or int(mem) != status.mem:
            status.cpu = int(cpu)
            status.mem = int(mem)
            self._dirty = True  # 触发渲染显示新值

    def _phase_process_sigwinch(self) -> None:
        """轮询处理 SIGWINCH（BUG-T4：信号处理器只置标志，渲染循环此处消费）。

        process_sigwinch() 返回 True 时，回调已触发 force_refresh / 请求重绘，
        无需额外置脏。
        """
        try:
            process_sigwinch()
        except Exception:
            _logger.debug("_phase_process_sigwinch 异常", exc_info=True)

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
        # ★ P5：优先复用缓存的 input-area fiber（_render_frame 已保证其有效；
        #   None 时回退全树查找——如测试直接构造 root 的场景）
        fiber = self._input_fiber
        if fiber is None:
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
        # 方向1 步骤4（缺失 completion 属性守卫）：popup_height 与 row 计算
        # 纳入 try/except——completion 缺 ``items`` 等属性时抛 AttributeError
        # 中断渲染；修复后缺属性回退 popup_height=0（记 debug），place_cursor
        # 调用保持独立 try。
        try:
            popup_height = _completion_height(completion, box.w)
        except Exception:
            popup_height = 0
            _logger.debug(
                "_position_cursor: completion 属性缺失，回退 popup_height=0",
                exc_info=True,
            )
        max_input = max(1, box.w - len(prompt))
        # ★ PERF-1：优先复用换行布局缓存（每帧至多 1 次换行；缓存写回
        #   dataInputArea 容器 fiber——InputArea 组件内部 _build_lines 写的是
        #   临时 fiber（_input_elements SimpleNamespace），此处是真实 Column
        #   fiber，二者分离；写回后同 text/max_input 帧零重复换行计算）。
        #   未命中时经 _compute_input_layout 计算并写回。
        cached = getattr(fiber, "_input_layout_cache", None)
        if cached is not None and cached[0] == (text, max_input):
            _, wrapped_by_logical = cached[1]
        else:
            rows, wrapped_by_logical = _compute_input_layout(text, max_input)
            fiber._input_layout_cache = ((text, max_input), (rows, wrapped_by_logical))
        vis_row, vis_col = _cursor_visual_from_layout(text, cursor_pos, wrapped_by_logical)
        # 输入文本起始行 = box.y + popup_height + 1（上分隔线之后）
        row = box.y + popup_height + 1 + vis_row + 1
        # ★ P0-1：反向历史搜索激活时 input_area 在输入文本行前追加 1 行
        #   (reverse-i-search) 覆盖行（_build_lines 已正确增行）——光标行偏移须
        #   同步计入（与 input_area._build_lines 共享 _is_search_active
        #   高度辅助，保持一致）。
        if _is_search_active(fiber.props.get("history_search")):
            row += 1
        # ★ 方向6（光标列右边界 clamp）：超宽输入（vis_col 超终端宽度）时
        #   光标列钳制到终端宽度（修复前 col 越界溢出导致光标定位异常）。
        width = self._width_cache.get_width()
        col = min(box.x + len(prompt) + vis_col + 1, width)
        try:
            self._ink_renderer.place_cursor(row, col)
        except Exception:
            _logger.debug("place_cursor 异常", exc_info=True)

    def _find_input_fiber(self, root_fiber):
        """在 host 树中查找输入区 fiber（标准组件 dataInputArea 容器或旧 host）。

        ★ 标准 React Ink 组件化：InputArea 标准组件返回 Column（props 含
        ``dataInputArea=True`` 标记 + 透传输入区状态）——查找条件为
        ``props.dataInputArea`` 或旧 ``type == "input-area"``（兼容）。
        """
        from .fiber import Fiber

        def walk(f: Fiber | None):
            f2 = f
            while f2 is not None:
                if f2.is_host and (
                    f2.type == "input-area"
                    or bool(f2.props.get("dataInputArea"))
                ):
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
            # ★ BUG-39：恢复成功置位稳定计时起点 + 清除崩溃标志——修复前
            #   ``_render_crashed`` Event 恢复后不清除（外部 render_crashed 判断
            #   恒 True）；``_recover_attempts`` 由 _render 循环在稳定运行后复位。
            self._last_recover_time = time.monotonic()
            try:
                self._render_crashed.clear()
            except Exception:
                pass
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
    "render",
    "_get_cmd_priority",
    "_get_cmd_id",
    "_CRITICAL_CMDS",
    "_STREAM_CMDS",
    "_CONTENT_COMMANDS",
]


# ═══════════════════════════════════════════════════════════
# render() — React Ink 轻量入口（方向 F1）
# ═══════════════════════════════════════════════════════════


class _SimpleModel:
    """render() 独立会话的最小模型占位（满足 InkSession 读取的属性）。"""

    width: int = 80
    input_text: str = ""
    input_cursor: int = 0
    status: object = None

    def reset_display(self) -> None:
        pass


def render(
    element: Element,
    stream=None,
    width: int | None = None,
    height: int | None = None,
) -> dict:
    """React Ink ``render()`` 等价物（轻量入口）：渲染组件树到终端。

    创建独立 InkSession 渲染给定元素（不依赖 App 模型/命令管线）——适用于
    组件开发/测试/独立 UI 场景。返回控制对象：
      - ``waitUntilExit()``：awaitable——app 退出（unmount/exit）后 resolve；
      - ``unmount()``：卸载 app（停止渲染线程）；
      - ``cleanup()``：同 unmount（React Ink 内部清理语义别名）；
      - ``rerender(new_element)``：以新元素树重新渲染；
      - ``clear()``：请求全帧清屏重绘。

    Args:
        element: 根元素（函数组件或 Element）。
        stream: 输出流（默认 ``sys.stdout``）。
        width/height: 终端尺寸覆盖（默认读取 width_cache）。

    Returns:
        dict：控制对象（waitUntilExit/unmount/cleanup/rerender/clear）。
    """
    import sys as _sys

    model = _SimpleModel()
    if width is not None:
        model.width = width

    # 组件函数形式的根元素：包装为固定构建函数（每帧返回最新 element）
    _state = {"element": element}

    def _build_tree(m, w):
        return _state["element"]

    session = InkSession(
        model=model,
        build_tree=_build_tree,
        stream=stream if stream is not None else _sys.stdout,
    )
    # 尺寸覆盖（TerminalWidthCache 只读接口——直接写内部缓存字段）
    if width is not None:
        session._width_cache._width = width
    if height is not None:
        session._width_cache._height = height

    session.start()

    def _wait_until_exit():
        async def _waiter():
            import asyncio as _aio
            while session._render_running:
                await _aio.sleep(0.05)
        return _waiter()

    def _unmount():
        try:
            session.request_exit()
        except Exception:
            _logger.debug("render unmount 异常", exc_info=True)

    def _rerender(new_element):
        _state["element"] = new_element
        session._request_render()

    return {
        "waitUntilExit": _wait_until_exit,
        "unmount": _unmount,
        "cleanup": _unmount,
        "rerender": _rerender,
        "clear": session.request_clear,
    }
