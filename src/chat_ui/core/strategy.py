"""chat_ui 渲染策略模块 — RenderStrategy Protocol + 两种策略实现。

从 _engine.py 拆分，将 5 种渲染策略从 if/else 分支重构为策略类。
TuiEngine.__init__ 一次性选择策略，_drain_queue() 不再含每帧策略分支。

策略：
  - DirectRenderStrategy：默认策略，同时处理直接渲染和 Rich Live 两种子路径
  - VNodeRenderStrategy：VNode Diff 增量渲染策略
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.engine import TuiEngine
    from ..vdom.vnode import VNode

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# RenderStrategy Protocol
# ═══════════════════════════════════════════════════════════

class RenderStrategy(Protocol):
    """渲染策略协议：定义内容渲染接口，不含帧调度。

    所有渲染策略必须实现 render_commands() 方法。
    start() / stop() 为可选生命周期方法（DirectRenderStrategy
    用于管理 Rich Live 上下文，VNodeRenderStrategy 为空操作）。
    """

    def render_commands(
        self, engine: "TuiEngine", commands: list,
        output_lock_held: bool = True,
    ) -> bool:
        """渲染一批命令。返回是否有内容输出（用于底部栏重绘决策）。"""
        ...

    def start(self) -> None:
        """启动策略（可选生命周期方法）。"""
        ...

    def stop(self) -> None:
        """停止策略（可选生命周期方法）。"""
        ...


# ═══════════════════════════════════════════════════════════
# DirectRenderStrategy — 默认 + Rich Live 双路径
# ═══════════════════════════════════════════════════════════

class DirectRenderStrategy:
    """@deprecated: 默认策略：逐条命令通过 TuiRenderer 直接渲染。

    已由 VNodeRenderStrategy 取代。仅在 CHAT_UI_RENDER_LEGACY_FALLBACK=1 环境变量
    设置时允许无警告使用，否则发出 DeprecationWarning。

    通过构造函数中的环境变量 CHAT_UI_RENDER_USE_RICH_LIVE 选择子路径：
      - 默认：逐条 dispatch 到 TuiRenderer.render()
      - Rich Live：内容命令走 Rich Live 缓冲区差分渲染，其余走直接渲染
    """

    def __init__(self, renderer):
        import warnings
        if not os.environ.get("CHAT_UI_RENDER_LEGACY_FALLBACK", "").strip().lower() in (
            "1", "true", "yes", "on"
        ):
            warnings.warn(
                "DirectRenderStrategy 已废弃，请使用 VNodeRenderStrategy。"
                "设置 CHAT_UI_RENDER_LEGACY_FALLBACK=1 可消除此警告。",
                DeprecationWarning,
                stacklevel=2,
            )
        self._renderer = renderer
        self._use_rich_live: bool = (
            os.environ.get("CHAT_UI_RENDER_USE_RICH_LIVE", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        self._rich_renderer = None
        if self._use_rich_live:
            # 使用公开 render_state 属性（避免 getattr 访问私有 _rs）
            _render_state = renderer.render_state
            if _render_state is not None:
                from ..core.renderer import RichLiveContentRenderer
                self._rich_renderer = RichLiveContentRenderer(
                    _render_state, renderer.output_adapter
                )
            if self._rich_renderer is None or not self._rich_renderer.available:
                _logger.warning("Rich Live 不可用（缺少 rich 库），回退到手动渲染")
                self._rich_renderer = None
                self._use_rich_live = False

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """启动 Rich Live 上下文（若启用）。"""
        if self._rich_renderer is not None:
            try:
                self._rich_renderer.start()
            except Exception:
                _logger.warning("Rich Live 启动失败", exc_info=True)

    def stop(self) -> None:
        """停止 Rich Live 上下文（若启用）。"""
        if self._rich_renderer is not None:
            try:
                self._rich_renderer.stop()
            except Exception:
                _logger.warning("Rich Live 停止失败", exc_info=True)

    # ── 核心渲染 ──────────────────────────────────

    def render_commands(
        self, engine: "TuiEngine", commands: list,
        output_lock_held: bool = True,
    ) -> bool:
        """渲染一批命令。

        Args:
            engine: TuiEngine 实例（用于 push_cmd 错误反馈）
            commands: 渲染命令列表
            output_lock_held: 是否已持有输出锁（默认 True）

        Returns:
            是否有内容输出（始终返回 True 当 commands 非空时）
        """
        if not commands:
            return False
        # ── 前置设置（从 _drain_queue 上移）──
        try:
            engine._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        engine.ensure_cursor_upper()
        if self._rich_renderer is not None:
            return self._render_rich_live(engine, commands)
        return self._render_direct(engine, commands)

    def _render_direct(self, engine: "TuiEngine", commands: list) -> bool:
        """默认路径：逐条命令通过 TuiRenderer 直接渲染。"""
        from ..commands.types import CmdError
        for cmd in commands:
            try:
                self._renderer.render(cmd)
            except Exception:
                _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                engine.push_cmd(CmdError(message=f"渲染命令 {type(cmd).__name__} 失败"))
        return True

    def _render_rich_live(self, engine: "TuiEngine", commands: list) -> bool:
        """Rich Live 路径：内容命令走差分渲染，其余命令直出。"""
        from ..commands.types import CmdContent, CmdReasoning, CmdError
        has_content = False
        for cmd in commands:
            if isinstance(cmd, (CmdContent, CmdReasoning)):
                self._rich_renderer.update_content(cmd.text)
                has_content = True
            else:
                try:
                    self._renderer.render(cmd)
                except Exception:
                    _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                    engine.push_cmd(CmdError(message=f"渲染命令 {type(cmd).__name__} 失败"))
        if has_content:
            try:
                self._rich_renderer.refresh()
            except Exception:
                _logger.debug("Rich Live 渲染异常", exc_info=True)
        return True


# ═══════════════════════════════════════════════════════════
# VNodeRenderStrategy — VNode Diff 增量渲染
# ═══════════════════════════════════════════════════════════

class VNodeRenderStrategy:
    """VNode Diff 增量渲染策略。

    每帧将命令 dispatch 到 TuiStore（不可变状态），构建 VNode 树，
    Diff 新旧树，仅在有实质性变更时触发渲染。

    管理自己的 _old_vnode 缓存，供下一帧 diff 使用。
    """

    def __init__(self, renderer, store, vnode_builder, output_func=None):
        self._renderer = renderer
        self._store = store
        self._build_vnode = vnode_builder  # build_vnode_tree 函数
        self._old_vnode: "VNode | None" = None
        self._output = output_func  # 输出函数: callable(str) → 写终端
        self._last_answer_text: str = ""  # answer_block 增量渲染缓存（假设文本只增不减）
        self._last_reasoning_text: str = ""  # thinking_block 增量渲染缓存
        self._last_write_lines_count: int = 0
        self._last_user_messages_count: int = 0
        self._last_tool_outputs: tuple = ()
        self._last_notifications_count: int = 0
        self._last_errors_count: int = 0
        self._last_tool_calls: dict = {}
        self._last_tool_results_count: int = 0
        self._animating: bool = False
        self._anim_frame_count: int = 0
        self._anim_idle_count: int = 0
        self._ANIM_IDLE_SKIP_THRESHOLD = 3
        self._ANIM_IDLE_SKIP_FRAMES = 6

    # ── 动画活跃状态控制 ──────────────────────────

    def set_animating(self, active: bool) -> None:
        """设置动画活跃状态（由 _drain_queue 在动画滴答时调用）。

        当 AnimationClock 产生 tick 时，_drain_queue 调用此方法
        通知策略当前有活跃动画，允许空命令列表触发 VNode 重建。
        """
        self._animating = active

    # ── 生命周期（空操作）────────────────────────

    def start(self) -> None:
        """VNode 策略无需生命周期管理。"""
        pass

    def stop(self) -> None:
        """VNode 策略无需生命周期管理。"""
        pass

    # ── 核心渲染 ──────────────────────────────────

    def render_commands(
        self, engine: "TuiEngine", commands: list,
        output_lock_held: bool = True,
    ) -> bool:
        """VNode Diff 渲染路径。

        1. 逐条 dispatch commands 到 TuiStore
        2. 获取最新 TuiState
        3. 构建新 VNode 树
        4. Diff 新旧树
        5. 若有实质性变更，通过 apply_patches + render_cb 增量渲染
        6. 缓存新树供下一帧 diff

        Returns:
            是否有实质性变更（用于底部栏重绘决策）
        """
        if not commands:
            if not self._animating:
                return False
            # 动画活跃但无命令：触发 VNode 重建以反映动画状态变化
            # 两级退避：空闲阈值触发后，再跳过 _ANIM_IDLE_SKIP_FRAMES 帧才真正休眠
            if self._anim_idle_count >= self._ANIM_IDLE_SKIP_THRESHOLD:
                self._anim_frame_count += 1
                if self._anim_frame_count < self._ANIM_IDLE_SKIP_FRAMES:
                    return False
                self._anim_frame_count = 0
        else:
            self._anim_frame_count = 0
            self._anim_idle_count = 0
        # ── 前置设置（从 _drain_queue 上移）──
        try:
            engine._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        engine.ensure_cursor_upper()
        try:
            # 1. Dispatch 所有命令到 Store
            for cmd in commands:
                try:
                    self._store.dispatch(cmd)
                except Exception:
                    _logger.debug("VNode dispatch %s 失败", type(cmd).__name__, exc_info=True)

            # 2. 获取最新状态
            state = self._store.get_state()

            # 3. 构建新 VNode 树
            new_vnode = self._build_vnode(state)

            # 4. Diff
            from ..vdom.vnode import diff as _vnode_diff, apply_patches as _apply_patches, PatchKind
            patches = _vnode_diff(self._old_vnode, new_vnode)

            # 5. 检测是否有实质性变更
            has_change = any(p.kind != PatchKind.NOOP for p in patches)

            # 空帧计数逻辑：追踪连续无变更帧，供空帧退避使用
            if not has_change:
                self._anim_idle_count += 1
            else:
                self._anim_idle_count = 0

            if has_change:
                # 渲染回调所需命令类型
                from ..commands.types import CmdContent, CmdReasoning, CmdToolOutput
                # 渲染回调：将 VNode 渲染为终端输出
                def _render_node(vnode: "VNode") -> None:
                    # ── 容器类型：递归渲染子节点 ──
                    # root: 递归遍历 children → content_area → children
                    if vnode.type == "root":
                        for child in vnode.children:
                            if child.type == "content_area":
                                for sub_child in child.children:
                                    _render_node(sub_child)
                            else:
                                _render_node(child)
                        return

                    # ── 流式文本类型 ──
                    # answer_block: 增量渲染（仅输出新增文本，避免全量覆写叠加）
                    # 通过 TuiRenderer.render(CmdContent) 路由到 IncrementalRenderer，
                    # 确保 Markdown → ANSI 渲染管线完整（代码高亮/表格/粗体等样式）。
                    # thinking_block: 同样通过 TuiRenderer 路由（支持 Markdown 思考内容）
                    if vnode.type == "answer_block":
                        text = vnode.props.get("text", "")
                        if text:
                            delta = text[len(self._last_answer_text):]
                            if delta:
                                # 路由到 TuiRenderer → IncrementalRenderer 完成 Markdown→ANSI
                                self._renderer.render(CmdContent(text=delta))
                            self._last_answer_text = text
                        return
                    if vnode.type == "thinking_block":
                        # 思考内容也通过 IncrementalRenderer 渲染（支持代码块等 Markdown）
                        text = vnode.props.get("text", "")
                        if text:
                            delta = text[len(self._last_reasoning_text):]
                            if delta:
                                self._renderer.render(CmdReasoning(text=delta))
                            self._last_reasoning_text = text
                        return

                    # ── 一次性块类型：增量渲染（仅输出新增条目，避免每帧重复）──
                    if vnode.type == "user_messages":
                        msgs = vnode.props.get("messages", ())
                        new_count = len(msgs)
                        if new_count > self._last_user_messages_count:
                            for msg in msgs[self._last_user_messages_count:]:
                                if self._output:
                                    self._output(f"\n  > {msg}")
                            self._last_user_messages_count = new_count
                    elif vnode.type == "tool_outputs":
                        outputs = vnode.props.get("outputs", ())
                        old_len = len(self._last_tool_outputs)
                        new_len = len(outputs)
                        if new_len > old_len:
                            # 有新条目：通过 TuiRenderer 路由（ToolOutputBlock 处理 ANSI + dim 样式）
                            for output in outputs[old_len:]:
                                _, text = output  # (name, text) 元组
                                self._renderer.render(CmdToolOutput(text=text))
                        elif new_len == old_len and old_len > 0:
                            # 条目数不变但内容变了（最后一项被追加修改）
                            if outputs[-1] != self._last_tool_outputs[-1]:
                                _, text = outputs[-1]
                                self._renderer.render(CmdToolOutput(text=text))
                        self._last_tool_outputs = outputs
                    elif vnode.type == "notifications":
                        items = vnode.props.get("items", ())
                        new_count = len(items)
                        if new_count > self._last_notifications_count:
                            for item in items[self._last_notifications_count:]:
                                if self._output:
                                    self._output(f"\n  · {item}")
                            self._last_notifications_count = new_count
                    elif vnode.type == "errors":
                        items = vnode.props.get("items", ())
                        new_count = len(items)
                        if new_count > self._last_errors_count:
                            for item in items[self._last_errors_count:]:
                                if self._output:
                                    self._output(f"\n  ! {item}")
                            self._last_errors_count = new_count
                    elif vnode.type == "write_lines":
                        lines = vnode.props.get("lines", ())
                        new_count = len(lines)
                        if new_count > self._last_write_lines_count:
                            for line in lines[self._last_write_lines_count:]:
                                if self._output:
                                    self._output(line)
                            self._last_write_lines_count = new_count
                    elif vnode.type == "subagent_frames":
                        frames = vnode.props.get("frames", ())
                        # 仅渲染最新帧 — SubagentFrameRenderer 依赖 last_lines
                        # 进行增量原地刷新（ANSI 转义序列坐标计算基于上一帧行数），
                        # 全量重放历史帧会因 last_lines 过期导致定位错误。
                        # VNode diff 保证仅在帧内容变化时才触发此回调。
                        if frames:
                            from ..components.subagent_frame import SubagentFrameRenderer
                            renderer = SubagentFrameRenderer()
                            try:
                                renderer.render(frames[-1], self._renderer.output_adapter)
                            except Exception:
                                pass
                    elif vnode.type == "tool_calls":
                        try:
                            calls = vnode.props.get("calls", ())
                            if calls:
                                from ..components.message_blocks import ToolCallBlockBox
                                for call in calls:
                                    tool_id = call.get("tool_id", "")
                                    status = call.get("status", "running")
                                    # 增量跟踪：仅渲染新增或状态变更的条目
                                    prev = self._last_tool_calls.get(tool_id)
                                    if prev is not None and prev == status:
                                        continue
                                    self._last_tool_calls[tool_id] = status
                                    box = ToolCallBlockBox(
                                        tool_name=call.get("name", "unknown"),
                                        status=status,
                                        text=call.get("text", ""),
                                        params_summary=call.get("params_summary", ""),
                                        elapsed_ms=call.get("elapsed_ms", 0.0),
                                    )
                                    rendered = box.render()
                                    if rendered and self._output:
                                        self._output(rendered)
                        except Exception:
                            _logger.warning("tool_calls 渲染异常", exc_info=True)
                    elif vnode.type == "tool_results":
                        try:
                            results = vnode.props.get("results", ())
                            if results:
                                # 仅输出新增条目（增量跟踪）
                                new_count = len(results)
                                if new_count > self._last_tool_results_count:
                                    from ..components.message_blocks import ToolResultBlockBox
                                    for result in results[self._last_tool_results_count:]:
                                        box = ToolResultBlockBox(
                                            tool_name=result.get("name", "unknown"),
                                            text=result.get("text", ""),
                                            success=result.get("status", "completed") == "completed",
                                        )
                                        rendered = box.render()
                                        if rendered and self._output:
                                            self._output(rendered)
                                    self._last_tool_results_count = new_count
                        except Exception:
                            _logger.warning("tool_results 渲染异常", exc_info=True)
                    # input_bar / status_line / completion_popup 由底部栏管理，不在此渲染

                _apply_patches(self._old_vnode, patches, _render_node)
                _logger.debug("VNode diff 检测到 %d 个 patches，已触发增量渲染", len(patches))

            # 6. 缓存新树
            self._old_vnode = new_vnode

            return has_change

        except Exception:
            _logger.warning("VNode Diff 渲染异常，回退到直接渲染", exc_info=True)
            # 回退：逐条直接渲染
            from ..commands.types import CmdError
            for cmd in commands:
                try:
                    self._renderer.render(cmd)
                except Exception:
                    _logger.debug("回退渲染 %s 失败", type(cmd).__name__, exc_info=True)
            return True


# ═══════════════════════════════════════════════════════════
# PhaseRenderStrategy — 可插拔 Phase 管线策略
# ═══════════════════════════════════════════════════════════

class PhaseRenderStrategy:
    """@deprecated: 可插拔 Phase 管线渲染策略。

    已由 VNodeRenderStrategy 取代。仅在 CHAT_UI_RENDER_LEGACY_FALLBACK=1 环境变量
    设置时允许无警告使用，否则发出 DeprecationWarning。

    将渲染流程拆分为独立的 Phase（PreUpdate → ContentRender → BottomBar → Cursor），
    每个 Phase 实现 RenderPhase Protocol。
    """

    def __init__(self, renderer, phases: list, store=None):
        import warnings
        if not os.environ.get("CHAT_UI_RENDER_LEGACY_FALLBACK", "").strip().lower() in (
            "1", "true", "yes", "on"
        ):
            warnings.warn(
                "PhaseRenderStrategy 已废弃，请使用 VNodeRenderStrategy。"
                "设置 CHAT_UI_RENDER_LEGACY_FALLBACK=1 可消除此警告。",
                DeprecationWarning,
                stacklevel=2,
            )
        self._renderer = renderer
        self._phases = phases
        self._store = store

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """Phase 管线策略无需生命周期管理。"""
        pass

    def stop(self) -> None:
        """Phase 管线策略无需生命周期管理。"""
        pass

    # ── 核心渲染 ──────────────────────────────────

    def render_commands(
        self, engine: "TuiEngine", commands: list,
        output_lock_held: bool = True,
    ) -> bool:
        """按 Phase 管线顺序执行渲染。

        每个 Phase.execute() 接收 (engine, commands, state)，
        state 来自 TuiStore（如果可用），否则为 None。
        """
        if not commands:
            return False

        state = None
        if self._store is not None:
            state = self._store.get_state()

        # 按顺序执行所有 phases
        has_any_content = False
        for phase in self._phases:
            try:
                result = phase.execute(engine, commands, state)
                if result:
                    has_any_content = True
            except Exception:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning("Phase %s 执行失败", type(phase).__name__, exc_info=True)

        # 防御性检查：如果 phases 中没有 BottomBarPhase，手动触发底部栏重绘
        # 惰性导入 BottomBarPhase 以避免循环依赖：
        # strategy → phase → engine → strategy。移到模块顶部会导致 ImportError。
        from ..core.phase import BottomBarPhase
        has_bottom_bar_phase = any(isinstance(p, BottomBarPhase) for p in self._phases)
        if not has_bottom_bar_phase:
            engine._phase_redraw_bottom(has_any_content)

        # 命令已处理：即使所有 phase 都返回 False，也返回 True
        return has_any_content or bool(commands)


# ═══════════════════════════════════════════════════════════
# RenderLoop — 帧调度包装器（固定帧率 vs 自适应）
# ═══════════════════════════════════════════════════════════

import time as _time
import threading as _threading


class RenderLoop:
    """渲染循环包装器：负责帧调度（固定帧率 vs 自适应）。

    将帧调度逻辑从 TuiEngine._render() 提取到此包装器，
    使 TuiEngine._render() 变为简单的委托。
    """

    def __init__(self, drain_fn, cmd_event, get_running, use_fixed_fps: bool):
        """参数：
        - drain_fn: callable → bool  (调用 engine._drain_queue)
        - cmd_event: threading.Event (命令就绪事件)
        - get_running: callable → bool (检查是否仍在运行)
        - use_fixed_fps: bool
        """
        self._drain = drain_fn
        self._event = cmd_event
        self._get_running = get_running
        self._use_fixed_fps = use_fixed_fps

    def run(self) -> None:
        """运行渲染循环（阻塞，直到 _get_running() 返回 False）"""
        if self._use_fixed_fps:
            self._run_fixed_fps()
        else:
            self._run_adaptive()

    def _run_fixed_fps(self) -> None:
        """固定帧率渲染循环 (~62.5fps)"""
        from ..commands.const import _FIXED_FRAME_INTERVAL
        FRAME_INTERVAL = _FIXED_FRAME_INTERVAL  # 0.016 (~62.5fps)
        while self._get_running():
            frame_start = _time.monotonic()
            self._drain()
            elapsed = _time.monotonic() - frame_start
            if elapsed < FRAME_INTERVAL:
                _time.sleep(FRAME_INTERVAL - elapsed)

    def _run_adaptive(self) -> None:
        """自适应渲染循环（有内容时 5ms，空闲时逐渐增加到 100ms）"""
        from ..commands.const import _ACTIVE_RENDER_INTERVAL, _IDLE_DRAIN_THRESHOLD, _RENDER_INTERVAL
        IDLE_THRESHOLD = _IDLE_DRAIN_THRESHOLD  # 连续空闲轮次阈值
        ACTIVE_INTERVAL = _ACTIVE_RENDER_INTERVAL  # 5ms
        IDLE_INTERVAL = _RENDER_INTERVAL  # 100ms

        idle_count = 0
        while self._get_running():
            has_content = self._drain()

            if has_content:
                idle_count = 0
                self._event.wait(timeout=ACTIVE_INTERVAL)
            else:
                idle_count += 1
                if idle_count >= IDLE_THRESHOLD:
                    self._event.wait(timeout=IDLE_INTERVAL)
                else:
                    self._event.wait(timeout=ACTIVE_INTERVAL)
                self._event.clear()
