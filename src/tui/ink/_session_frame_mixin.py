"""_SessionFrameMixin — InkSession 渲染帧执行子域（架构改进方向 A，2026-08-16）。

拆分背景：InkSession（原 ~1540 行）为「上帝类」——命令队列/线程生命周期/
渲染循环/崩溃恢复/hooks 等职责混杂。方向 A 按**可独立测试的职责边界**
拆分：渲染帧执行（组件树构建 + 调和 + 渲染 + 光标 + 宽度传播 + 系统监控）
收敛为本 mixin，命令入队/背压/排空安全为 ``_session_queue_mixin._SessionQueueMixin``，
session 保留渲染循环调度（_render/_drain_queue/_should_render）与生命周期。

本 mixin 承载：
  - ``_render_frame`` — 构建组件树 → 调和 → 渲染 → 输出 → 光标（含 resize
    宽度/高度传播、全量刷新标志、input-area fiber 缓存、``_frame_active``
    帧执行标记包装、末尾 ``_advance_frame_seq`` 帧完成通知）；
  - ``_apply_commands`` — 批量应用命令到模型（CLEAR_MSGS 置 resize 全量刷新）；
  - ``_update_system_stats`` — 每采集间隔（TuiConfig.sys_stats_interval）采集
    CPU/MEM 写入模型（输入区分隔线）；
  - ``_position_cursor`` / ``_find_input_fiber`` — 输入光标定位（委托纯函数
    模块 ``._cursor``）。

依赖约定（由 InkSession.__init__ 初始化，运行时经 ``self`` 访问）：
  - ``_build_tree`` / ``_model`` / ``_apply_fn`` — 组件树构建与命令应用注入；
  - ``_reconciler`` / ``_root_fiber`` — React Ink 调和器与根 fiber；
  - ``_ink_renderer`` / ``_width_cache`` / ``_config`` — 渲染器与尺寸缓存；
  - ``_input_fiber`` / ``_last_render_width`` / ``_last_render_height`` /
    ``_resize_pending`` / ``_dirty`` / ``_frame_active`` — 帧状态（缓存/全量
    刷新/脏标记/帧执行标记——标记由本 mixin ``_render_frame`` 包装器维护，
    session 侧等待方读取）；
  - ``_advance_frame_seq``（经 getattr 调用的 session 方法，宿主类提供）—
    帧完成通知（唤醒 flush_input_router 等待者）；
  - ``_system_monitor`` / ``_last_sys_stats_time`` / ``_sys_stats_interval``
    — 系统监控状态。

行为说明：2026-08-16 拆分时为原 InkSession 同名方法原样迁移；此后
``_render_frame`` 增量演进（帧执行标记包装 + 帧完成通知，见上），其余
方法仍为迁移原样。测试以实例属性替换（monkeypatch）方法仍生效（本
mixin 方法即实例方法）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅静态检查（避免运行期循环导入）
    from .renderer import Frame

import logging
import math
import time

from src.tui._const import RenderCommand
from src.tui.ink._cmd_priority import _get_cmd_id, _cmd_name
from src.tui.ink import components as _components
from src.tui.ink import hooks as _hooks
from src.tui.ink import _cursor
from src.tui.ink import _stream_height
from src.tui.ink._animation import advance_animation, has_active_animations

_logger = logging.getLogger(__name__)

#: 终端尺寸主动轮询间隔（秒）——SIGWINCH 兜底（2026-10-05）：部分终端/平台
#: 窗口 resize **不发送 SIGWINCH**（Cygwin pty 实测 winsize 变化不触发信号），
#: ``TerminalWidthCache`` 默认 TTL 60s 内宽度陈旧——工具卡/布局宽度不随窗口
#: 变化。渲染帧每帧经 ``_poll_terminal_size`` 按本间隔主动重探（5 次/秒
#: ioctl 成本可忽略，渲染帧本就 60Hz）。
_SIZE_POLL_INTERVAL = 0.2

#: layout effect 触发的渲染前重跑上限（React 语义：layout effect 中的
#: set_state 应在绘制前 flush）——定义在 ``reconciler``（同一次
#: ``render`` 调用内部的提交阶段，不改变对外「一帧一次构建」语义）。


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
        转换后的整数；失败（TypeError/ValueError/OverflowError——含
        ``int(float('inf'))`` 的溢出）返回 ``default``。
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class _SessionFrameMixin:
    """InkSession 渲染帧执行子域（mixin）。

    无独立状态——方法经 ``self`` 访问 InkSession 实例字段（模块 docstring
    依赖约定）。方法可直接被测试以实例属性替换（monkeypatch 语义保持）。
    """

    # ── 类型标注（InkSession.__init__ 初始化） ──
    _build_tree: Any
    _model: Any
    _apply_fn: Any
    _reconciler: Any
    _root_fiber: Any
    _ink_renderer: Any
    _width_cache: Any
    _config: Any
    _input_fiber: Any
    #: 下屏（``dataFloatAnchor="bottom_area"``）fiber 缓存（高度锁定补白锚点）
    _bottom_area_fiber: Any
    #: 流式高度锁定器（``_stream_height.StreamHeightLock``）
    _height_lock: Any
    #: 最近一帧实际补白行数（诊断/测试用）
    _frame_bottom_pad: int
    #: 本帧补白是否已在 ``render_frame`` 构建期完成（2026-10-23 浮窗路径）
    _stream_pad_handled: bool
    _last_render_width: int
    _last_render_height: int
    _last_size_poll: float
    _resize_pending: bool
    #: 首帧尺寸基准同步标志（InkSession.__init__ 置 True，首帧渲染后置 False）
    _first_frame_pending: bool
    _dirty: bool
    _frame_active: bool
    _system_monitor: Any
    _last_sys_stats_time: float
    _sys_stats_interval: float
    _hook_ctx: Any
    _on_render_callback: Any
    _cmd_queue: Any
    #: 最近一帧（空闲帧复用直接重放；None = 尚未渲染过任何帧）
    _last_frame: "Frame | None"
    #: 最近一帧是否在「静态态」下构建（False = 该帧仍含活跃态内容，禁止复用）
    _last_frame_idle_static: bool
    #: 最近一帧**实际重建**时刻（monotonic；空闲帧复用不更新）——浮窗
    #: ``refresh_interval`` 到期判定基准（见 ``_float_refresh_due``）
    _last_frame_built_at: float
    #: 最近渲染帧行数（render(debug=True) 统计）
    _last_frame_lines: int

    # ── 命令应用 ─────────────────────────────────────

    def _apply_commands(self, commands: list, budget: float | None = None) -> list:
        """批量应用命令到模型。

        ★ 性能（流式超长单行）：先经 ``coalesce_commands`` 合并本批内相邻的
        CONTENT / REASONING 增量命令——同一帧内多条增量只触发一次 markdown
        预览刷新（超长单行每次刷新都要重渲染 + 重新换行整个尾部窗口）。合并
        为纯追加语义，渲染结果与逐条应用等价。

        ★ 2026-10-23（命令时间盒）：``budget`` 非 None 时按时间预算应用，
        超出预算的**剩余命令原样返回**（调用方存入 deferred 队列，下一帧优先
        应用）——单条命令（markdown 全量重渲染）仍可能超预算，但不会让数百条
        历史回放命令在一帧内全部执行把输入处理推迟数百 ms。

        Returns:
            未应用的剩余命令（顺序不变）；全部应用时为空列表。
        """
        if self._apply_fn is None:
            return []
        try:
            from src.tui.app.apply import coalesce_commands
            merged = coalesce_commands(commands)
        except Exception:
            _logger.debug("命令合并失败，按原批应用", exc_info=True)
            merged = commands
        start = time.monotonic()
        for index, cmd in enumerate(merged):
            if budget is not None and index > 0 and time.monotonic() - start >= budget:
                return list(merged[index:])
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
                    # ★ 输出历史基线同步（清屏/重放）：committed_lines 被清空，
                    #   文档行号空间重建——旧基线（已回调内容行数）失效，必须
                    #   归零，否则清屏后重放/新内容会按旧行号区间错位回调
                    #   （回调到无关行 / 漏记）。
                    try:
                        self._ink_renderer.reset_content_lines()
                    except Exception:
                        _logger.debug("reset_content_lines 异常", exc_info=True)
            except Exception:
                _logger.warning("应用命令 %s 失败", _cmd_name(_get_cmd_id(cmd)), exc_info=True)
        return []

    # ── 渲染帧 ───────────────────────────────────────

    def _render_frame(self) -> None:
        """构建组件树 → 调和 → 渲染 → 输出 → 光标。

        ★ 帧执行标记（``_frame_active``）：进入置位 / finally 复位——外部
        等待方（flush_input_router / _wait_render_flush / _join_render_thread）
        据此区分「正在执行超长单帧」（单帧耗时无上界，视作有进展续期等待）
        与「渲染线程挂起」（无进展，按软超时/硬上限降级）。

        ★ 渲染互斥（``_render_lock``，可重入）：统一串行化本帧执行——正常
        路径下只有 render 线程的每拍单次调用会进入本方法；**例外**是
        render 线程未运行（suspend 期间）时 ``request_bottom_redraw`` /
        ``clear_screen`` 的同步渲染（无 game_loop 可用），锁避免其与
        线程帧并发写 stream。stub（无 ``_render_lock``）保持既有语义。
        """
        lock = getattr(self, "_render_lock", None)
        if lock is None:
            self._frame_active = True
            try:
                self._render_frame_impl()
            except Exception as exc:
                self._render_failure_frame(exc)
            finally:
                self._frame_active = False
            return
        with lock:
            self._frame_active = True
            try:
                self._render_frame_impl()
            except Exception as exc:
                self._render_failure_frame(exc)
            finally:
                self._frame_active = False

    def _render_failure_frame(self, exc: Exception) -> None:
        """渲染/布局异常 → 旁路输出可见错误提示（不静默、不破坏渲染器记账）。

        ★ 2026-10-23（异常可观测性）：修复前帧构建异常由 session 层按指数
        退避 ``time.sleep(0.1~1.0)`` 重试——界面冻结且用户无任何提示。

        ★ 2026-10-23（渲染故障自愈，第二轮修复）：修复前的错误帧走
        ``InkRenderer.render(Frame([错误行]))``——把整篇文档模型替换成 1 行，
        恢复时依赖漂移/重写路径重新对齐；若渲染器记账已损坏，错误帧自身
        也会再次抛错（兜底失效）。现改为：
          1. ``emit_error_line`` **旁路直写**当前光标行（限频 1s），不进入
             帧/光标模型，不修改 ``_prev``/``_buf_h``；
          2. ``mark_corrupted`` 重置渲染器记账并置「下次全量重绘」——下一帧
             成功时清可见区（ED2，不清 scrollback）后完整重画，从任意损坏中
             恢复；
          3. 清空 ``_last_frame``（禁止空闲复用陈旧帧）并置脏（下一拍重试）。
        """
        now = time.monotonic()
        last = float(getattr(self, "_last_render_fault_log", 0.0) or 0.0)
        if now - last >= 5.0:
            self._last_render_fault_log = now
            _logger.error(
                "渲染帧构建异常（已输出可见错误提示）",
                exc_info=True,
                extra={
                    "ink_error_type": type(exc).__name__,
                    "ink_error": str(exc),
                },
            )
        # ★ 可见错误提示：旁路直写（限频 1s——持续异常时不刷屏/不抖动）。
        last_show = float(getattr(self, "_last_render_fault_show", 0.0) or 0.0)
        if now - last_show >= 1.0:
            self._last_render_fault_show = now
            text = f"[render error: {type(exc).__name__}: {exc}]"
            try:
                width = int(self._width_cache.get_width())
            except Exception:
                width = 80
            if width > 0:
                text = text[:width]
            try:
                self._ink_renderer.emit_error_line(text)
            except Exception:
                # 错误提示输出失败本身必须可见（限频由外层 5s 日志覆盖）。
                _logger.error("错误提示旁路输出失败", exc_info=True)
        # ★ 标记渲染器损坏：下一次成功帧全量重绘（仅内存重置，不做 I/O）。
        try:
            self._ink_renderer.mark_corrupted()
        except Exception:
            _logger.error("渲染器损坏标记失败", exc_info=True)
        # 禁止空闲帧复用陈旧帧；置脏保证下一拍重试正常路径。
        self._last_frame = None
        self._last_frame_lines = 1
        self._dirty = True

    def _poll_terminal_size(self) -> None:
        """SIGWINCH 兜底：定期主动重探终端尺寸（刷新 ``_width_cache``）。

        ★ 2026-10-05（用户需求：工具卡宽度跟随终端宽度变化）：部分终端/
        平台窗口 resize 不发送 SIGWINCH（Cygwin pty 实测 winsize 变化不
        触发信号），仅靠 TTL（默认 60s）刷新时宽度长时间陈旧——工具卡/
        布局不随窗口变化。渲染帧每帧调用本方法（内部按
        ``_SIZE_POLL_INTERVAL`` 节流）：陈旧即重探；宽度变化由调用方
        （``_render_frame_impl``）的 ``width != _last_render_width`` 分支
        感知 → reflow 已提交行 + 全量重绘 + 向开放通道传播新宽度。

        ``_override``（``render()`` 显式尺寸）时 ``poll`` 不探测（尺寸固定）。
        """
        now = time.monotonic()
        if now - self._last_size_poll < _SIZE_POLL_INTERVAL:
            return
        self._last_size_poll = now
        try:
            self._width_cache.poll(_SIZE_POLL_INTERVAL)
        except Exception:
            _logger.debug("终端尺寸轮询异常", exc_info=True)

    def _idle_static_now(self) -> bool:
        """求值宿主「静态状态」预测（未注入/异常 → 仍按静态处理）。

        与 ``_should_reuse_idle_frame`` 拆开：帧执行器每拍都需要本拍的预测
        结果来维护 ``_last_frame_idle_static``（见该属性说明），而复用判定
        还需要额外的前置条件。预测异常按「非静态」处理（宁可多渲染一帧）。

        ★ 2026-10-23（idle 默认动态）：未注入预测函数时**默认按动态处理**
        ——每帧重建。修复前默认「静态」会让时间/外部数据驱动但未声明的
        第三方组件冻结在旧帧（且静态态下没有脏事件纠正）。自带 App 会话
        注入 ``set_idle_frame_predicate``，不受影响；独立 ``render()`` 会话
        改为安全默认（多渲染一帧，不冻结）。
        """
        predicate = getattr(self, "_idle_frame_predicate", None)
        if predicate is None:
            return False
        try:
            return bool(predicate())
        except Exception:
            _logger.debug("空闲预测函数异常（本帧不复用）", exc_info=True)
            return False

    def _should_reuse_idle_frame(self, static_now: bool | None = None) -> bool:
        """本拍是否复用上一帧（空闲帧复用——60Hz 节拍不变，省空转重建开销）。

        全部条件满足才复用（任一不满足 → 正常重建，零行为变化）：
          1. 宿主空闲预测（``set_idle_frame_predicate``）返回 True；未注入时
             默认动态（安全：每帧重建）；
          2. 已有上一帧（``_last_frame``）且该帧**也是在静态态下构建的**
             （``_last_frame_idle_static``）；
          3. 无待处理命令（``_cmd_queue`` 为空——命令应用会改模型且置脏）；
          4. 非脏（``_dirty``，输入/命令/系统统计等变更标记）；
          5. 无 resize 挂起、无底部重绘请求；
          6. 无激活的 ``useAnimation`` 动画（动画需逐帧推进）。

        ★ 2026-10-21（Ctrl+H 打开轨迹视图后 Esc 关闭界面不刷新修复）：条件 2
        的「上一帧也是静态态构建」是新增强制条件。静态预测只描述**当前**
        状态，不能证明上一帧内容与当前状态一致：关闭模态全屏视图（Ctrl+H
        打开的轨迹视图按 Esc）后 ``model.fullscreen`` 由非空变空，预测在同
        一拍由 False 翻为 True——修复前本方法只看当前预测，直接复用上一帧
        （仍是全屏视图内容）→ 屏幕不刷新，且静态态下不再有脏事件，界面会
        一直停在已关闭的视图上（直到系统统计数值变化等偶发置脏才补渲）。
        新增条件后，离开活跃态的第一拍强制重建（渲染当前状态的正常界面），
        下一拍起恢复正常复用——性能优化只在「连续静态」时生效。

        Args:
            static_now: 本拍预测结果（帧执行器已求值时传入，避免重复求值）；
                None 时内部调用 ``_idle_static_now``（测试/独立调用兼容）。
        """
        if getattr(self, "_last_frame", None) is None:
            return False
        if static_now is None:
            static_now = self._idle_static_now()
        if not static_now:
            return False
        # 上一帧不是静态态内容 → 必须先重建一帧（静态预测可能刚由假翻真）
        if not getattr(self, "_last_frame_idle_static", False):
            return False
        # ★ 浮窗定时刷新到期（2026-10-21 渲染性能优化）：带 ``refresh_interval``
        #   的浮窗（如 ``/loop`` 计时窗）允许空闲复用，但到期必须重建一帧
        #   ——否则计时/进度条冻结在上一帧内容上。
        if self._float_refresh_due():
            return False
        # ★ 2026-10-23（全屏视图定时刷新）：声明 refresh_interval 的视图
        #   （如 TraceView 每秒更新实时耗时）到期必须重建一帧。
        if self._view_refresh_due():
            return False
        # 本拍有变更（输入/命令/请求重绘，由 ``_should_render`` 落位）→ 重建
        if not getattr(self, "_idle_ok", False):
            return False
        if getattr(self, "_dirty", False) or getattr(self, "_resize_pending", False):
            return False
        try:
            if self._cmd_queue is not None and not self._cmd_queue.empty():
                return False
        except Exception:
            return False
        redraw = getattr(self, "_bottom_redraw_requested", None)
        if redraw is not None and redraw.is_set():
            return False
        try:
            if has_active_animations(ctx=getattr(self, "_hook_ctx", None)):
                return False
        except Exception:
            _logger.debug("has_active_animations 探测异常", exc_info=True)
            return False
        return True

    def _view_refresh_due(self) -> bool:
        """当前模态视图的「定时刷新」是否到期（2026-10-23 全屏视图脏驱动）。

        视图经 ``ViewSpec.refresh_interval`` 声明（``view_registry._refresh``
        挂到组件 ``_ink_refresh_interval``）——空闲复用期间到期强制重建一帧，
        兼顾「空闲不每帧重建」与「实时耗时/计时每秒更新」。
        """
        model = getattr(self, "_model", None)
        if model is None:
            return False
        try:
            from src.tui.app.app import BOTTOM_VIEWS, FULLSCREEN_VIEWS
        except Exception:
            return False
        view = None
        fullscreen_id = getattr(model, "fullscreen", "") or ""
        if fullscreen_id:
            view = FULLSCREEN_VIEWS.get(fullscreen_id)
        else:
            bottom_id = getattr(model, "bottom_view", "") or ""
            if bottom_id:
                entry = BOTTOM_VIEWS.get(bottom_id)
                view = entry[0] if isinstance(entry, tuple) else entry
        if view is None:
            return False
        raw = getattr(view, "_ink_refresh_interval", None)
        if raw is None:
            return False
        try:
            interval = float(raw)
        except (TypeError, ValueError, OverflowError):
            return False
        if not (interval > 0 and interval != float("inf")):
            return False
        try:
            elapsed = time.monotonic() - float(
                getattr(self, "_last_frame_built_at", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            return True
        return elapsed >= interval

    def _float_refresh_due(self) -> bool:
        """浮窗「定时刷新」是否到期（到期 → 本拍必须重建，禁止复用上一帧）。

        ★ 2026-10-21 渲染性能优化（用户需求：优化有浮动窗口时的渲染性能）：
        浮窗内容若依赖 ``time.monotonic()``（计时器 / 进度动画），此前空闲
        预测只能对**所有非 ``idle_static`` 窗口**返回「非静态」——等于有浮窗
        就永久按 60Hz 全量重建整个界面（组件树 + 调和 + 布局 + 绘制 + 合成）。
        窗口声明 ``FloatingWindowSpec.refresh_interval`` 后：空闲态允许帧复用，
        本方法按**最短间隔**判定到期，到期那一拍强制重建一帧（内容更新粒度即
        该间隔）。未声明的窗口（含 ``/loop`` 循环浮窗——用户确认保持每帧刷新）
        仍走「有浮窗即每帧重建」，本方法不额外约束。

        判定口径：
          - 无浮窗 / 无 ``refresh_interval`` 窗口 → False（不额外约束）；
          - 多个窗口取**最小**间隔（最频繁的那个决定刷新率）；
          - ``_last_frame_built_at`` 为最近一次**实际重建**时刻（复用帧不更新
            ——否则刷新永远不到期）；
          - 非法/非有限/非正数间隔忽略（与 ``_window_blocks_idle`` 同口径）。
        """
        model = getattr(self, "_model", None)
        windows = getattr(model, "floating_windows", None) or []
        if not windows:
            return False
        interval: float | None = None
        for window in windows:
            raw = getattr(window, "refresh_interval", None)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            interval = value if interval is None else min(interval, value)
        if interval is None:
            return False
        try:
            elapsed = time.monotonic() - float(
                getattr(self, "_last_frame_built_at", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            return True  # 时间基准异常 → 保守重建
        return elapsed >= interval

    def _render_frame_impl(self) -> None:
        # ★ 2026-10-23（game_loop 单次调用）：清屏请求在帧内统一消费——
        #   模型 reset_display + 终端 full_clear 都发生在 render 线程本帧，
        #   外部线程只置 ``_clear_pending``（见 InkSession.clear_screen）。
        # ★ 2026-10-23（流式高度锁定）：清屏/resize 会重建文档坐标系，
        #   必须重置高度锁定高水位（否则下一帧按旧高水位补出大段空白）。
        lock_reset = bool(getattr(self, "_clear_pending", False))
        apply_clear = getattr(self, "_apply_pending_clear", None)
        if callable(apply_clear):
            apply_clear()
        # ★ 2026-10-23（/loop 后流式抖动修复）：本帧补白是否已在帧构建期
        #   （``render_frame`` 的 ``stream_pad`` 钩子）完成——完成时不再执行
        #   帧后补白（否则重复补白两次）。每帧复位，仅由钩子置位。
        self._stream_pad_handled = False
        if self._build_tree is None:
            return
        # 终端尺寸变化标志（下方内容行基线同步用；模型为空时保持 False）
        size_changed = False
        # ★ useAnimation 共享动画驱动：每帧推进一次 tick 并通知订阅组件
        #   （多个动画组件合并为一轮渲染，React Ink v7 语义）——显式传本会话
        #   上下文（多会话下各推进各自的驱动）。
        try:
            advance_animation(ctx=getattr(self, "_hook_ctx", None))
        except Exception:
            _logger.debug("advance_animation 异常", exc_info=True)
        # ★ SIGWINCH 兜底（2026-10-05）：渲染帧主动重探终端尺寸——部分终端/
        #   平台 resize 不发送 SIGWINCH，仅靠 TTL 缓存刷新会使宽度长时间陈旧
        #   （工具卡/布局不跟随窗口）。尺寸变化经下方 ``width !=
        #   _last_render_width`` 分支触发 reflow + 全量重绘。
        self._poll_terminal_size()
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
            # ★ 2026-10-09（用户需求：运行中的工具实时刷新运行时间）：运行中
            #   工具卡的标题行 = 实时运行时间（替代 ●）。未增量提交的卡由
            #   ``ToolCard`` 组件每帧渲染自动刷新；**已增量提交**（标题行进入
            #   committed_lines）的卡由宿主主动刷新该静态行——否则时间冻结在
            #   提交时刻。宿主无此方法（桩模型）时跳过。
            refresh_titles = getattr(
                self._model, "refresh_running_tool_titles", None,
            )
            if refresh_titles is not None:
                try:
                    refresh_titles()
                except Exception:
                    _logger.debug("refresh_running_tool_titles 异常", exc_info=True)
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
                # ★ P3（review 2026-08-18）：同步向 InkRenderer 传播宽度
                #   （set_width）——place_cursor 列上限防御钳制需要终端宽度
                #   （与下方 set_height 传播同模式；幂等，宽度未变不触发）。
                try:
                    self._ink_renderer.set_width(width)
                except Exception:
                    _logger.debug("InkRenderer set_width 传播异常", exc_info=True)
                self._last_render_width = width
                # ★ 2026-10-20：首帧只记录尺寸基准，不当作 resize（否则
                #   ``reset(full=True)`` 会清屏并关掉起始行锚定——见
                #   ``InkSession._first_frame_pending`` 注释）。
                if not getattr(self, "_first_frame_pending", False):
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
                # ★ 2026-10-20：首帧不视为 resize（同上，见
                #   ``InkSession._first_frame_pending``）。
                if not getattr(self, "_first_frame_pending", False):
                    self._resize_pending = True
                height_changed = True
            size_changed = width_changed or height_changed
            if width_changed or height_changed:
                # ★ React Ink useWindowSize（方向 E）+ P3-19（review 方向）：
                #   宽度/高度任一变化都通知订阅组件重渲染——修复前仅在宽度
                #   分支调用 ``_notify_window_size()``：高度单独变化（resize
                #   只改高度）时 useWindowSize 订阅者不重渲染，窗口尺寸状态
                #   陈旧（useWindowSize 返回 columns/rows 双值，rows 变化须
                #   通知）。宽高同时变化时合并为一次通知（版本只递增一次，
                #   订阅者单次重渲染，避免双帧重绘）。
                try:
                    _hooks._notify_window_size(ctx=getattr(self, "_hook_ctx", None))
                except Exception:
                    _logger.debug("notify_window_size 异常", exc_info=True)
        # ★ 方向3（resize 全量刷新消费）：尺寸变化后本帧即全量重建（不等待
        #   下一帧 diff）——重置渲染器 prev（full=True），使 render() 走全量
        #   写入路径（并在写入前清屏，清掉旧尺寸帧的残留）。仅 resize 使用
        #   full=True；其余路径均走增量 diff。
        if getattr(self, "_resize_pending", False):
            self._resize_pending = False
            # ★ 2026-10-23（流式高度锁定）：resize/清屏重建文档坐标系——
            #   重置高水位，避免按旧高度补出错误空白。
            lock_reset = True
            self._ink_renderer.reset(full=True)
            # ★ 2026-10-20（resize 布局不跟随修复）：本帧禁止空闲帧复用。
            #   修复前 ``_resize_pending`` 在此即被清除，下方
            #   ``_should_reuse_idle_frame`` 看不到 resize 标志；静态状态
            #   （``_idle_ok`` 为 True）下复用上一帧（**旧尺寸布局**）→ 窗口
            #   尺寸变化后 TUI 仍按旧宽度渲染（右侧空白/行宽错误），直到下一次
            #   脏事件（系统统计 ≤2s）才重建。置 ``_idle_ok=False`` 强制本帧
            #   按新尺寸重建组件树。
            self._idle_ok = False
        # ★ 2026-10-20：首帧尺寸基准同步完成——本帧是首次渲染，尺寸已记录为
        #   基准（见上方 width/height 分支），此后尺寸变化按真实 resize 处理
        #   （``reset(full=True)`` + 清屏重写，resize 残留修复契约不变）。
        if getattr(self, "_first_frame_pending", False):
            self._first_frame_pending = False
        # ★ 空闲帧复用（2026-10-07 性能优化，用户需求）：宿主声明的「静态
        #   状态」（``set_idle_frame_predicate``）下跳过组件树重建/调和/布局/
        #   绘制，直接复用上一帧——**帧号照常推进（对外仍恒定 60Hz）**，仅省
        #   空转 CPU（无变化帧的重建开销 ≈ 1ms/帧）。判定条件全部满足才复用：
        #   预测函数返回 True + 上一帧也是静态态构建 + 无待处理命令 + 非脏 +
        #   无 resize + 无重绘请求 + 无激活动画（``useAnimation``）。未注入
        #   预测函数时恒不复用（默认行为与既有 60Hz 全量渲染完全一致）。
        # ★ 本拍静态预测只求值一次：复用判定与「上一帧静态态」标记共用同一
        #   结果（见 ``_should_reuse_idle_frame`` 的 2026-10-21 修复说明）。
        #   复用路径保持标记不变（复用帧本身即静态态构建的帧）；重建路径写
        #   入本拍结果——离开活跃态（视图关闭）时预测刚为 True 而标记仍是
        #   False → 本拍强制重建，重绘当前状态的界面。
        static_now = self._idle_static_now()
        frame: Any = None
        #: 本帧是否实际重建（True 时才应用高度锁定；复用帧已含上一帧补白）
        built_frame = False
        if self._should_reuse_idle_frame(static_now) and self._last_frame is not None:
            frame = self._last_frame
        else:
            built_frame = True
            # ★ P1 修复（layout effect 更新时序）：effects 在 reconciler.render
            #   内提交，其中的 set_state 只排队到下一帧；但绘制发生在
            #   reconciler.render 之后（render_frame），因此 effect 更新不会
            #   反映到本帧（useMeasure/useBoxMetrics 首帧 0x0、状态切换闪烁）。
            #   React 语义要求 layout effect 的更新在绘制前 flush。
            # ★ 2026-10-23（game_loop 单次调用）：组件树只构建一次，
            #   pre-paint flush 由 ``reconciler.render`` 在**同一次调用内部**
            #   完成（重跑提交阶段，最多 ``_MAX_PRE_PAINT_PASSES`` 轮）——不再
            #   由本方法循环重建组件树。返回值表示达到上限仍有待 flush 更新，
            #   此时保留脏标记到下一拍（不静默丢弃）。
            element = self._build_tree(self._model, width)
            pending_update = self._reconciler.render(
                self._root_fiber, element, width,
                self._width_cache.get_height(),
            )
            self._dirty = bool(pending_update)
            if pending_update:
                from .reconciler import _MAX_PRE_PAINT_PASSES

                # ★ 2026-10-23（日志刷屏修复）：组件在 layout effect 中每帧
                #   setState 时原实现每帧一条 warning（60 条/秒）。限频 5s。
                now = time.monotonic()
                last = float(getattr(self, "_last_prepaint_warn", 0.0) or 0.0)
                if now - last >= 5.0:
                    self._last_prepaint_warn = now
                    _logger.warning(
                        "layout effect 更新超过 %d 轮，剩余更新延后到下一帧",
                        _MAX_PRE_PAINT_PASSES,
                    )
            # ★ 浮窗定位已收敛到 ``render_frame`` 内部（2026-10-21 统一入口）：
            #   此前只在本分支调用 ``apply_floating_layout``——``renderToString``
            #   / ``render()`` 等其它帧构建路径渲染 App 树时浮窗不定位（粘在
            #   文档左上角）。现在所有路径共用同一套定位语义；这里额外传
            #   **视口高度**，把绝对坐标 / 文档锚点浮窗钳进可视区（渲染器只
            #   重写可见行，否则长文档中位于可视区上方的窗口永不显示）。
            try:
                viewport_height = _safe_int(self._width_cache.get_height(), 0)
            except Exception:  # pragma: no cover - 尺寸缓存异常按未知视口处理
                viewport_height = 0
            frame = _components.render_frame(
                self._root_fiber, width, viewport_height=viewport_height,
                stream_pad=self._make_stream_pad_callback(reset=lock_reset),
            )
            self._last_frame = frame
            self._last_frame_idle_static = static_now
            # ★ 浮窗定时刷新基准（2026-10-21）：记录**实际重建**时刻——
            #   复用帧不更新该时间（否则 ``_float_refresh_due`` 永远不到期，
            #   计时浮窗会冻结）。
            self._last_frame_built_at = time.monotonic()
        # ★ 2026-10-23（流式高度锁定）：实际重建帧后按高水位补白（下屏之前），
        #   防止流式期间解析进度行消失 / Markdown 预览重排等**合法高度缩短**
        #   让上方内容整体跳动一行。复用帧已含上一帧补白，不重复处理。
        #   ★ 2026-10-23（/loop 后流式抖动修复）：存在底部锚定浮窗时补白已在
        #   帧构建期完成（``_make_stream_pad_callback`` → ``render_frame``），
        #   这里跳过帧后补白，避免浮窗与下屏被补白两次而错位。
        if built_frame and not getattr(self, "_stream_pad_handled", False):
            frame = self._apply_stream_height_lock(frame, reset=lock_reset)
            self._last_frame = frame
        # ★ 输出历史接线（committed 内容行数）：InkRenderer 只回调 committed
        #   区的**新增内容行**（修复前用文档末尾行区间推断 → 状态栏/输入区/
        #   时间线被反复写入输出历史且真正内容行漏记）。committed_lines 为
        #   卡片行（角色头 + 正文 + 尾空行），自文档第 1 行（TopHeader 之后）
        #   连续排布；未注入时渲染器不产生任何回调（安全）。
        # ★ 内容区起始行显式注入（架构修复）：修复前渲染器硬编码
        #   ``_CONTENT_LINE_OFFSET``（假设 App 树第 0 行恒为 TopHeader）——
        #   此处从 committed host fiber 的 ``layout_box.y`` 读取真实起始行
        #   （``_find_committed_chat`` 结果已由 ``render_frame`` 缓存于 root，
        #   O(1)）；App 顶部增删元素后输出历史不再错位。
        committed_fiber = _components._find_committed_chat(self._root_fiber)
        content_start = None
        if committed_fiber is not None and committed_fiber.layout_box is not None:
            content_start = max(0, committed_fiber.layout_box.y)
        self._ink_renderer.set_content_line_count(
            len(getattr(self._model, "committed_lines", None) or []),
            start=content_start,
            resync=size_changed,
        )
        # ★ 2026-10-23（增长闪烁修复）：注入文档末尾固定区行数——渲染器
        #   增长时把滚动区域限制在内容区（DECSTBM），固定区不随滚动上移、
        #   无需每帧写回，消除「增长时底栏上下跳一下」的闪。
        #   ★ 2026-10-23（满屏流式浮窗闪烁修复）：固定区不再只是下屏
        #   （状态栏 + 输入区），还包括可见的**底部锚定浮窗**（/loop、
        #   /usage、/pet）——满屏流式增长时浮窗原本随内容一起上滚再被重写，
        #   逐字节渲染的终端（mintty）上表现为浮窗跳一下/闪一下。固定区
        #   = 下屏顶行与全部底部锚定浮窗最上沿的最小值到文档末尾。
        #   无下屏（全屏视图）注入 0 → 回退整屏滚动（既有行为）。
        bottom_reserved = self._fixed_bottom_rows(frame.height)
        try:
            self._ink_renderer.set_bottom_reserved(bottom_reserved)
        except Exception:
            _logger.debug("注入固定区行数异常", exc_info=True)
        self._ink_renderer.render(frame)
        # ★ render({onRender})：每帧渲染后回调（官方 RenderMetrics 语义）——
        #   回调异常仅记录日志，不中断渲染循环。
        if self._on_render_callback is not None:
            try:
                self._on_render_callback({
                    "width": width,
                    "height": len(frame.lines),
                })
            except Exception:
                _logger.debug("onRender 回调异常", exc_info=True)
        # ★ render() debug 选项：记录最近帧行数（session._debug_log_frame 统计）
        self._last_frame_lines = len(frame.lines)
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
        # ★ 帧完成通知（2026-08-19，editmsg「很多上文时按回车不能编辑对应
        #   消息」根因修复）：本帧 input router 已发布（reconciler.render 内
        #   _publish_input_router → session._on_input_router → dispatcher
        #   接线）——唤醒 flush_input_router 等待者（弹窗清理方同步等待新
        #   router，防旧 router 吞掉用户后续 Enter）。
        advance = getattr(self, "_advance_frame_seq", None)
        if callable(advance):
            try:
                advance()
            except Exception:
                _logger.debug("_advance_frame_seq 异常", exc_info=True)

    # ── 流式高度锁定（防内容整体跳行） ───────────────

    def _make_stream_pad_callback(self, *, reset: bool):
        """构造「帧构建期补白」钩子（2026-10-23 ``/loop`` 后流式抖动修复）。

        背景：``_apply_stream_height_lock`` 是**帧后**补白——把空行插在下屏
        之前。悬窗打开时该路径被整体禁用（补白会让浮窗相对下屏上下跳动）：
        禁用后流式期间文档高度瞬时缩短（解析进度行消失 / Markdown 预览
        重排）会让内容整体上下移动一行，``/loop`` 浮窗打开时用户看到的就是
        「抖得很夸张」。

        本钩子把补白提前到 ``render_frame`` 内（布局之后、绘制之前）：
        ``render_frame`` 以返回的 pad 增高文档总高，本方法负责把**下屏与
        底部锚定浮窗**的布局盒一起下移 pad 行。补白行因此同时位于三者
        之前，内容行号不变、浮窗/下屏行号 +pad 且文档总高 +pad——三者在
        终端上的可见位置完全不变。

        ★ 2026-10-23（浮窗抖动修复）：本钩子内的平移只是粗调——浮窗的
        可见高度/顶部裁剪/运行时矩形都依赖**文档总高**，补白后必须由
        ``render_frame`` 用含补白的总高重新解析一次（见该函数内
        ``doc_height=total_h`` 的调用），否则补白帧窗口顶部会被多裁一行。

        启用条件（任一不满足 → 返回 None，走既有帧后补白路径）：
          - 本帧存在浮窗（``model.floating_windows`` 非空）；
          - 锁定器可用。

        兼容性：仅 ``bottom_area`` / ``input_*`` 锚点的浮窗可与补白协同；
        存在 ``doc_bottom`` / ``doc_top`` / 绝对坐标浮窗时**禁用锁定**
        （这些浮窗的位置语义依赖文档高度，补白会改变其相对位置）。

        Args:
            reset: 是否重置高水位（清屏/resize 重建文档坐标系）。

        Returns:
            回调 ``(root_fiber, total_h) -> pad``；无需构建期补白时 None。
        """
        lock = getattr(self, "_height_lock", None)
        model = self._model
        windows = getattr(model, "floating_windows", None) if model is not None else None
        if lock is None or model is None or not windows:
            return None

        def _resolve(root_fiber, total_h: int) -> int:
            # 标记本帧补白已由构建期处理（``_render_frame_impl`` 据此跳过
            # 帧后 ``_apply_stream_height_lock``）。
            self._stream_pad_handled = True
            if reset:
                lock.reset()
            bottom = self._find_bottom_area_fiber()
            box = getattr(bottom, "layout_box", None)
            bottom_y = int(getattr(box, "y", 0) or 0) if box is not None else 0
            # 本帧浮窗列表由 ``apply_floating_layout`` 写入同帧缓存（紧随
            # ``_apply_float_positions`` 之后调用，不会陈旧）。
            from ._floating import _anchor_kind, _collect_float_fibers

            fibers = getattr(root_fiber, "_float_frame_windows", None)
            if fibers is None:
                fibers = _collect_float_fibers(root_fiber, include_hidden=True)

            compatible: list = []
            disabled = (
                bottom is None
                or bottom_y <= 0
                or int(getattr(self._ink_renderer, "_height", 0) or 0) <= 0
            )
            for fiber in fibers or ():
                if getattr(fiber, "_float_hidden", False):
                    continue
                if int(getattr(fiber, "_float_visible_h", 0) or 0) <= 0:
                    continue
                props = getattr(fiber, "props", None)
                if not isinstance(props, dict) or _anchor_kind(props) not in (
                    "bottom_area", "input",
                ):
                    # 文档锚点/绝对坐标浮窗：补白会改变其相对位置 → 保持
                    # 既有语义（禁用锁定），不在本帧补白。
                    disabled = True
                    break
                compatible.append(fiber)
            pad = lock.update(
                active=self._stream_height_active(),
                height=int(total_h),
                bottom_y=bottom_y,
                disabled=disabled,
            )
            self._frame_bottom_pad = pad
            if pad <= 0 or bottom is None:
                return 0
            # 下屏与底锚浮窗一起下移：补白行落在它们之前，三者在屏幕上的
            # 位置都不变（内容行号不变 + 文档总高 +pad）。
            _stream_height.shift_subtree_y(bottom, pad)
            for fiber in compatible:
                _stream_height.shift_float_for_pad(fiber, pad)
            return pad

        return _resolve

    def _stream_height_active(self) -> bool:
        """当前是否处于需要「高度锁定」的活跃期。

        与空闲帧复用的静态预测口径一致（流式输出/工具执行/参数解析中），
        任一为真即视为活跃：``status.status_active``（模型阶段）、
        ``model.tool_boxes``（工具卡运行）、``model.parse_line``（解析进度行）。

        Returns:
            True — 活跃（文档高度变化应被锁定）；False — 空闲。
        """
        model = self._model
        if model is None:
            return False
        status = getattr(model, "status", None)
        if status is not None and getattr(status, "status_active", False):
            return True
        if getattr(model, "tool_boxes", None):
            return True
        return getattr(model, "parse_line", None) is not None

    def _find_bottom_area_fiber(self):
        """查找（并缓存）下屏 fiber（高度锁定补白锚点）。

        下屏是 App 根组件中包裹状态栏 + 输入区的 Box，带
        ``dataFloatAnchor="bottom_area"``。fiber 跨帧由调和器复用；缓存
        失效条件：被删除 / props 标记变化（如模态底部视图替换）→ 重新遍历。

        Returns:
            下屏 host fiber；未找到返回 None。
        """
        fiber = getattr(self, "_bottom_area_fiber", None)
        if (
            fiber is not None
            and not getattr(fiber, "deleted", False)
            and isinstance(getattr(fiber, "props", None), dict)
            and fiber.props.get("dataFloatAnchor") == "bottom_area"
        ):
            return fiber
        fiber = _stream_height.find_bottom_area(self._root_fiber)
        self._bottom_area_fiber = fiber
        return fiber

    def _fixed_bottom_rows(self, doc_height: int) -> int:
        """文档末尾固定区行数（下屏 + 可见的底部锚定浮窗）。

        ★ 2026-10-23（满屏流式浮窗闪烁修复）：渲染器把增长/缩短的滚动区域
        限制在「内容区」、固定区原地不动。固定区此前只含下屏（状态栏 +
        输入区）；满屏流式输出时底部锚定浮窗（``/loop``、``/usage``、
        ``/pet``）位于内容区内，会随内容一起上滚、随后再被重写——终端
        （mintty 等逐字节渲染）会显示这个中间态，用户看到「浮动窗口闪」。
        把浮窗并入固定区后，滚动只作用于浮窗上方的内容，浮窗钉在屏幕原位。

        固定区起点 = ``min(下屏顶行, 全部可见底部锚定浮窗的最上沿)``：多个
        浮窗可叠放（``/loop`` + ``/usage``），取最高的那个；浮窗可能带
        ``offset_y`` / ``max_height``，其布局盒已是本帧最终行号（布局后
        定位 + 补白平移均已完成）。

        保守回退：存在与固定区相交的**非**底部锚定浮窗（``doc_bottom`` /
        ``doc_top`` / 绝对坐标）时只固定下屏——这些窗口的位置随文档高度
        变化，冻结其所在行会让它们停止滚动而错位。浮窗行数变化（隐藏 /
        高度变化）由渲染器 ``_reserved_bottom_for_scroll`` 判为不连续并
        回退整屏滚动（正确性优先）。

        Args:
            doc_height: 本帧文档总行数。

        Returns:
            固定区行数（0 = 不固定，回退整屏滚动）。
        """
        bottom = self._find_bottom_area_fiber()
        box = getattr(bottom, "layout_box", None)
        if box is None:
            return 0
        try:
            bottom_y = int(getattr(box, "y", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
        if bottom_y < 0 or bottom_y > doc_height:
            return 0
        bottom_rows = doc_height - bottom_y
        model = getattr(self, "_model", None)
        if model is None or not getattr(model, "floating_windows", None):
            return bottom_rows
        fibers = getattr(self._root_fiber, "_float_frame_windows", None) or ()
        if not fibers:
            return bottom_rows
        # 局部导入：``_floating`` 只依赖 ink 内部模块（避免模块级循环导入）。
        from ._floating import _anchor_kind

        float_top: int | None = None
        others: list[tuple[int, int]] = []
        for fiber in fibers:
            if getattr(fiber, "_float_hidden", False):
                continue
            try:
                visible_h = int(getattr(fiber, "_float_visible_h", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                visible_h = 0
            if visible_h <= 0:
                continue
            fbox = getattr(fiber, "layout_box", None)
            props = getattr(fiber, "props", None)
            if fbox is None or not isinstance(props, dict):
                continue
            try:
                y0 = int(getattr(fbox, "y", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if _anchor_kind(props) in ("bottom_area", "input"):
                float_top = y0 if float_top is None else min(float_top, y0)
            else:
                others.append((y0, y0 + visible_h))
        if float_top is None:
            return bottom_rows
        fixed_top = max(0, min(bottom_y, float_top))
        for y0, y1 in others:
            # 非固定浮窗与固定区相交：冻结会让它停止滚动 → 只固定下屏。
            if y0 < doc_height and y1 > fixed_top:
                return bottom_rows
        return doc_height - fixed_top

    def _apply_stream_height_lock(self, frame, *, reset: bool = False):
        """应用流式高度锁定：必要时在**下屏之前**补空行，返回（可能新的）帧。

        设计见 ``_stream_height`` 模块 docstring。关键点：

          - 只在实际**重建帧**后调用（空闲复用帧已含上一帧的补白，不重复补）；
          - 补白插在下屏 fiber 的 ``layout_box.y`` 之前，同时把下屏子树
            layout_box.y 下移同样的行数——``_position_cursor`` 读输入区
           布局盒定位光标，必须与补白后的物理位置一致；
          - 浮窗 / 全屏视图 / 模态底部视图 / 未知屏幕高度不锁定（补白会
            破坏浮窗锚点/模态布局）；
          - 清屏 / resize 时重置高水位（``reset=True``）。

        Args:
            frame: 本帧（实际重建的帧）。
            reset: True 时先释放锁定（清屏/resize）。

        Returns:
            补白后的新 Frame（无需补白时原样返回）。
        """
        lock = getattr(self, "_height_lock", None)
        if lock is None or frame is None:
            return frame
        if reset:
            lock.reset()
        model = self._model
        if model is None:
            return frame
        disabled = bool(
            getattr(model, "fullscreen", "")
            or getattr(model, "bottom_view", "")
            or getattr(model, "floating_windows", None)
            or getattr(frame, "base_lines", None) is not None
            or int(getattr(self._ink_renderer, "_height", 0) or 0) <= 0
        )
        bottom = None if disabled else self._find_bottom_area_fiber()
        bottom_y = 0
        box = getattr(bottom, "layout_box", None)
        if box is not None:
            bottom_y = int(getattr(box, "y", 0) or 0)
        pad = lock.update(
            active=self._stream_height_active(),
            height=int(frame.height),
            bottom_y=bottom_y,
            disabled=disabled,
        )
        self._frame_bottom_pad = pad
        if pad <= 0 or bottom is None:
            return frame
        _stream_height.shift_subtree_y(bottom, pad)
        return _stream_height.pad_frame(frame, bottom_y, pad)

    # ── 系统监控 ─────────────────────────────────────

    def _update_system_stats(self) -> None:
        """每 2 秒采集 CPU/MEM 写入模型并标记脏（输入区顶部分隔线显示）。

        空闲时也每 2 秒渲染一次（仅更新该值），CPU 开销可忽略。
        """
        now = time.monotonic()
        if now - self._last_sys_stats_time < self._sys_stats_interval:
            return
        # ★ P3（review）：先判 model/status 再刷新时间戳——修复前先刷新
        #   ``_last_sys_stats_time`` 再判 status，无 status 的模型每 2s 均会
        #   进入采集路径一次（空转）；顺序调整后无 status 时零开销早退。
        if self._model is None:
            return
        status = getattr(self._model, "status", None)
        if status is None:
            return  # 测试桩模型无 status
        self._last_sys_stats_time = now
        if self._system_monitor is None:
            from src.tui._system_monitor import _SystemMonitor
            self._system_monitor = _SystemMonitor()
        # ★ 2026-10-23（渲染线程阻塞修复）：系统监控改为**后台线程**采集，
        #   帧内只读缓存——无 psutil 平台的子进程采样（iostat/powershell/
        #   sysctl，timeout=5s）不再阻塞 60Hz 渲染线程/输入处理。
        try:
            self._system_monitor.start(self._sys_stats_interval)
        except Exception:
            _logger.debug("系统监控后台线程启动异常", exc_info=True)
        try:
            cpu, mem = self._system_monitor.get_cpu_and_mem()
        except Exception:
            _logger.debug("系统监控缓存读取异常", exc_info=True)
            return
        # ★ P2-5（review 方向）：int() 转换纳入防御——``get_cpu_and_mem()``
        #   异常时返回 0.0，但平台差异/子进程解析可能返回非数字（如 "N/A"），
        #   直接 ``int()`` 抛 ValueError 使渲染线程崩溃（_drain_queue 每帧
        #   调用本方法）。``_safe_int`` 转换失败回退 0，不中断渲染循环。
        cpu_i = _safe_int(cpu)
        mem_i = _safe_int(mem)
        # ★ P2（review 2026-08-19）：status.cpu/mem 比较与赋值纳入防御——
        #   status 存在但缺 cpu/mem 字段（测试桩/未来模型变更）时
        #   AttributeError 传播致渲染线程崩溃恢复，与本文件其它模型访问
        #   （reflow/set_width）全部 try 保护的风格一致。
        try:
            if cpu_i != status.cpu or mem_i != status.mem:
                status.cpu = cpu_i
                status.mem = mem_i
                self._dirty = True  # 触发渲染显示新值
        except AttributeError:
            _logger.debug("status 缺少 cpu/mem 字段，跳过系统监控更新", exc_info=True)

    # ── 光标 ─────────────────────────────────────────

    def _position_cursor(self) -> None:
        """渲染后定位输入光标（从文档底部相对移动），有输入区才显示光标。

        方向B（2026-08-05）：布局/坐标计算委托 ``_cursor.position_cursor``
        （纯函数模块）；本方法只负责 fiber 获取与异常兜底。

        ★ 2026-08-17（用户需求：轨迹 Trace 不显示光标；2026-08-17 通用化）：
        **模态全屏视图**（``model.fullscreen`` 非空——App 整屏渲染全屏视图
        组件、无输入区）下 ``find_input_fiber`` 返回 None → **隐藏终端光标**
        （避免光标停留在残留位置闪烁）；正常模式（找到 input fiber）→ 显示
        光标并定位。显隐经 ``InkRenderer.set_cursor_visible`` 状态跟踪（仅
        变化时写 DECTCEM 序列，不变帧零输出——每帧调用开销可忽略）。

        ★ 2026-10-22（用户需求：TUI 渲染时先隐藏光标，渲染完有输入界面才
        移到对应界面再显示、没有输入界面不显示）：渲染器在**写帧前**已统一
        隐藏光标（``InkRenderer._hide_cursor_for_write``）。本方法负责收口：
          1. 找到输入区 fiber → **先** ``place_cursor`` 定位到输入光标位置，
             **定位成功后**才 ``set_cursor_visible(True)``——修复前先显示再
             定位，光标会短暂出现在上一帧位置；
          2. 找不到输入区（模态全屏视图）→ 保持隐藏（不再显示）；
          3. 找到 fiber 但定位未发生（``_cursor.position_cursor`` 返回 False：
             无布局盒）或定位抛异常 → **保持隐藏**，等下一帧定位成功再显示
             （不把光标停在旧位置）。
        """
        if self._model is None:
            return
        # ★ P5：优先复用缓存的 input-area fiber（_render_frame 已保证其有效；
        #   None 时回退全树查找——如测试直接构造 root 的场景）
        fiber = self._input_fiber
        if fiber is None:
            fiber = _cursor.find_input_fiber(self._root_fiber)
        if fiber is None:
            try:
                self._ink_renderer.set_cursor_visible(False)
            except Exception:
                _logger.debug("hide_cursor 异常", exc_info=True)
            return
        placed = False
        try:
            placed = _cursor.position_cursor(
                self._ink_renderer, self._width_cache.get_width(), fiber,
            )
        except Exception:
            _logger.debug("place_cursor 异常", exc_info=True)
        if not placed:
            # 光标位置未知（无布局盒/定位异常）→ 保持隐藏（渲染器写帧前已隐藏；
            # 无写入帧时状态跟踪保证不重复写），下一帧定位成功后再显示。
            return
        try:
            self._ink_renderer.set_cursor_visible(True)
        except Exception:
            _logger.debug("show_cursor 异常", exc_info=True)

    def _find_input_fiber(self, root_fiber):
        """在 host 树中查找输入区 fiber（委托 ``_cursor.find_input_fiber``）。

        ★ 标准 React Ink 组件化：InputArea 标准组件返回 Column（props 含
        ``dataInputArea=True`` 标记 + 透传输入区状态）——查找条件为
        ``props.dataInputArea`` 或旧 ``type == "input-area"``（兼容）。
        """
        return _cursor.find_input_fiber(root_fiber)


__all__ = ["_SessionFrameMixin", "_safe_int", "_SIZE_POLL_INTERVAL"]
