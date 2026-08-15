"""_SessionFrameMixin — InkSession 渲染帧执行子域（架构改进方向 A，2026-08-16）。

拆分背景：InkSession（原 ~1540 行）为「上帝类」——命令队列/线程生命周期/
渲染循环/崩溃恢复/hooks 等职责混杂。方向 A 按**可独立测试的职责边界**
拆分：渲染帧执行（组件树构建 + 调和 + 渲染 + 光标 + 宽度传播 + 系统监控）
收敛为本 mixin，命令入队/背压/排空安全为 ``_session_queue_mixin._SessionQueueMixin``，
session 保留渲染循环调度（_render/_drain_queue/_should_render）与生命周期。

本 mixin 承载：
  - ``_render_frame`` — 构建组件树 → 调和 → 渲染 → 输出 → 光标（含 resize
    宽度/高度传播、全量刷新标志、input-area fiber 缓存）；
  - ``_apply_commands`` — 批量应用命令到模型（CLEAR_MSGS 置 resize 全量刷新）；
  - ``_update_system_stats`` — 每 2 秒采集 CPU/MEM 写入模型（输入区分隔线）；
  - ``_position_cursor`` / ``_find_input_fiber`` — 输入光标定位（委托纯函数
    模块 ``._cursor``）。

依赖约定（由 InkSession.__init__ 初始化，运行时经 ``self`` 访问）：
  - ``_build_tree`` / ``_model`` / ``_apply_fn`` — 组件树构建与命令应用注入；
  - ``_reconciler`` / ``_root_fiber`` — React Ink 调和器与根 fiber；
  - ``_ink_renderer`` / ``_width_cache`` / ``_config`` — 渲染器与尺寸缓存；
  - ``_input_fiber`` / ``_last_render_width`` / ``_last_render_height`` /
    ``_resize_pending`` / ``_dirty`` — 帧状态（缓存/全量刷新/脏标记）；
  - ``_system_monitor`` / ``_last_sys_stats_time`` / ``_sys_stats_interval``
    — 系统监控状态。

行为零变化（2026-08-16 拆分确认）：方法体为原 InkSession 同名方法原样
迁移——测试以实例属性替换（monkeypatch）方法仍生效（本 mixin 方法即实例
方法）。
"""

from __future__ import annotations

import logging
import time

from src.tui._const import RenderCommand
from src.tui.ink._cmd_priority import _get_cmd_id, _cmd_name
from src.tui.ink import components as _components
from src.tui.ink import hooks as _hooks
from src.tui.ink import _cursor

_logger = logging.getLogger(__name__)


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
    _resize_pending: bool
    _dirty: bool
    _system_monitor: object
    _last_sys_stats_time: float
    _sys_stats_interval: float

    # ── 命令应用 ─────────────────────────────────────

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

    # ── 渲染帧 ───────────────────────────────────────

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

    # ── 系统监控 ─────────────────────────────────────

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


__all__ = ["_SessionFrameMixin", "_safe_int"]
