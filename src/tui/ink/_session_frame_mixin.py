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

import logging
import time

from src.tui._const import RenderCommand
from src.tui.ink._cmd_priority import _get_cmd_id, _cmd_name
from src.tui.ink import components as _components
from src.tui.ink import hooks as _hooks
from src.tui.ink import _cursor
from src.tui.ink._animation import advance_animation, has_active_animations

_logger = logging.getLogger(__name__)

#: 终端尺寸主动轮询间隔（秒）——SIGWINCH 兜底（2026-10-05）：部分终端/平台
#: 窗口 resize **不发送 SIGWINCH**（Cygwin pty 实测 winsize 变化不触发信号），
#: ``TerminalWidthCache`` 默认 TTL 60s 内宽度陈旧——工具卡/布局宽度不随窗口
#: 变化。渲染帧每帧经 ``_poll_terminal_size`` 按本间隔主动重探（5 次/秒
#: ioctl 成本可忽略，渲染帧本就 30Hz）。
_SIZE_POLL_INTERVAL = 0.2


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
    _build_tree: object
    _model: object
    _apply_fn: object
    _reconciler: object
    _root_fiber: object
    _ink_renderer: object
    _width_cache: object
    _config: object
    _input_fiber: object
    _last_render_width: int
    _last_render_height: int
    _last_size_poll: float
    _resize_pending: bool
    _dirty: bool
    _frame_active: bool
    _system_monitor: object
    _last_sys_stats_time: float
    _sys_stats_interval: float
    _hook_ctx: object

    # ── 命令应用 ─────────────────────────────────────

    def _apply_commands(self, commands: list) -> None:
        """批量应用命令到模型。

        ★ 性能（流式超长单行）：先经 ``coalesce_commands`` 合并本批内相邻的
        CONTENT / REASONING 增量命令——同一帧内多条增量只触发一次 markdown
        预览刷新（超长单行每次刷新都要重渲染 + 重新换行整个尾部窗口）。合并
        为纯追加语义，渲染结果与逐条应用等价。
        """
        if self._apply_fn is None:
            return
        try:
            from src.tui.app.apply import coalesce_commands
            merged = coalesce_commands(commands)
        except Exception:
            _logger.debug("命令合并失败，按原批应用", exc_info=True)
            merged = commands
        for cmd in merged:
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

    # ── 渲染帧 ───────────────────────────────────────

    def _render_frame(self) -> None:
        """构建组件树 → 调和 → 渲染 → 输出 → 光标。

        ★ 帧执行标记（``_frame_active``）：进入置位 / finally 复位——外部
        等待方（flush_input_router / _wait_render_flush / _join_render_thread）
        据此区分「正在执行超长单帧」（单帧耗时无上界，视作有进展续期等待）
        与「渲染线程挂起」（无进展，按软超时/硬上限降级）。

        ★ 渲染互斥（``_render_lock``，可重入）：统一串行化本帧执行——外部
        线程的**同步渲染**（``request_bottom_redraw`` / ``flush_input_router``
        主动渲染）与渲染线程的常规帧互斥，消除「双线程并发写 stream」（P3-20
        输出撕裂）。stub（无 ``_render_lock``）保持既有语义。
        """
        lock = getattr(self, "_render_lock", None)
        if lock is None:
            self._frame_active = True
            try:
                self._render_frame_impl()
            finally:
                self._frame_active = False
            return
        with lock:
            self._frame_active = True
            try:
                self._render_frame_impl()
            finally:
                self._frame_active = False

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

    def _should_reuse_idle_frame(self) -> bool:
        """本拍是否复用上一帧（空闲帧复用——30Hz 节拍不变，省空转重建开销）。

        全部条件满足才复用（任一不满足 → 正常重建，零行为变化）：
          1. 宿主注入空闲预测函数（``set_idle_frame_predicate``）且返回 True
             ——组件树内容在无输入/无命令时是否保持静态由宿主判定（框架无法
             推断宿主的时间驱动动画）；
          2. 已有上一帧（``_last_frame``）；
          3. 无待处理命令（``_cmd_queue`` 为空——命令应用会改模型且置脏）；
          4. 非脏（``_dirty``，输入/命令/系统统计等变更标记）；
          5. 无 resize 挂起、无底部重绘请求；
          6. 无激活的 ``useAnimation`` 动画（动画需逐帧推进）。
        """
        if getattr(self, "_last_frame", None) is None:
            return False
        predicate = getattr(self, "_idle_frame_predicate", None)
        if predicate is None:
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
        try:
            return bool(predicate())
        except Exception:
            _logger.debug("空闲预测函数异常（本帧不复用）", exc_info=True)
            return False

    def _render_frame_impl(self) -> None:
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
        #   写入路径。仅 resize 使用 full=True；其余路径均走增量 diff。
        if getattr(self, "_resize_pending", False):
            self._resize_pending = False
            self._ink_renderer.reset(full=True)
        # ★ 空闲帧复用（2026-10-07 性能优化，用户需求）：宿主声明的「静态
        #   状态」（``set_idle_frame_predicate``）下跳过组件树重建/调和/布局/
        #   绘制，直接复用上一帧——**帧号照常推进（对外仍恒定 30Hz）**，仅省
        #   空转 CPU（无变化帧的重建开销 ≈ 1ms/帧）。判定条件全部满足才复用：
        #   预测函数返回 True + 无待处理命令 + 非脏 + 无 resize + 无重绘请求 +
        #   无激活动画（``useAnimation``）。未注入预测函数时恒不复用（默认
        #   行为与既有 30Hz 全量渲染完全一致）。
        if self._should_reuse_idle_frame():
            frame = self._last_frame
        else:
            element = self._build_tree(self._model, width)
            self._reconciler.render(self._root_fiber, element, width, self._width_cache.get_height())
            frame = _components.render_frame(self._root_fiber, width)
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
        """渲染后定位输入光标（从文档底部相对移动）。

        方向B（2026-08-05）：布局/坐标计算委托 ``_cursor.position_cursor``
        （纯函数模块）；本方法只负责 fiber 获取与异常兜底。

        ★ 2026-08-17（用户需求：轨迹 Trace 不显示光标；2026-08-17 通用化）：
        **模态全屏视图**（``model.fullscreen`` 非空——App 整屏渲染全屏视图
        组件、无输入区）下 ``find_input_fiber`` 返回 None → **隐藏终端光标**
        （避免光标停留在残留位置闪烁）；正常模式（找到 input fiber）→ 显示
        光标并定位。显隐经 ``InkRenderer.set_cursor_visible`` 状态跟踪（仅
        变化时写 DECTCEM 序列，不变帧零输出——每帧调用开销可忽略）。
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
        try:
            self._ink_renderer.set_cursor_visible(True)
        except Exception:
            _logger.debug("show_cursor 异常", exc_info=True)
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


__all__ = ["_SessionFrameMixin", "_safe_int", "_SIZE_POLL_INTERVAL"]
