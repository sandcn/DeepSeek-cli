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
# ★ Element 已随 render() 拆移至 _render_api.py（session.py 不再直接使用；
#   类型注解在 docstring 中为字符串形式，无需运行时 import）。
# ★ 命令优先级策略（方向B 拆分，2026-08-05）：优先级常量与映射函数自
#   ._cmd_priority 导入（模块级 re-export——旧导入路径
#   ``from src.tui.ink.session import _get_cmd_priority`` 兼容）。
from ._cmd_priority import (
    _CMD_PRIORITY_CRITICAL,
    _CMD_PRIORITY_LOW,
    _CRITICAL_CMDS,
    _STREAM_CMDS,
    _get_cmd_id,
    _get_cmd_priority,
    _cmd_name,
)
# ★ 输入光标定位（方向B 拆分，2026-08-05）：_position_cursor/_find_input_fiber
#   的布局计算自 ._cursor 导入（纯函数模块，独立可测）。
from . import _cursor

_logger = logging.getLogger(__name__)
# ── 内容命令集合（真源在 _const.CONTENT_COMMANDS） ──────────
_CONTENT_COMMANDS = CONTENT_COMMANDS

# ── 暂停/恢复保留命令集合（2026-08-15 短内容丢失修复） ──────────
# suspend（交互工具独占终端）/ 崩溃恢复 / flush 超时兜底经 ``_drain_queue_safe``
# 清空队列时，**用户可见核心内容命令**（思考/回答/工具卡/阶段/错误等）不丢弃、
# 保留在队列中，resume 后渲染线程处理——修复前这些命令被无条件丢弃，模型
# 在交互工具挂起 / 渲染暂停期间输出的短思考/短回答**永久丢失**（模型状态也
# 未应用），视觉上「很短的回答跟思考没显示」（偶发，取决于命令入队与
# suspend 清理的时序）。
# 可丢弃命令（WRITE_LINE/DISPLAY_MSGS/SUBAGENT_FRAME/CLEAR_MSGS/SPLASH/
# BG_BASH_COUNT）为外部输出/历史回放/面板刷新/清屏等——临时挂起后重放或
# 由调用方重新触发，丢弃可接受（避免暂停期间积压陈旧命令污染恢复帧）。
_KEEP_CONTENT_CMDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_OUTPUT,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.TOOL_OPEN,
    RenderCommand.TOOL_CLOSE,
    RenderCommand.TOOL_COUNT_INC,
    RenderCommand.TOOL_COUNT_DEC,
    RenderCommand.TOOL_FAIL_INC,
    RenderCommand.USER_MSG,
    RenderCommand.ERROR,
    RenderCommand.NOTIFICATION,
    RenderCommand.MAIN_PHASE,
    RenderCommand.PARSE_INFO,
    RenderCommand.SUBAGENT_MARKDOWN,
})

#: BUG-39：崩溃恢复后视为「稳定」的最小运行时长（秒）——恢复成功且持续运行
#: 超过该阈值后复位 ``_recover_attempts``（防长时间运行后恢复预算耗尽）。
_RECOVER_STABLE_SECS = 60.0

#: P2-2（review 方向）：``_put_no_drop`` 内容命令背压最大等待时长（秒）。
#: 渲染线程存活时队列满 → 背压等待（不静默丢弃内容）；超过本阈值（渲染线程
#: 卡死/消费停滞）回退为丢弃并记 warning——防调用方（流式/事件循环线程）
#: 永久阻塞。
_PUT_NO_DROP_TIMEOUT = 30.0


def _safe_int(value, default: int = 0) -> int:
    """安全整数转换（系统监控值防御）。

    P2-5（review 方向）：``_SystemMonitor.get_cpu_and_mem`` 平台采集在异常时
    返回 0.0，但某些路径（子进程输出解析/平台差异）可能返回非数字（如
    "N/A"）——``int(value)`` 在渲染线程内抛 ``ValueError`` 使渲染线程崩溃。
    转换失败回退默认值（0），不中断渲染循环。

    Args:
        value: 待转换值（数字/数字字符串/其他）。
        default: 转换失败回退值。

    Returns:
        转换后的整数；失败返回 ``default``。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

    def set_line_tracker(self, tracker) -> None:
        """绑定输出行追踪器（输出历史落盘 + line callback 接线）。

        2026-08-05 装配层重构：取代装配对 ``session._line_tracker`` /
        ``session._ink_renderer.set_line_callback`` 的**私有字段直写**——
        装配经本公开方法注入：
          - 新增内容行回调 ``tracker.track``（输出历史跟踪）；
          - ``self._line_tracker`` 供 ``TuiLifecycle.stop`` 流程调用
            ``close()``（flush 剩余行 + 停止 daemon 刷盘定时器）。
        None 可清除绑定（line callback 置空）。
        """
        self._line_tracker = tracker
        try:
            self._ink_renderer.set_line_callback(
                tracker.track if tracker is not None else None,
            )
        except Exception:
            _logger.debug("set_line_tracker 接线 line callback 异常", exc_info=True)

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
                # ★ 修复（长任务思考/回答丢失）：pop 绕过 get() 直接移除 heapq
                #   元素，须补 task_done() 减 unfinished_tasks——否则
                #   queue.join()（flush 等待排空）因 unfinished_tasks 虚高而
                #   永远等待 → flush 恒超时 → _drain_queue_safe 丢弃未消费的
                #   reasoning/content 命令（视觉「只显示工具调用」）。
                #   task_done() 须在 mutex 外调用：其内部经 all_tasks_done
                #   （Condition，与 mutex 同源普通 Lock 不可重入）再次获取
                #   mutex，持 mutex 调用会自死锁。
                try:
                    self._cmd_queue.task_done()
                except ValueError:
                    pass
                try:
                    self._cmd_queue.put(
                        (priority, next(self._cmd_seq), cmd), block=False,
                    )
                    self._consecutive_full = 0
                    self._cmd_event.set()
                    return
                except queue.Full:
                    pass  # 并发竞争仍满 → 保持丢弃（不无限循环）
            # ★ 内容命令不静默丢弃（长任务思考/回答丢失修复，2026-08-13）：
            #   REASONING/CONTENT/TOOL_OUTPUT 是用户可见核心内容——队列满且无
            #   LOW 可腾位时，修复前非 blocking 内容命令被**立即静默丢弃** →
            #   长任务（大量工具输出/流式文本积压致队列满）中思考/回答偶发不
            #   显示，而 TOOL_OPEN/TOOL_CLOSE/PHASE_DONE 等 CRITICAL 命令走
            #   阻塞路径不丢，视觉上「只显示工具调用卡片」。渲染线程存活时
            #   背压等待（模型流式让渲染消费跟上，内容不丢）；渲染线程已终止
            #   （UI 不可用）回退原丢弃语义（不无限卡死调用方/事件循环）。
            if _get_cmd_id(cmd) in _STREAM_CMDS and self._render_running:
                if self._put_no_drop(priority, cmd):
                    return
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

    def _put_no_drop(self, priority: int, cmd: RenderCmd) -> bool:
        """内容命令背压等待：渲染线程存活时持续等待入队（不静默丢弃内容）。

        长任务渲染拥塞修复（2026-08-13）：REASONING/CONTENT/TOOL_OUTPUT 是
        用户可见核心内容——队列满时若静默丢弃，长任务中思考/回答偶发不显示
        （工具调用卡因 CRITICAL 阻塞语义不丢，视觉上「只显示工具调用」）。
        渲染线程存活时阻塞等待（背压：模型流式等待渲染消费跟上，内容不丢）；
        渲染线程已终止（UI 不可用）返回 False → 调用方回退丢弃告警路径，
        避免无限卡死调用方（流式/事件循环线程）。

        ★ P2-2（review 方向）：背压**有上限**——``while`` 循环内
        ``put(timeout=0.5)`` 无限重试，渲染线程卡死（消费停滞但线程仍存活）
        时调用方永久阻塞。修复：增加最大等待时长（``_PUT_NO_DROP_TIMEOUT``，
        30s），超时后回退为丢弃并记 warning（返回 False，调用方回退既有
        丢弃告警路径，语义兼容）。

        Returns:
            True — 已入队成功；False — 渲染线程已终止或背压超时，未入队。
        """
        deadline = time.monotonic() + _PUT_NO_DROP_TIMEOUT
        while self._render_running:
            try:
                self._cmd_queue.put(
                    (priority, next(self._cmd_seq), cmd),
                    block=True, timeout=0.5,
                )
                self._consecutive_full = 0
                self._cmd_event.set()
                return True
            except queue.Full:
                # 渲染线程存活但队列仍满（消费中）→ 继续等待（背压，不丢内容）
                if time.monotonic() >= deadline:
                    _logger.warning(
                        "内容命令背压等待超时（>%.0fs），回退丢弃: %s (优先级=%d)",
                        _PUT_NO_DROP_TIMEOUT, _cmd_name(_get_cmd_id(cmd)), priority,
                    )
                    return False
                continue
        return False

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
            # ★ P2-1（review 方向）：渲染失败后补置 ``_dirty``——修复前失败被
            #   吞且不补脏标记：Ctrl+L 清屏后 ``full_clear()`` 已清空屏幕（渲染
            #   器 prev 软重置为空帧），而重建空文档失败时屏幕保持空白；渲染
            #   线程下一拍 ``_should_render`` 因无脏标记（无命令/无 force/无
            #   动画）跳过渲染 → 屏幕空白且空闲时永不重绘。补置 ``_dirty`` 后
            #   下一 10Hz 拍重试重建（配合 _drain_queue 既有指数退避防刷屏）。
            self._dirty = True
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
        #   ★ P3-20 竞态窗口说明（review 方向）：``not self._render_running``
        #   检查与同步渲染之间存在竞态窗口——render 线程可能恰在检查后启动
        #   （start()/resume() 并发调用）→ 本帧同步渲染与线程渲染并行（双写
        #   终端）。output lock 串行化实际写入（同一锁互斥），但存在双帧渲染
        #   （同步帧 + 线程帧）顺序不确定性：同步帧内容与线程下一帧内容一致
        #   （同一模型状态），后写帧覆盖先写帧，最终显示一致——可接受（已知
        #   权衡，输出锁已消除撕裂主风险）。
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
                    # ★ BUG（review 方向）：``asyncio.get_event_loop().run_until_complete()``
                    #   在 Python 3.10+ 非主线程调用抛 RuntimeError（无当前事件循环）、
                    #   3.12+ 产生 DeprecationWarning——改用独立事件循环执行协程
                    #   （``new_event_loop`` 线程安全，无 get_event_loop 的线程绑定）。
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(result)
                    finally:
                        loop.close()
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
                # ★ P2 修复（review 方向）：join 超时但版本未变——不提前 break，
                #   继续下一轮二次等待确认线程退出（渲染线程卡住时仍在写
                #   stream，未确认退出即 reset/suspend 会造成输出撕裂竞态）。
                continue
            # 兜底：循环结束后线程仍存活 → 记 warning 并排空队列（不无限等待）
            if self._render_thread is not None and self._render_thread.is_alive():
                _logger.warning(
                    "stop() 等待 render 线程超时（版本=%d），排空队列后退出",
                    self._render_version,
                )
        # 重置渲染器状态（suspend/resume 路径：下次 start 全量重绘）。
        # ★ P2-4（review 方向）：join 超时（渲染线程仍存活）时**跳过渲染器
        #   suspend**——修复前超时后仍无条件执行 ``_ink_renderer.suspend()``
        #   + ``_drain_queue_safe()``：渲染线程若仍在写 stream 会与清理并发
        #   （输出撕裂/渲染状态错乱）。渲染器状态保留至线程真正退出（stop 后
        #   该线程/渲染器不再复用；确需重启走 start/resume 的 reset/full
        #   路径）。``_drain_queue_safe()`` 保留——排空队列为 stop 必要语义，
        #   队列操作线程安全（mutex 保护），不写 stream 无撕裂风险。
        if self._render_thread is None or not self._render_thread.is_alive():
            self._ink_renderer.suspend()
        self._drain_queue_safe()

    def flush(self, timeout: float | None = 5.0) -> None:
        """等待队列处理完成。

        超时后若渲染线程仍存活（仅消费慢，非崩溃/停止），继续等待队列排空
        而非丢弃——修复前超时即 ``_drain_queue_safe()`` 丢弃队列中未消费命令
        （含 reasoning/content），长任务大量工具输出积压时 flush 超时 → 后几轮
        思考/回答命令被丢弃（工具卡因 CRITICAL 阻塞语义不丢），视觉上「只显示
        工具调用」。渲染线程存活则持续消费，等待直至排空；仅当渲染线程停止
        （``_render_running=False`` / 线程退出）才丢弃兜底，避免无限等待。
        """
        if self._render_thread is None or not self._render_thread.is_alive():
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        # ★ P1-1（review 方向）：daemon=True——修复前 ``daemon=False`` 泄漏
        #   非 daemon 线程：flush 超时后 ``_drain_queue_safe(keep_content=True)``
        #   保留内容命令不消费、不 task_done → ``unfinished_tasks`` 永不归零 →
        #   本线程永远阻塞在 ``queue.join()``，进程退出时挂起。daemon=True 后
        #   进程退出不等待本线程（渲染线程停止时 ``_drain_queue_safe`` 兜底
        #   已清理队列；线程随进程退出自然终止，无资源泄漏）。flush() 其余
        #   创建线程处（start/resume/崩溃恢复）均已 daemon=True，仅此一处遗漏。
        task_done = threading.Thread(target=self._cmd_queue.join, daemon=True)
        task_done.start()
        task_done.join(timeout=timeout)
        if task_done.is_alive():
            # ★ 修复（长任务思考/回答丢失）：超时后渲染线程存活则继续等待排空
            #   （每次 1s 轮询），仅当渲染线程停止（_render_running=False 或
            #   线程退出）才丢弃剩余命令兜底——避免超时即丢内容命令。
            while (
                task_done.is_alive()
                and self._render_running
                and self._render_thread is not None
                and self._render_thread.is_alive()
            ):
                task_done.join(timeout=1.0)
            if task_done.is_alive():
                # ★ 2026-08-15（短内容丢失修复）：flush 超时兜底丢弃时保留
                #   内容命令（思考/回答/工具卡）——修复前超时即丢弃队列中
                #   未消费的 reasoning/content，长任务/渲染暂停后短内容丢失。
                self._drain_queue_safe(keep_content=True)
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
            if self._render_thread.is_alive():
                # ★ P2 修复（review 方向）：join 超时后检查 is_alive()——线程
                #   仍存活时记 warning 并二次等待确认退出后再继续清理（防渲染
                #   线程卡住仍在写 stream → 让出终端后输出撕裂竞态）。
                _logger.warning(
                    "suspend() 等待 render 线程超时（版本=%d），二次等待确认退出",
                    self._render_version,
                )
                self._render_thread.join(timeout=2.0)
                if self._render_thread.is_alive():
                    _logger.warning(
                        "suspend() render 线程二次等待仍存活（版本=%d），强制继续清理",
                        self._render_version,
                    )
        self._ink_renderer.suspend()
        # ★ 2026-08-15（短内容丢失修复）：suspend 清空队列时**保留内容命令**
        #   （思考/回答/工具卡等）——模型在交互工具挂起期间输出的短内容命令
        #   不丢弃，resume 后渲染线程处理显示（修复前无条件丢弃 → 偶发丢失）。
        self._drain_queue_safe(keep_content=True)
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
        # ★ P2-3（review 方向）：resume 前检查旧线程是否已退出——修复前
        #   无条件覆盖 ``_render_thread``：suspend() join 超时旧线程仍存活时
        #   直接启动新线程 → 两个渲染线程并发（双写终端）。仍存活则先
        #   join(timeout=...) 等待确认退出（suspend 已置 ``_render_running=
        #   False``，旧线程解除阻塞后自然退出）；二次等待仍存活记 warning 并
        #   强制启动新线程（resume 必须恢复渲染，输出锁互斥防撕裂，旧线程
        #   终将退出）。
        if self._render_thread is not None and self._render_thread.is_alive():
            _logger.warning(
                "resume() 旧 render 线程仍存活（版本=%d），先等待其退出",
                self._render_version,
            )
            self._render_thread.join(timeout=2.0)
            if self._render_thread.is_alive():
                _logger.warning(
                    "resume() 旧 render 线程等待超时仍存活（版本=%d），强制启动新线程",
                    self._render_version,
                )
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
                        # ★ prefill/交互立即渲染修复（2026-08-15）：底部重绘请求
                        #   （force，如 /editmsg /deitmsg /retry 的 prefill 注入经
                        #   update_input → _request_render 置位）**提前退出**节流
                        #   等待，本拍立即处理渲染——修复前 prefill 注入仅置位
                        #   标志，渲染线程被节流（next_loop）拦截且 _should_render
                        #   的 interval 检查可能因 EditmsgPlugin flush 刚渲染过而
                        #   不满足 → 输入区延迟 0.1~0.5s 才显示 prefill（用户感知
                        #   「编辑后没立即显示，要再按一次回车才刷新」）。
                        #   高频命令（ToolCountInc/DecCmd、SUBAGENT_FRAME、
                        #   ParseInfoCmd 等）不置位 _bottom_redraw_requested，仍走
                        #   既有 10Hz 批处理（PERF-8 忙循环防护不回归）。
                        if self._bottom_redraw_requested.is_set():
                            break
                next_loop = max(next_loop, time.monotonic()) + self._config.render_interval
                try:
                    # 方向5（死代码清理）：返回值未使用——has_content 删除。
                    self._drain_queue()
                    self._cmd_event.clear()
                    # ★ prefill/交互立即渲染修复（2026-08-15）：force
                    #   （_bottom_redraw_requested，如 /editmsg /deitmsg /retry
                    #   prefill 注入经 update_input → _request_render 置位）未
                    #   消费时**保持 _cmd_event 唤醒**——渲染线程 busy（处理
                    #   EditmsgPlugin 的 clear/display/write 命令）期间注入的
                    #   force 请求若在本行被无条件 clear，渲染线程下一轮循环进入
                    #   节流等待时 ``_cmd_event.wait`` 不会立即返回（事件已 clear），
                    #   prefill 延迟到下一 10Hz 拍才渲染（输入区 0.1~0.5s 空白）。
                    #   保持唤醒 → 下一轮循环 force 提前退出节流等待（上方）→
                    #   _drain_queue → _should_render（force 跳过 interval）→
                    #   立即渲染 prefill。
                    if self._bottom_redraw_requested.is_set():
                        self._cmd_event.set()
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
            # ★ 2026-08-15（短内容丢失修复）：渲染线程退出时保留内容命令
            #   （思考/回答/工具卡等）——suspend 流程中 ``_render_thread.join()``
            #   等待本线程退出，若 finally 以 keep_content=False 排空，会丢弃
            #   suspend 清理刚保留的内容命令（模型输出短内容永久丢失）；统一
            #   保留，resume 后新线程处理显示。stop() 的排空仍会清空全部。
            dropped = self._drain_queue_safe(keep_content=True)
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
            # 方向1 步骤4 + review 方向（应用移出锁块）：锁块内**仅排空命令**
            # ——``_apply_commands`` 含 markdown 全量渲染（_do_subagent_markdown
            # → AnsiStreamRenderer）等耗时操作，持锁会阻塞其他写入方（diff
            # 渲染/紧急输出）。命令应用在锁外执行（见下）；渲染与失败处理本
            # 就在锁外（块内置 render_failed 标记？否——渲染在锁外直接执行，
            # sleep 退避期间输出锁可被其他写入方获取）。
            while len(commands) < self._config.max_batch_size:
                try:
                    _, _, cmd = self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                    commands.append(cmd)
                except queue.Empty:
                    break
            changed = bool(commands)
        # 锁外应用命令（review 方向）：apply_cmd 是纯模型状态变更（AppModel），
        # 不直接写终端——移出输出锁避免 markdown 渲染等耗时操作持锁阻塞其他
        # 写入方。模型仅 render 线程修改（push_cmd 只入队不应用），线程安全。
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
        # ★ P3-20 锁外渲染设计说明（review 方向）：输出锁**仅保护队列排空**
        #   （锁块内只做 ``_cmd_queue.get_nowait`` 收集）——命令应用与渲染在
        #   锁外执行。已知权衡：渲染本身不受锁互斥（request_bottom_redraw
        #   同步渲染可能与本线程渲染并行，双写终端——output lock 串行化写入
        #   防撕裂，双帧顺序不确定性可接受，见 request_bottom_redraw 竞态窗口
        #   注释）；收益：应用/渲染耗时（markdown 渲染、失败退避 sleep）不
        #   阻塞其他写入方（diff 渲染/紧急输出），命令吞吐不因单帧慢渲染
        #   阻塞。若未来需要渲染互斥，应引入独立渲染锁（非输出锁）。
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

        ★ 2026-08-16 需求变更：渲染循环已改为**全程 10Hz**（空闲也持续渲染，
        见 ``_should_render``），本方法不再决定是否跳过渲染——保留用于标记
        活跃动画状态（置脏），以及对时间基动画语义的探测（其他模块注释引用）。

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
        # ★ 补全弹窗（2026-08-05 修复，与 user_select 弹窗同）：不驱动动画
        #   循环——弹窗已静态化（标题/高亮/说明/提示均为静态色，见
        #   input_area.py _build_popup_lines），无需 10Hz 渲染推进呼吸。修复前
        #   补全弹窗激活持续 10Hz 渲染，每帧重写弹窗行（Termux 等终端闪烁）。
        #   弹窗内容仅随打字（items 变化）/导航（selected 变化）更新，经
        #   事件驱动渲染。
        # ★ 反向历史搜索（2026-08-05 修复，与弹窗同）：不驱动动画循环——
        #   搜索行 query 已静态化（见 input_area.py _build_lines），无需 10Hz
        #   渲染推进呼吸。修复前搜索激活持续 10Hz 渲染（Termux 等终端闪烁）；
        #   搜索行内容仅随按键（query/matches 变化）更新，经事件驱动渲染。
        # ★ user_select 弹窗（2026-08-05 修复）：不驱动动画循环——弹窗已
        #   静态化（标题/高亮/说明/提示均为静态色，见 user_select.py），
        #   无需 10Hz 渲染推进呼吸。修复前弹窗激活持续 10Hz 渲染，每帧
        #   重写弹窗行（呼吸色 time_glow 变化），Termux 等终端每帧刷新/
        #   闪烁（「每 fps 刷出错乱显示」）。弹窗内容仅随交互（按键导航/
        #   确认/取消）变化，经 use_state setter → _request_render 重绘。
        return False

    def _should_render(self, changed: bool) -> bool:
        """是否需渲染本帧：全程 10Hz 拍渲染（空闲也持续刷新）。

        事件（命令/重绘请求）标记脏并唤醒循环，但不立即渲染——与下一个
        10Hz 拍一起渲染（批处理）。

        ★ 全程 10Hz（2026-08-16 需求变更）：移除原先「空闲（无脏）跳过渲染
        （CPU ~0）」短路——**没有流式输出（等待用户输入/空闲）期间 TUI 也
        以每秒 10Hz 刷新**。组件树大量缓存下无变化帧 diff 零输出（仅光标
        定位），CPU 开销可控；空闲时状态栏/输入区等时间基元素同样平滑推进。

        ★ 立即渲染：force（_bottom_redraw_requested，如 /editmsg prefill 注入）
        时跳过 interval 节流——用户可感知的 UI 更新即时渲染；高频命令
        （工具状态）不走 force，10Hz 批处理语义不变。

        Returns:
            True — 本拍渲染（render_interval 已到期或 force）。
        """
        now = time.monotonic()
        force = self._bottom_redraw_requested.is_set()
        self._bottom_redraw_requested.clear()
        if changed or force:
            self._dirty = True  # 本批命令已应用 / 底部重绘请求 → 标记脏
        # ★ 活跃动画状态 → 置脏（语义保留：有动画需求时标记 dirty；渲染决定
        #   已不依赖 dirty——全程 10Hz 渲染，见上）。
        if not self._dirty and self._needs_animation():
            self._dirty = True
        # ★ 全程 10Hz：空闲也按 render_interval 渲染（移除空闲跳过短路）。
        if now - self._last_bottom_redraw >= self._config.render_interval or force:
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
                # ★ 2026-08-15（/editmsg 后渲染错乱修复）：CLEAR_MSGS
                #   （reset_display 清空聊天块）后置 ``_resize_pending`` ——
                #   下一帧 ``_render_frame`` 经 reset(full=True) 全量重写。
                #   修复前 clear+display 整篇重建（文档高度大减 + 内容全变）
                #   走 ``_rewrite_drifted`` 漂移路径：首差异行 0 触发底部对齐
                #   切换，物理缓冲（buf_h）与文档高度严重不匹配（漂移），
                #   后续增量增长（_grow_drifted）只重写变化行，状态栏/输入区
                #   等「新旧内容相同」的行不重写 → 屏幕布局错乱（状态栏
                #   丢失、内容错位）。
                if _get_cmd_id(cmd) == RenderCommand.CLEAR_MSGS:
                    self._resize_pending = True
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
            width_changed = False
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
                width_changed = True
            # ★ 增量渲染屏幕高度传播（方向1）：高度变化（resize）时更新
            #   InkRenderer.set_height——渲染器按新屏幕高度钳制光标/跳过不可达行。
            height = self._width_cache.get_height()
            height_changed = False
            if height != self._last_render_height:
                try:
                    self._ink_renderer.set_height(height)
                except Exception:
                    _logger.debug("set_height 传播异常", exc_info=True)
                self._last_render_height = height
                # 高度变化与宽度变化共用同一次重置（全量刷新标志由宽度/高度
                # 分支任一置位，下方消费）。
                self._resize_pending = True
                height_changed = True
            if width_changed or height_changed:
                # ★ React Ink useWindowSize（方向 E）+ P3-19（review 方向）：
                #   宽度/高度任一变化都通知订阅组件重渲染——修复前仅在宽度
                #   分支调用 ``_notify_window_size()``：高度单独变化（resize
                #   只改高度）时 useWindowSize 订阅者不重渲染，窗口尺寸状态
                #   陈旧（useWindowSize 返回 columns/rows 双值，rows 变化须
                #   通知）。宽高同时变化时合并为一次通知（版本只递增一次，
                #   订阅者单次重渲染，避免双帧重绘）。
                try:
                    _hooks._notify_window_size()
                except Exception:
                    _logger.debug("notify_window_size 异常", exc_info=True)
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
        # ★ P2-5（review 方向）：int() 转换纳入防御——``get_cpu_and_mem()``
        #   异常时返回 0.0，但平台差异/子进程解析可能返回非数字（如 "N/A"），
        #   直接 ``int()`` 抛 ValueError 使渲染线程崩溃（_drain_queue 每帧
        #   调用本方法）。``_safe_int`` 转换失败回退 0，不中断渲染循环。
        cpu_i = _safe_int(cpu)
        mem_i = _safe_int(mem)
        if cpu_i != status.cpu or mem_i != status.mem:
            status.cpu = cpu_i
            status.mem = mem_i
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
        """渲染后定位输入光标（从文档底部相对移动）。

        方向B（2026-08-05）：布局/坐标计算委托 ``_cursor.position_cursor``
        （纯函数模块）；本方法只负责 fiber 获取与异常兜底。
        """
        if self._model is None:
            return
        # ★ P5：优先复用缓存的 input-area fiber（_render_frame 已保证其有效；
        #   None 时回退全树查找——如测试直接构造 root 的场景）
        fiber = self._input_fiber
        if fiber is None:
            fiber = _cursor.find_input_fiber(self._root_fiber)
        if fiber is None:
            return
        try:
            _cursor.position_cursor(
                self._ink_renderer, self._width_cache.get_width(), fiber,
            )
        except Exception:
            _logger.debug("place_cursor 异常", exc_info=True)

    def _find_input_fiber(self, root_fiber):
        """在 host 树中查找输入区 fiber（委托 ``_cursor.find_input_fiber``）。

        ★ 标准 React Ink 组件化：InputArea 标准组件返回 Column（props 含
        ``dataInputArea=True`` 标记 + 透传输入区状态）——查找条件为
        ``props.dataInputArea`` 或旧 ``type == "input-area"``（兼容）。
        """
        return _cursor.find_input_fiber(root_fiber)

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
            # ★ P3-20 sleep 行为说明（review 方向）：崩溃恢复前**阻塞 sleep**
            #   （recover_delay）——期间渲染线程挂起不处理命令（队列堆积，
            #   命令经 PriorityQueue 保序不丢失，恢复后批量处理）。已知权衡：
            #   阻塞 sleep 避免崩溃恢复风暴（持续性异常下立即重启会反复崩溃
            #   刷屏/忙循环），代价是恢复期间 UI 无渲染（延迟 recover_delay
            #   可见）；恢复前置 ``_drain_queue_safe()`` 丢弃待处理命令（防
            #   陈旧命令在恢复帧被应用），队列清空语义由 finally 排空兜底。
            time.sleep(self._config.recover_delay)
            # ★ 2026-08-15（短内容丢失修复）：崩溃恢复清空队列时**保留内容
            #   命令**（思考/回答/工具卡）——修复前恢复前置 ``_drain_queue_safe()``
            #   丢弃待处理命令，崩溃瞬间模型输出的短内容永久丢失；保留后恢复
            #   帧批量处理（队列经 PriorityQueue 保序不丢失）。
            self._drain_queue_safe(keep_content=True)
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

    def _drain_queue_safe(self, keep_content: bool = False) -> int:
        """清空渲染命令队列（丢弃计数）。

        Args:
            keep_content: True 时**保留用户可见核心内容命令**
                （``_KEEP_CONTENT_CMDS``：思考/回答/工具卡/阶段/错误等）——
                仅丢弃非内容命令（WRITE_LINE/DISPLAY_MSGS/SUBAGENT_FRAME 等）。
                供 suspend（交互工具独占终端）/ 崩溃恢复 / flush 超时兜底使用：
                模型在渲染暂停期间输出的短思考/短回答命令**不丢失**，resume 后
                渲染线程处理并显示（修复前无条件丢弃 → 短内容永久丢失）。

        Returns:
            丢弃的命令条数。
        """
        dropped = 0
        if keep_content:
            # ★ 保留内容命令：**不 get/put 重放**（会破坏 unfinished_tasks——
            # get_nowait+task_done 后 put 回去，resume 后 _drain_queue 的
            # task_done 抛 ValueError）——直接在 mutex 保护下遍历队列，仅
            # 移除要丢弃的命令（保留命令原地不动，seq/堆序不变）。
            with self._cmd_queue.mutex:
                keep_items: list = []
                drop_items: list = []
                for item in self._cmd_queue.queue:
                    if _get_cmd_id(item[2]) in _KEEP_CONTENT_CMDS:
                        keep_items.append(item)
                    else:
                        drop_items.append(item)
                self._cmd_queue.queue[:] = keep_items
                # ★ BUG-31 同族：heapq 数组任意修改后须 heapify 恢复堆序
                #   （否则后续 heappush/heappop 在损坏堆上操作可能返回非最小项）。
                heapq.heapify(self._cmd_queue.queue)
                dropped = len(drop_items)
            # 丢弃的命令补 task_done（unfinished_tasks 一致性：put 增加、task_done
            # 减少；丢弃的命令不再被消费 → 补一次 task_done）。与 push_cmd 腾位
            # 语义一致：task_done 在 mutex 外调用（all_tasks_done Condition 与
            # mutex 同源普通 Lock 不可重入，持 mutex 调用会自死锁）。
            for _ in drop_items:
                try:
                    self._cmd_queue.task_done()
                except ValueError:
                    pass
            if dropped > 0:
                _logger.info(
                    "渲染队列清理：丢弃 %d 条非内容命令，保留 %d 条内容命令",
                    dropped, len(keep_items),
                )
            return dropped
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


# ★ render() 轻量入口（方向 F1）已拆分至独立模块 _render_api.py
#   （2026-08-05 架构优化）——本模块 re-export 保持旧导入路径兼容
#   （``from src.tui.ink.session import render`` 仍可用，测试锁定）。
from ._render_api import render, _SimpleModel  # noqa: F401  re-export 兼容

__all__ = [
    "InkSession",
    "render",
    "_get_cmd_priority",
    "_get_cmd_id",
    "_CRITICAL_CMDS",
    "_STREAM_CMDS",
    "_CONTENT_COMMANDS",
]
