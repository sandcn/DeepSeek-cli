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
from enum import Enum
from typing import Callable

from src.tui._const import (
    CONTENT_COMMANDS,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
from src.tui._config import TuiConfig
from src.renderer._locks import _try_acquire_output_lock
from src.tui._screen import (
    TerminalWidthCache,
    _get_terminal_size,
    process_sigwinch,
)
from .reconciler import Reconciler
from .renderer import InkRenderer
from . import hooks as _hooks
# ★ 命令优先级策略（方向B 拆分，2026-08-05）：优先级常量与映射函数自
#   ._cmd_priority 导入（模块级 re-export——旧导入路径
#   ``from src.tui.ink.session import _get_cmd_priority`` 兼容）。
from ._cmd_priority import (
    _CRITICAL_CMDS,
    _STREAM_CMDS,
    _get_cmd_id,
    _get_cmd_priority,
)
# ★ 架构改进方向 A（2026-08-16，InkSession 上帝类拆分）：
#   - 命令队列管理（入队/背压/排空安全）→ _session_queue_mixin
#   - 渲染帧执行（组件树/调和/渲染/光标/系统监控）→ _session_frame_mixin
#   - InkSession 组合两 mixin，本模块保留渲染循环调度/生命周期/崩溃恢复/
#     注入/访问器（facade 职责）；下述模块级常量随方法迁移后 re-export
#     保持旧导入路径兼容（``src.tui.ink.session._KEEP_CONTENT_CMDS`` 等）。
from ._session_queue_mixin import (
    _SessionQueueMixin,
    _KEEP_CONTENT_CMDS,
    _PUT_NO_DROP_TIMEOUT,
)
from ._session_frame_mixin import (
    _SessionFrameMixin,
    _safe_int,
)

_logger = logging.getLogger(__name__)
# ── 内容命令集合（真源在 _const.CONTENT_COMMANDS） ──────────
_CONTENT_COMMANDS = CONTENT_COMMANDS

#: BUG-39：崩溃恢复后视为「稳定」的最小运行时长（秒）——恢复成功且持续运行
#: 超过该阈值后复位 ``_recover_attempts``（防长时间运行后恢复预算耗尽）。
_RECOVER_STABLE_SECS = 60.0

# ── 渲染线程自适应等待参数（★ 单帧耗时无上界约束） ──────────────
# 约束（2026-08-19）：渲染线程为 10Hz 循环，**单帧执行没有耗时上限**——
# 大量上文重放/超长 markdown 渲染一帧可达数秒以上。所有「等待渲染线程」
# 的逻辑不得把慢帧误判为超时/挂起：
#   - 帧号推进（``_frame_seq``）或帧执行中（``_frame_active``）= 有进展，
#     续期软超时继续等（每帧执行多久都可以）；
#   - 硬上限仅防**真挂起**（如 PTY 缓冲区满 write 永久阻塞）——远大于正常
#     帧耗时的兜底值，触达时降级并记 warning。
#: flush_input_router 总硬上限（秒）——真挂起防护（软超时仍由 timeout 参数决定）
_ROUTER_FLUSH_HARD_CEILING = 60.0
#: _wait_render_flush 无进展软超时（秒）——帧执行中/帧号推进时续期
_RENDER_FLUSH_SOFT_TIMEOUT = 5.0
#: _wait_render_flush 总硬上限（秒）——真挂起防护
_RENDER_FLUSH_HARD_TIMEOUT = 60.0
#: _join_render_thread 硬上限（秒）——真挂起防护（线程活着且未达上限就一直等）
_JOIN_RENDER_HARD_TIMEOUT = 30.0

# ★ 架构改进方向 A：``_KEEP_CONTENT_CMDS`` / ``_PUT_NO_DROP_TIMEOUT`` 定义已
#   迁至 ``_session_queue_mixin``（唯一使用方），本模块 re-export 保持旧导入
#   路径兼容；``_safe_int`` 定义已迁至 ``_session_frame_mixin``（系统监控防御
#   工具），同上 re-export。


class RenderLoopPhase(Enum):
    """渲染循环单帧阶段（架构改进方向 E，2026-08-16：显式状态迁移）。

    ``_drain_queue`` 按本枚举驱动单帧六阶段处理，阶段迁移显式化：

      SIGWINCH → INPUT → PANELS → SYSTEM_STATS → DRAIN_COMMANDS
      → APPLY → RENDER（终态，返回本帧是否有命令变更）

    显式迁移收益：
      - 阶段顺序可读/可测（原隐式顺序仅靠注释维系）；
      - 未来插入新阶段（如崩溃恢复检查/预渲染挂钩）不破坏既有流程；
      - 每阶段失败可独立定位（异常栈带阶段上下文）。
    """
    #: SIGWINCH 轮询消费（信号处理器只置标志，此处正常线程上下文处理）
    SIGWINCH = "sigwinch"
    #: 输入事件分发（stdin 读取/解析/缓冲）
    INPUT = "input"
    #: 面板刷新回调（subagent 面板等）
    PANELS = "panels"
    #: 系统监控采集（CPU/MEM，2s 节流）
    SYSTEM_STATS = "system_stats"
    #: 输出锁内排空渲染命令队列（锁不可用时跳过本帧）
    DRAIN_COMMANDS = "drain_commands"
    #: 锁外应用命令（纯模型状态变更，不写终端）
    APPLY = "apply"
    #: 渲染决策 + 渲染执行（含失败指数退避）
    RENDER = "render"


class InkSession(_SessionQueueMixin, _SessionFrameMixin):
    """Ink 渲染会话（facade）— 渲染循环调度 + 生命周期 + 崩溃恢复 + 注入。

    ★ 架构改进方向 A（2026-08-16，上帝类拆分）：命令队列管理（入队/背压/
    排空安全）与渲染帧执行（组件树/调和/渲染/光标/系统监控）已分别拆至
    ``_SessionQueueMixin`` / ``_SessionFrameMixin``；本类保留**会话协调**职责：
      - 渲染循环调度（_render / _drain_queue / _should_render / _needs_animation）
      - 线程生命周期（start / stop / suspend / resume / flush）
      - 崩溃自动恢复（_handle_render_crash / _drain_queue_safe 委托队列 mixin）
      - 依赖注入与访问器（set_input / set_line_tracker / request_* 等）
      - React Ink hooks 回调接线（useApp / useInput / useWindowSize / useCursor）

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
        # ★ 渲染帧序号 + router flush 等待者（2026-08-19，editmsg「很多上文
        #   时按回车不能编辑对应消息」根因修复）：弹窗清理后渲染线程发布
        #   新 input router 前存在窗口——旧 router（含已卸载弹窗的
        #   SelectInput handler + use_modal 吞噬）仍会把用户的 Enter 消费
        #   掉（``_enter()`` 不执行 → prefill 不提交）。``flush_input_router``
        #   同步等待渲染线程完成「清理后状态」的帧（router 已更新）再返回。
        self._frame_seq: int = 0
        self._frame_seq_lock = threading.Lock()
        self._frame_flush_waiters: list = []
        self._cmd_queue_dropped: int = 0
        self._render_crashed: threading.Event = threading.Event()
        self._last_bottom_redraw: float = 0.0
        # ★ 脏标记：模型有变更（命令应用/输入/重绘请求）时置位，
        #   空闲时跳过渲染（避免 10Hz 全量重建整棵树 → CPU 100%）
        self._dirty: bool = False
        # ★ 帧执行标记（2026-08-19，单帧耗时无上界约束）：``_render_frame``
        #   进入置位 / finally 复位——等待方据此区分「正在执行超长单帧」
        #   （有进展，续期等待）与「渲染线程挂起」（无进展，降级）。
        self._frame_active: bool = False
        self._recover_attempts: int = 0
        self._render_version: int = 0
        self._cmd_seq = itertools.count()
        self._input = None  # Phase F 接线注入
        # ★ P2（review）：``_line_tracker`` 显式初始化——修复前首帧赋值仅在
        #   ``set_line_tracker``（属性初始化依赖调用时序），消费方
        #   ``_lifecycle.py`` 走 ``getattr(self._engine, "_line_tracker", None)``
        #   防御式访问；显式 None 后直接属性访问不再抛 AttributeError。
        self._line_tracker = None
        # use_input router 发布缓存（_input 未注入时记录，set_input 后补发）
        self._pending_input_router = None
        # ★ hooks 全局接线约束（review 方向，单会话约束声明）：下方
        #   set_input_router_callback / set_app_control / set_std_accessors /
        #   set_window_size_accessor / set_cursor_position_fn /
        #   set_render_flush_fn / set_suspend_terminal_fn 为**模块级全局**
        #   （hooks 模块字段），持有本 session 强引用——本框架为单 InkSession
        #   会话模型（reconciler P3-17 同约束）：重复 assemble 时旧会话回调被
        #   新会话覆盖（最后一次构造者生效），stop() 不注销 hooks（与
        #   SIGWINCH 回调按 token 注销不对称——hooks 全局无多会话注册表，
        #   引入即多会话支持改造，超出单会话约束范围）。
        _hooks.set_input_router_callback(self._on_input_router)
        # ★ useApp 控制（方向B 步骤10）：session 注入 exit/clear 回调
        self._exit_requested = False
        _hooks.set_app_control({"exit": self.request_exit, "clear": self.request_clear})
        # ★ useStdin/useStdout/useStderr（完善 react ink）：session 注入惰性
        #   访问器——stdin 为 Input 实例（set_input 后可用），stdout 为渲染器
        #   输出流，stderr 为 sys.__stderr__（紧急路径一致）。
        _hooks.set_std_accessors(
            lambda: self._input,
            lambda: self._ink_renderer.stream,
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
        # 系统监控（CPU/MEM；每 sys_stats_interval 秒刷新输入区顶部分隔线显示）
        self._system_monitor = None
        self._last_sys_stats_time: float = 0.0
        self._sys_stats_interval: float = self._config.sys_stats_interval
        # ★ React Ink render() options（官方 API 补齐）：
        #   - ``_debug``：调试模式（True 时每渲染帧输出统计到 stderr）
        #   - ``_exit_on_ctrl_c``：Ctrl+C 是否退出（True 时 render() 独立会话
        #     Ctrl+C → request_exit；False 时 Ctrl+C 交给 useInput handler）
        #   - ``_last_frame_lines``：最近渲染帧行数（debug 统计用；
        #     _render_frame 渲染后写入）
        self._debug: bool = False
        self._exit_on_ctrl_c: bool = True
        self._last_frame_lines: int = 0
        #: stderr 流（render() stderr 选项；useStderr().stderr 与 debug 帧统计共用）
        self._stderr_stream = sys.__stderr__

    # ── 注入 ─────────────────────────────────────────

    def set_input(self, input_instance) -> None:
        """注入 Input 实例（render 循环输入分发）。

        ★ React Ink useStdin().isAnyKeyPressed 接线：向 InputDispatcher 注入
        任意键按下回调 → hooks 置位标志（``mark_any_key_pressed``）。Input
        外观委托存在（``set_key_pressed_callback``），异常（外部/测试构造的
        桩实例无此方法）吞掉记 debug——置位缺失仅影响 isAnyKeyPressed 读取，
        不阻断输入主路径。
        """
        self._input = input_instance
        # isAnyKeyPressed 置位接线（官方 React Ink useStdin 字段）
        try:
            input_instance.set_key_pressed_callback(_hooks.mark_any_key_pressed)
        except Exception:
            _logger.debug("set_input 注入 key pressed 回调异常", exc_info=True)
        # ★ use_input router 补发：构造期（_input 未注入）发布的 router 缓存于此，
        #   set_input 后补发最新 router，保证 useInput 钩子完整接线。
        if self._pending_input_router is not None and self._input is not None:
            try:
                self._input.set_input_hook_router(self._pending_input_router)
                self._pending_input_router = None
            except Exception:
                _logger.debug("set_input 补发 input router 异常", exc_info=True)

    def set_stderr(self, stream) -> None:
        """注入 stderr 流（render() stderr 选项；useStderr().stderr 读取）。

        默认 ``sys.__stderr__``（构造期注入）；替换后 useStderr 返回新流，
        debug 帧统计（``_debug_log_frame``）亦写入该流。传入 None 时恢复
        默认（``sys.__stderr__``）。
        """
        self._stderr_stream = stream if stream is not None else sys.__stderr__
        _hooks.set_std_accessors(
            lambda: self._input,
            lambda: self._ink_renderer.stream,
            lambda: self._stderr_stream,
        )

    def set_exit_on_ctrl_c(self, enabled: bool) -> None:
        """设置 Ctrl+C 退出标志（render() exitOnCtrlC 选项）。

        True（默认）：render() 独立会话 Ctrl+C 经 interrupt 回调请求退出；
        False：Ctrl+C 事件放行给 useInput handler（React Ink 语义）——由
        render() 按本标志配置 Input 的 interrupt 注入/放行。
        """
        self._exit_on_ctrl_c = bool(enabled)

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
            # ★ P3（review）：紧急写失败留痕（不裸吞）——紧急路径失败完全
            #   不可观测会掩盖流已关闭等问题；仅记 debug（紧急路径不宜
            #   再生副作用）。
            _logger.debug("紧急输出写入失败 stream=%s", stream, exc_info=True)

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
        #   ★ P3-20 竞态窗口说明（review 方向，2026-08-19 修正表述）：
        #   ``not self._render_running`` 检查与同步渲染之间存在竞态窗口——
        #   render 线程可能恰在检查后启动（start()/resume() 并发调用）→
        #   本帧同步渲染与线程渲染并行。注意：渲染线程的 RENDER 阶段在
        #   output lock **外**执行（InkRenderer 内部无锁），output lock 只
        #   串行化「同步帧 + DRAIN 阶段」与「外部写入方」，**不能**阻止
        #   同步帧与线程帧并发写 stream——已知权衡：双帧内容一致（同一
        #   模型状态），后写覆盖先写，最终显示一致；彻底消除需独立渲染
        #   互斥（见 _drain_queue P3-20 注释，未来方向）。
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

    def flush_input_router(self, timeout: float = 2.0) -> bool:
        """同步等待渲染线程完成「当前调用之后」状态的帧（input router 已更新）。

        ★ 2026-08-19（editmsg「很多上文时按回车不能编辑对应消息」根因修复）：
        模态弹窗（EditMsgSelectPopup / UserSelectPopup 等）清理后，渲染线程
        发布**新 input router** 前存在窗口——旧 router（含已卸载弹窗的
        ``SelectInput`` use_input handler + ``use_modal`` 吞噬）仍会把用户的
        Enter **消费掉**（router 返回 True → InputDispatcher 跳过 ``_enter()``
        → prefill 不提交 →「按回车没反应，要再按一次」）。窗口时长 =
        清理时刻 → 渲染线程完成下一帧（10Hz 节流 + 帧耗时——大量上文重放
        时一帧 100ms~1s+）——「很多上文时大概率复现，1 条消息快速连按也
        会命中」。

        本方法阻塞等待渲染线程完成两帧（``_frame_seq + 2``）后返回，保证
        调用方此前的模型清理（如 ``model.editmsg_select`` 重置、
        ``bottom_view`` 复位）已被至少一帧渲染消费——新 router 不再含弹窗
        hooks，用户的 Enter 走正常 ``_enter()`` 提交路径。

        等两帧（而非一帧）的原因：调用时渲染线程可能正在渲染「读到清理前
        模型」的旧帧（该帧发布的 router 仍含弹窗 hooks，早退会误判完成）；
        再等一帧确保读到清理后状态。

        ★ W5 加固（慢渲染自适应，2026-08-19）：固定 2s 超时在慢渲染场景
        （一帧 >1s，大量上文 clear+display 重放）下会过早降级（两帧 >2s →
        返回 False → 旧 router 残留 → 用户 Enter 仍被吞）。改为**分段等待 +
        进展续期**：每段 0.5s；段末检查进展——**帧号推进 或 帧执行中
        （``_frame_active``）**则续期软超时继续等；无进展最多等满
        ``timeout``；总时长硬上限 ``max(timeout, _ROUTER_FLUSH_HARD_CEILING)``
        （渲染线程真挂起时不死锁调用方）。

        ★ 单帧耗时无上界（2026-08-19）：超长单帧执行期间帧号不推进（帧号
        仅在帧完成时递增）——``_frame_active`` 信号保证「正在渲染」被视作
        有进展（每帧执行多久都可以），不因慢帧误降级。

        Args:
            timeout: 软超时（秒）——无进展时的最长等待；有进展时按段续期，
                受硬上限（``max(timeout, 60)``）约束。

        Returns:
            True — 目标帧已完成（新 router 已发布）；
            False — 超时（渲染线程未完成两帧/已挂起）。
        """
        if not self._render_running:
            # render 线程未运行（suspend 等）：request_bottom_redraw 内部
            # 同步渲染一帧（router 随 reconciler.render 发布）——无需等待。
            self.request_bottom_redraw()
            return True
        ev = threading.Event()
        with self._frame_seq_lock:
            target = self._frame_seq + 2
            base_seq = self._frame_seq
            self._frame_flush_waiters.append((target, ev))
        # force 唤醒：设置重绘请求（跳过 10Hz 节流）+ 命令事件（提前退出
        # 节流等待），渲染线程尽快完成目标帧。
        self._bottom_redraw_requested.set()
        self._dirty = True
        self._cmd_event.set()
        now = time.monotonic()
        soft_deadline = now + timeout
        hard_deadline = now + max(timeout, _ROUTER_FLUSH_HARD_CEILING)
        while True:
            limit = min(soft_deadline, hard_deadline)
            remain = limit - time.monotonic()
            if remain <= 0:
                break
            if ev.wait(min(0.5, remain)):
                return True
            # 段末检查进展：帧号推进 或 帧执行中（超长单帧——帧号仅在帧
            # 完成时递增，帧内执行多久都视作有进展）→ 慢渲染场景续期软
            # 超时（硬上限内继续等）；无进展 → 可能是线程挂起，按软超时
            # 继续分段等。
            with self._frame_seq_lock:
                cur = self._frame_seq
            if cur > base_seq or self._frame_active:
                base_seq = cur
                soft_deadline = time.monotonic() + timeout
        # 超时：移除 waiter（防列表增长；事件无人等待，唤醒无害但清理干净）
        with self._frame_seq_lock:
            self._frame_flush_waiters = [
                w for w in self._frame_flush_waiters if w[1] is not ev
            ]
        _logger.warning(
            "flush_input_router 等待渲染帧超时——旧 router 可能短暂残留，"
            "弹窗后首次 Enter 可能被吞",
        )
        return False

    def _advance_frame_seq(self) -> None:
        """帧序号递增并唤醒达到目标的 flush 等待者（渲染线程调用）。

        由 ``_render_frame`` 末尾调用——reconciler.render 已发布本帧的
        input router，达到 ``target <= _frame_seq`` 的等待者可以安全返回。
        """
        with self._frame_seq_lock:
            self._frame_seq += 1
            seq = self._frame_seq
            ready = [w for w in self._frame_flush_waiters if w[0] <= seq]
            if ready:
                self._frame_flush_waiters = [
                    w for w in self._frame_flush_waiters if w[0] > seq
                ]
        for _target, ev in ready:
            ev.set()

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
        且无脏标记（渲染完成）后返回。

        ★ 单帧耗时无上界（2026-08-19）：原固定 5s 死线在慢渲染场景（超长
        单帧 >5s，大量上文 markdown 重放）下会提前返回——调用方误以为渲染
        已完成。改为自适应等待：
          - 完成（队列排空 + 无脏标记 + **无帧执行中**——渲染线程时序为
            DRAIN 取空队列 → 清 ``_dirty`` → ``_render_frame`` 执行，若只判
            前两者会在帧执行中提前返回假阳性）或渲染线程停止 → 立即返回；
          - 帧号推进 或 帧执行中（``_frame_active``）= 有进展 → 续期软超时
            （``_RENDER_FLUSH_SOFT_TIMEOUT``，每帧执行多久都可以）；
          - 无进展超软超时 / 达硬上限（``_RENDER_FLUSH_HARD_TIMEOUT``，
            真挂起防护）→ 降级返回（记 warning，与 flush_input_router /
            _join_render_thread 同族降级日志对齐）。
        """
        async def _waiter():
            import asyncio
            try:
                import time as _t
                soft_deadline = _t.monotonic() + _RENDER_FLUSH_SOFT_TIMEOUT
                hard_deadline = _t.monotonic() + _RENDER_FLUSH_HARD_TIMEOUT
                last_seq = self._frame_seq
                while True:
                    if (
                        self._cmd_queue.empty()
                        and not self._dirty
                        and not self._frame_active
                    ) or not self._render_running:
                        return
                    thread = self._render_thread
                    if thread is None or not thread.is_alive():
                        return
                    now = _t.monotonic()
                    if now >= min(soft_deadline, hard_deadline):
                        reason = (
                            "硬上限（渲染线程可能挂起）"
                            if now >= hard_deadline
                            else "无进展软超时"
                        )
                        _logger.warning(
                            "wait_render_flush 降级返回（%s）：seq=%d queue_empty=%s "
                            "dirty=%s frame_active=%s",
                            reason, self._frame_seq,
                            self._cmd_queue.empty(), self._dirty,
                            self._frame_active,
                        )
                        return
                    await asyncio.sleep(0.01)
                    with self._frame_seq_lock:
                        cur = self._frame_seq
                    if cur != last_seq or self._frame_active:
                        last_seq = cur
                        soft_deadline = _t.monotonic() + _RENDER_FLUSH_SOFT_TIMEOUT
            except Exception:
                _logger.debug("wait_render_flush 异常", exc_info=True)
        return _waiter()

    def _join_render_thread(self, *, hard_timeout: float | None = None) -> bool:
        """自适应等待渲染线程退出（stop/suspend/resume 共用）。

        ★ 单帧耗时无上界（2026-08-19）：原固定 join(timeout=2.0) 在渲染
        线程执行超长单帧（>2s）时误判「卡住」→ 强制清理与仍在写 stream
        的渲染线程并发（输出撕裂）/ resume 强启新线程（双线程双写终端）。
        改为分段 join（每段 0.2s）：线程活着且未达硬上限就一直等——线程
        正在执行慢帧（无论多久）终会退出；硬上限（真挂起，如 PTY 缓冲区
        满 write 永久阻塞）触达时放弃并返回 False（调用方按既有降级语义
        处理）。每段重读 ``self._render_thread``——崩溃恢复重启新线程
        （BUG-T9）时自动跟随 join 新线程。

        Args:
            hard_timeout: 硬上限秒数；None 用 ``_JOIN_RENDER_HARD_TIMEOUT``。

        Returns:
            True — 线程已退出（或本就无线程）；
            False — 达硬上限放弃（线程可能仍在运行）。
        """
        if hard_timeout is None:
            hard_timeout = _JOIN_RENDER_HARD_TIMEOUT
        deadline = time.monotonic() + hard_timeout
        while True:
            thread = self._render_thread
            if thread is None or not thread.is_alive():
                return True
            thread.join(timeout=0.2)
            if not thread.is_alive():
                return True
            if time.monotonic() >= deadline:
                _logger.warning(
                    "等待 render 线程退出超时（硬上限 %.0fs，版本=%d），放弃等待",
                    hard_timeout, self._render_version,
                )
                return False

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
        # ★ 单帧耗时无上界（2026-08-19）：自适应 join——渲染线程正在执行
        #   超长单帧（>2s 的 markdown 重放等）时不再按固定 2s 误判「卡住」
        #   强制清理（与仍在写 stream 的线程并发 → 输出撕裂）。线程活着
        #   就等到它退出（每帧执行多久都可以）；仅真挂起时硬上限放弃。
        #   BUG-T9：崩溃恢复重启新线程——_join_render_thread 每段重读
        #   self._render_thread，自动跟随 join 新线程。
        joined = self._join_render_thread()
        if not joined:
            # 兜底：线程仍存活（真挂起）→ 记 warning 并排空队列（不无限等待）
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
        # ★ 架构改进方向 C：stop 时注销本会话的 SIGWINCH 回调——释放回调
        #   注册表对会话实例的强引用（修复前回调从不注销，stop 后会话对象
        #   仍被全局注册表持有；重复 assemble 时旧会话回调仍被触发）。
        #   幂等：未注册/已注销时无操作。stop 后再次 start 会经装配层重新
        #   注册（InkSession 生命周期为「装配注册一次，stop 注销」）。
        try:
            from src.tui._screen import unregister_sigwinch_callback
            unregister_sigwinch_callback(self)
        except Exception:
            _logger.debug("stop 注销 SIGWINCH 回调异常", exc_info=True)

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
            self._drain_queue_safe()
            return
        # ★ P1-1（review 方向）：daemon=True——修复前 ``daemon=False`` 泄漏
        #   非 daemon 线程：flush 超时后 ``_drain_queue_safe(keep_content=True)``
        #   保留内容命令不消费、不 task_done → ``unfinished_tasks`` 永不归零 →
        #   本线程永远阻塞在 ``queue.join()``，进程退出时挂起。daemon=True 后
        #   进程退出不等待本线程（渲染线程停止时 ``_drain_queue_safe`` 兜底
        #   已清理队列；线程随进程退出自然终止，无资源泄漏）。flush() 其余
        #   创建线程处（start/resume/崩溃恢复）均已 daemon=True，仅此一处遗漏。
        drain_waiter = threading.Thread(target=self._cmd_queue.join, daemon=True)
        drain_waiter.start()
        drain_waiter.join(timeout=timeout)
        if drain_waiter.is_alive():
            # ★ 修复（长任务思考/回答丢失）：超时后渲染线程存活则继续等待排空
            #   （每次 1s 轮询），仅当渲染线程停止（_render_running=False 或
            #   线程退出）才丢弃剩余命令兜底——避免超时即丢内容命令。
            while (
                drain_waiter.is_alive()
                and self._render_running
                and self._render_thread is not None
                and self._render_thread.is_alive()
            ):
                drain_waiter.join(timeout=1.0)
            if drain_waiter.is_alive():
                # ★ 2026-08-15（短内容丢失修复）：flush 超时兜底丢弃时保留
                #   内容命令（思考/回答/工具卡）——修复前超时即丢弃队列中
                #   未消费的 reasoning/content，长任务/渲染暂停后短内容丢失。
                self._drain_queue_safe(keep_content=True)
                drain_waiter.join(timeout=1.0)

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
        # ★ 单帧耗时无上界（2026-08-19）：自适应 join（原固定 2s+2s——渲染
        #   线程执行超长单帧时被误判卡住，强制清理与仍在写 stream 的线程
        #   并发 → 让出终端后输出撕裂）。真挂起时硬上限放弃 + warning。
        joined = self._join_render_thread()
        if not joined:
            _logger.warning(
                "suspend() render 线程等待超时仍存活（版本=%d），强制继续清理",
                self._render_version,
            )
        self._ink_renderer.suspend()
        # ★ 2026-08-15（短内容丢失修复）：suspend 清空队列时**保留内容命令**
        #   （思考/回答/工具卡等）——模型在交互工具挂起期间输出的短内容命令
        #   不丢弃，resume 后渲染线程处理显示（修复前无条件丢弃 → 偶发丢失）。
        self._drain_queue_safe(keep_content=True)
        # 定位光标到终端底部：交互工具同步渲染弹窗的起点
        # ★ P3（review）：经 InkRenderer 公开 API（``goto_bottom``）——修复前
        #   跨对象直写私有成员 ``_ink_renderer._stream``。
        try:
            _, h = _get_terminal_size()
            self._ink_renderer.goto_bottom(max(1, h))
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
            # ★ 单帧耗时无上界（2026-08-19）：自适应 join（原固定 2s——超长
            #   单帧误判 → 强启新线程 → 双渲染线程并发双写终端）。真挂起时
            #   硬上限放弃后才强制启动新线程。
            joined = self._join_render_thread()
            if not joined:
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
        """单帧处理：六阶段显式状态机（架构改进方向 E，2026-08-16）。

        阶段迁移：``SIGWINCH → INPUT → PANELS → SYSTEM_STATS →
        DRAIN_COMMANDS → APPLY → RENDER``（RENDER 为终态，返回本帧是否有
        命令变更）。各阶段职责与改动前完全一致（行为零变化）——仅将隐式
        顺序改写为显式阶段迁移（``RenderLoopPhase``），阶段可独立测试/
        扩展，未来插入新阶段（如崩溃恢复检查）不破坏既有流程。

        设计要点（保留自重构前注释）：
          - 输出锁仅保护队列排空（DRAIN_COMMANDS 阶段）——命令应用（APPLY）
            与渲染（RENDER）在锁外执行：``_apply_commands`` 含 markdown
            全量渲染等耗时操作，持锁会阻塞其他写入方；渲染失败退避 sleep
            亦不持锁。
          - 锁不可用（DRAIN_COMMANDS 超时未获取）→ 跳过本帧（返回 False，
            不渲染），与旧行为一致。
          - P3-20：渲染本身不受锁互斥（request_bottom_redraw 同步渲染可能
            与本线程渲染并行，双写终端——output lock 串行化写入防撕裂，
            双帧顺序不确定性可接受）；若未来需要渲染互斥，应引入独立渲染锁。
        """
        phase = RenderLoopPhase.SIGWINCH
        commands: list = []
        changed = False
        while True:
            if phase is RenderLoopPhase.SIGWINCH:
                self._phase_process_sigwinch()
                phase = RenderLoopPhase.INPUT
            elif phase is RenderLoopPhase.INPUT:
                self._phase_process_input()
                phase = RenderLoopPhase.PANELS
            elif phase is RenderLoopPhase.PANELS:
                self._phase_pre_update_panels()
                phase = RenderLoopPhase.SYSTEM_STATS
            elif phase is RenderLoopPhase.SYSTEM_STATS:
                self._update_system_stats()
                phase = RenderLoopPhase.DRAIN_COMMANDS
            elif phase is RenderLoopPhase.DRAIN_COMMANDS:
                commands, changed, locked = self._drain_commands_locked()
                if not locked:
                    return False  # 锁超时：跳过本帧（不渲染），与旧行为一致
                phase = RenderLoopPhase.APPLY
            elif phase is RenderLoopPhase.APPLY:
                # 锁外应用命令（review 方向）：apply_cmd 是纯模型状态变更
                # （AppModel），不直接写终端——移出输出锁避免 markdown 渲染
                # 等耗时操作持锁阻塞其他写入方。模型仅 render 线程修改
                # （push_cmd 只入队不应用），线程安全。
                if commands:
                    # 方向3（宽度源统一）：应用命令前刷新 model.width——committed
                    # 行（按 model.width wrap）与 live 行（按渲染宽度 wrap）同源，
                    # 避免 resize/首帧批次提交用陈旧宽度（_render_frame 中更新
                    # 发生在命令应用之后）。幂等：_render_frame 的
                    # ``model.width = width`` 保持。
                    if self._model is not None and hasattr(self._model, "width"):
                        try:
                            self._model.width = self._width_cache.get_width()
                        except Exception:
                            _logger.debug("应用命令前刷新 model.width 异常", exc_info=True)
                    self._apply_commands(commands)
                phase = RenderLoopPhase.RENDER
            elif phase is RenderLoopPhase.RENDER:
                # 渲染与失败处理（锁外）：渲染失败退避 sleep 不持有输出锁
                # （修复前 sleep 在锁块内 → render_lock 阻塞其他写入方输出）。
                if self._should_render(changed):
                    try:
                        self._render_frame()
                    except Exception:
                        # P3-16（设计说明）：无 boundary 异常端到端不触发崩溃
                        # 恢复——reconciler 层对无边界异常 re-raise 保留，但
                        # session 层在此吞掉仅记 warning：**单帧失败 + 10Hz
                        # 重试**（render 线程不崩溃不重启）。reconciler.
                        # _handle_render_crash 崩溃恢复仅服务 render 循环级异常
                        # （队列/输入/面板回调等）。
                        # P7（方向2）：持久性渲染异常从 10Hz 无限重试降为指数
                        # 退避（0.1→0.2→0.4→…→1.0 封顶，≤1Hz），日志刷屏缓解；
                        # 正常路径无 sleep（渲染帧率 10Hz 不降低）。
                        # 方向1（渲染失败帧不重试修复）：_should_render 已清
                        # _dirty，失败帧若不补置脏标记下一拍不会重试（仅退避
                        # 等待）——补置 _dirty = True，下一 10Hz 拍重试，配合
                        # 既有指数退避防刷屏（退避封顶 1s，不会无限重试）。
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
                        # ★ render() debug 选项（官方 React Ink）：调试模式下
                        #   每渲染帧输出统计到 stderr（帧号/行数/队列积压），
                        #   便于观察渲染节奏与性能。非渲染帧（跳过）无输出。
                        if self._debug:
                            self._debug_log_frame()
                return changed

    def _debug_log_frame(self) -> None:
        """debug 模式帧统计输出（render() debug 选项）。

        渲染帧成功后将统计写入 stderr 流（``_stderr_stream``，render()
        stderr 选项注入；缺省 sys.__stderr__）：帧行数 + 队列积压 + 渲染
        失败计数。异常吞掉（debug 输出为观测辅助，不阻断渲染循环）。
        """
        try:
            self._stderr_stream.write(
                f"[ink:debug] frame lines={self._last_frame_lines} "
                f"queue={self._cmd_queue.qsize()} "
                f"failures={self._consecutive_render_failures}\n"
            )
            self._stderr_stream.flush()
        except Exception:
            _logger.debug("debug 帧统计输出异常", exc_info=True)

    def _drain_commands_locked(self):
        """输出锁内排空渲染命令队列（DRAIN_COMMANDS 阶段）。

        锁块内**仅排空命令**——命令应用（``_apply_commands``）与渲染在锁外
        执行（见 ``_drain_queue`` APPLY/RENDER 阶段说明）。

        Returns:
            (commands, changed, locked) 三元组：
              - commands: 本帧排空的命令列表（≤ max_batch_size）；
              - changed:  是否有命令被排空；
              - locked:   True=锁已获取；False=锁超时（调用方跳过本帧）。
        """
        commands: list = []
        with _try_acquire_output_lock(
            name="ink_session.drain_queue",
            timeout=self._config.drain_lock_timeout,
        ) as locked:
            if not locked:
                return [], False, False
            while len(commands) < self._config.max_batch_size:
                try:
                    _, _, cmd = self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                    commands.append(cmd)
                except queue.Empty:
                    break
        return commands, bool(commands), True

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
        # ★ TOCTOU 说明（review 方向，已知权衡）：force 读-清两步非原子——
        #   ``is_set()`` 与 ``clear()`` 之间并发 set 的 force 会被本次 clear
        #   吞掉（该请求降级为下一 10Hz 拍渲染，延迟 ≤ render_interval 0.1s，
        #   用户不可感知）。原子化需 Condition/锁，热路径成本不值——窗口内
        #   丢失的 force 语义由 10Hz 全程渲染兜底。
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

    # ── 阶段 ─────────────────────────────────────────

    def _phase_process_sigwinch(self) -> None:
        """轮询处理 SIGWINCH（BUG-T4：信号处理器只置标志，渲染循环此处消费）。

        process_sigwinch() 返回 True 时，回调已触发 force_refresh / 请求重绘，
        无需额外置脏。
        """
        try:
            process_sigwinch()
        except Exception:
            _logger.debug("_phase_process_sigwinch 异常", exc_info=True)

    # ── SIGWINCH 回调（架构改进方向 C：实例方法替代模块级全局态） ──

    def _on_sigwinch(self, cols: int, rows: int) -> None:
        """SIGWINCH 回调：刷新宽度缓存 + 请求底部重绘。

        架构改进方向 C（2026-08-16）：以**实例方法**替代旧模块级
        ``_active_session`` 全局引用 + 稳定闭包组合——多 TUI 实例各持自身
        回调（经 ``register_sigwinch_callback(self._on_sigwinch, token=self)``
        按 token 去重注册，``stop()`` 时注销），消除全局可变引用与陈旧
        会话刷新错乱。
        """
        try:
            self._width_cache.force_refresh()
            self.request_bottom_redraw()
        except Exception:
            # P2-9：不裸吞异常——记录 debug 日志（SIGWINCH 刷新异常属非关键
            # 降级，不阻断信号处理）。
            _logger.debug("SIGWINCH 刷新异常", exc_info=True)

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
            # ★ P3（review）：删除死状态 ``_recovering_event``——原字段仅在此
            #   ``set()`` 从未被读取/清除（全项目无消费方），崩溃恢复进行中
            #   的可观测性已由 ``_render_crashed`` / 日志覆盖。
            self._render_thread = threading.Thread(target=self._render, daemon=True)
            self._render_thread.start()
            _logger.info("render 线程已自动恢复 (第 %d/%d 次)",
                         self._recover_attempts, self._config.max_recover_attempts)
            return True
        else:
            self._render_running = False
            self._cmd_event.set()
            return False


# ★ render() 轻量入口（方向 F1）已拆分至独立模块 _render_api.py
#   （2026-08-05 架构优化）——本模块 re-export 保持旧导入路径兼容
#   （``from src.tui.ink.session import render`` 仍可用，测试锁定）。
from ._render_api import render, measureElement, _SimpleModel  # noqa: F401  re-export 兼容

__all__ = [
    "InkSession",
    "render",
    "_get_cmd_priority",
    "_get_cmd_id",
    "_CRITICAL_CMDS",
    "_STREAM_CMDS",
    "_CONTENT_COMMANDS",
    # 架构改进方向 A：命令队列 mixin 常量 / 系统监控防御工具 re-export
    # （旧导入路径兼容：``src.tui.ink.session._KEEP_CONTENT_CMDS`` 等）
    "_KEEP_CONTENT_CMDS",
    "_PUT_NO_DROP_TIMEOUT",
    "_safe_int",
]
