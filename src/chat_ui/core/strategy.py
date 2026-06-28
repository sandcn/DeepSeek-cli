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

from ...api.stats import get_total_tokens, get_per_second_speed

_logger = logging.getLogger(__name__)


def _read_legacy_fallback() -> bool:
    """读取 CHAT_UI_RENDER_LEGACY_FALLBACK 开关。

    优先通过 FeatureFlags 统一注册表读取，失败时回退到
    直接读取环境变量。

    Returns:
        True 当 CHAT_UI_RENDER_LEGACY_FALLBACK 为启用的真值。
    """
    try:
        from src.shared_events.feature_flags import get_feature_flags
        return get_feature_flags().chat_ui_render_legacy_fallback
    except Exception:
        return os.environ.get("CHAT_UI_RENDER_LEGACY_FALLBACK", "").strip().lower() in (
            "1", "true", "yes", "on"
        )


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
        _legacy_fallback = _read_legacy_fallback()
        if not _legacy_fallback:
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

    def __init__(self, renderer, store, vnode_builder, output_func=None,
                 use_layered=False, output_adapter=None):
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
        self._last_tool_output_text: str = ""  # tool_outputs 最后一项已渲染文本（增量渲染用）
        self._last_notifications_count: int = 0
        self._last_errors_count: int = 0
        self._last_tool_calls: dict = {}
        self._last_tool_results_count: int = 0
        self._animating: bool = False
        self._anim_frame_count: int = 0
        self._anim_idle_count: int = 0
        self._ANIM_IDLE_SKIP_THRESHOLD = 3
        self._ANIM_IDLE_SKIP_FRAMES = 6
        self._frozen_elapsed: float = 0.0

        # ── 层级渲染系统 ──
        self._use_layered: bool = use_layered
        self._output_adapter = output_adapter  # 供 IncrementalLayerRenderer 使用
        self._layer_manager: object | None = None
        self._compositor: object | None = None
        self._layer_renderer: object | None = None

        if self._use_layered:
            # 延迟导入，避免循环依赖
            from ..layer import LayerManager, Compositor, IncrementalLayerRenderer
            from ..layer.types import Layer
            from ..commands.const import _DEFAULT_LAYER_DIFF_THRESHOLD

            terminal_height = 80  # 合理的默认值，运行时会通过 engine 更新
            terminal_width = 120
            self._layer_manager = LayerManager(terminal_height, terminal_width)
            self._compositor = Compositor()
            # layer_renderer 在策略首次渲染时延迟创建（需要 output_adapter）
            self._layer_renderer: IncrementalLayerRenderer | None = None

    # ── 分层渲染回调 ──────────────────────────────

    def _render_node_to_layer(self, vnode: "VNode") -> None:
        """分层渲染回调：将 VNode 内容写入 layer buffer。

        与 _render_node_direct（内联于 render_commands）功能对等，
        但输出目标从终端改为 layer_manager buffer。
        """
        from ..layer.types import Layer
        lm = self._layer_manager

        # ── 容器类型：递归渲染子节点 ──
        if vnode.type == "root":
            for child in vnode.children:
                if child.type == "content_area":
                    for sub_child in child.children:
                        self._render_node_to_layer(sub_child)
                else:
                    self._render_node_to_layer(child)
            return

        # ── 流式文本类型 → Layer.CONTENT ──
        if vnode.type == "answer_block":
            text = vnode.props.get("text", "")
            if text:
                lm.append(Layer.CONTENT, text)
            return
        if vnode.type == "thinking_block":
            text = vnode.props.get("text", "")
            if text:
                lm.append(Layer.CONTENT, text)
            return

        # ── 一次性块类型 ──
        if vnode.type == "user_messages":
            msgs = vnode.props.get("messages", ())
            if msgs:
                for msg in msgs:
                    lm.append(Layer.CONTENT, f"\n  > {msg}")
        elif vnode.type == "tool_outputs":
            outputs = vnode.props.get("outputs", ())
            if outputs:
                for _, text in outputs:
                    lm.append(Layer.CONTENT, text)
        elif vnode.type == "notifications":
            items = vnode.props.get("items", ())
            if items:
                for item in items:
                    lm.append(Layer.OVERLAY, f"\n  · {item}")
        elif vnode.type == "errors":
            items = vnode.props.get("items", ())
            if items:
                for item in items:
                    lm.append(Layer.OVERLAY, f"\n  ! {item}")
        elif vnode.type == "write_lines":
            lines = vnode.props.get("lines", ())
            if lines:
                for line in lines:
                    lm.append(Layer.CONTENT, line)
        elif vnode.type == "tool_calls":
            try:
                calls = vnode.props.get("calls", ())
                if calls:
                    from ..components.message_blocks import ToolCallBlockBox
                    for call in calls:
                        box = ToolCallBlockBox(
                            tool_name=call.get("name", "unknown"),
                            status=call.get("status", "running"),
                            text=call.get("text", ""),
                            params_summary=call.get("params_summary", ""),
                            elapsed_ms=call.get("elapsed_ms", 0.0),
                        )
                        rendered = box.render()
                        if rendered:
                            lm.append(Layer.CONTENT, rendered)
            except Exception:
                _logger.warning("tool_calls 分层渲染异常", exc_info=True)
        elif vnode.type == "tool_results":
            try:
                results = vnode.props.get("results", ())
                if results:
                    from ..components.message_blocks import ToolResultBlockBox
                    for result in results:
                        box = ToolResultBlockBox(
                            tool_name=result.get("name", "unknown"),
                            text=result.get("text", ""),
                            success=result.get("status", "completed") == "completed",
                        )
                        rendered = box.render()
                        if rendered:
                            lm.append(Layer.CONTENT, rendered)
            except Exception:
                _logger.warning("tool_results 分层渲染异常", exc_info=True)
        # input_bar / status_line / completion_popup 由底部栏管理，不在此渲染

    # ── bottom_bar VNode 渲染 ──────────────────────────

    def _render_bottom_bar(self, vnode: "VNode") -> None:
        """渲染 bottom_bar VNode 到终端固定区域。

        从 vnode.props 提取原始数据，实例化 BottomBarContent 组件，
        调用 render() 产出 ANSI 字符串，通过 engine 的 BottomBarBridge
        写入终端固定区域。
        """
        from ..components.bottom_bar_content import BottomBarContent
        from ..infrastructure.status_bar import render_normal, render_streaming_line
        from ..infrastructure.claude_style import _is_claude_style_enabled
        from ..state.tui_state import UISessionState, StreamingState
        import shutil

        props = vnode.props
        status_line = props.get("status")
        input_line = props.get("input_line")
        completion = props.get("completion")
        subagent_slots = props.get("subagent_slots", {})

        import time as _time_module

        # ── 构建 StatusBar 所需的状态行文本 ──
        # 优先从 TuiState.status.model 读取，回退到 BottomBarBridge._model_name
        model_name = status_line.model if (hasattr(status_line, 'model') and status_line.model) else ""
        if not model_name and hasattr(self, '_engine') and self._engine is not None:
            model_name = getattr(self._engine._bb, '_model_name', '')

        # 从 BottomBarBridge 读取真实的流式状态
        # StatusLine.streaming 依赖 CmdStatusUpdate 派发，但 app_loop 不派发该命令，
        # 实际流式状态由 BottomBarBridge._status_active 维护（enable_status/disable_status）
        is_streaming = False
        if hasattr(self, '_engine') and self._engine is not None:
            is_streaming = self._engine._bb.is_status_active

        # 计算本轮耗时
        # 流式时实时计时并更新 _frozen_elapsed，非流式时使用冻结值（停止计时不清0）
        round_elapsed = 0.0
        if hasattr(status_line, 'round_start_time') and status_line.round_start_time > 0:
            if is_streaming:
                round_elapsed = _time_module.time() - status_line.round_start_time
                self._frozen_elapsed = round_elapsed
            else:
                round_elapsed = self._frozen_elapsed
        else:
            round_elapsed = 0.0
            self._frozen_elapsed = 0.0

        # 构建状态文本（工具计数）
        status_text = ""
        tool_count = status_line.tool_count if hasattr(status_line, 'tool_count') else 0
        tool_fail = status_line.tool_fail if hasattr(status_line, 'tool_fail') else 0
        if tool_count > 0:
            status_text = f"⚙{tool_count}"
            if tool_fail > 0:
                status_text += f"!{tool_fail}"

        _output_tokens = get_total_tokens()
        session = UISessionState(
            model=model_name,
            message_count=0,
            input_tokens=0,
            output_tokens=_output_tokens,
            status_text=status_text,
            session_duration=round_elapsed,
            show_duration=True,
            show_tokens=True,
            show_time=True,
            cost_usd=_output_tokens * 0.00001 if _output_tokens > 0 else 0.0,
            context_pct=0.0,  # TuiState 未追踪上下文百分比，保持默认
        )

        claude = _is_claude_style_enabled()

        if is_streaming and not claude:
            streaming = StreamingState()
            streaming.active = True
            # 设置 start_time 使 elapsed property 返回正确的实时耗时
            if round_elapsed > 0:
                streaming.start_time = _time_module.monotonic() - round_elapsed
            streaming.output_tokens = get_total_tokens()
            streaming.speed = get_per_second_speed()
            status_text = render_streaming_line(session, streaming)
        else:
            status_text = render_normal(session)

        # ── 终端宽度 ──
        term_width = shutil.get_terminal_size().columns

        # ── 实例化 BottomBarContent 组件 ──
        component = BottomBarContent(
            term_width=term_width,
            status_text=status_text,
            input_text=input_line.text if hasattr(input_line, 'text') else "",
            input_cursor_pos=input_line.cursor_pos if hasattr(input_line, 'cursor_pos') else 0,
            is_streaming=is_streaming,
            completion_items=tuple(completion.items) if hasattr(completion, 'items') else (),
            completion_selected=completion.selected if hasattr(completion, 'selected') else 0,
            completion_visible=completion.visible if hasattr(completion, 'visible') else False,
            subagent_slots=subagent_slots,
            claude_style=claude,
        )

        # ── 渲染为 ANSI 字符串 ──
        content = component.render()

        # 同步补全弹窗高度到 BottomBarBridge（影响 _bottom_lines 和光标定位）
        completion_visible_val = completion.visible if hasattr(completion, 'visible') else False
        completion_items_tuple = tuple(completion.items) if hasattr(completion, 'items') else ()
        completion_height = 0
        if completion_visible_val and completion_items_tuple:
            # 标题行(1) + 选项行(min(len, _COMPLETION_MAX_ITEMS)) + 快捷键行(1) + 额外1行buffer
            completion_height = 2 + min(len(completion_items_tuple), 10)
        if hasattr(self, '_engine') and self._engine is not None:
            self._engine._bb.set_completion_height(completion_height)

        # ── 通过 engine 的 BottomBarBridge 写入终端固定区域 ──
        if hasattr(self, '_engine') and self._engine is not None:
            self._engine._bb.force_redraw_from_vnode(content)

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
        # 存储 engine 引用，供 _render_bottom_bar 使用
        self._engine = engine

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

            # 6. 导入 VNode 渲染路径所需命令类型（提升到 has_change 块外，避免
            #    条件导入在 has_change=False 时 UnboundLocalError）
            from ..commands.types import CmdContent, CmdReasoning, CmdToolOutput, CmdPhaseDone

            # 空帧计数逻辑：追踪连续无变更帧，供空帧退避使用
            if not has_change:
                self._anim_idle_count += 1
            else:
                self._anim_idle_count = 0

            if has_change:
                # 直接渲染回调：将 VNode 渲染为终端输出
                def _render_node_direct(vnode: "VNode") -> None:
                    # ── 容器类型：递归渲染子节点 ──
                    # root: 递归遍历 children → content_area → children
                    if vnode.type == "root":
                        bottom_bar_vnode = None
                        for child in vnode.children:
                            if child.type == "content_area":
                                for sub_child in child.children:
                                    _render_node_direct(sub_child)
                            elif child.type == "bottom_bar":
                                bottom_bar_vnode = child
                            else:
                                _render_node_direct(child)

                        # 渲染 bottom_bar 到固定区域
                        if bottom_bar_vnode is not None:
                            self._render_bottom_bar(bottom_bar_vnode)
                        return

                    # ── 底部栏渲染 ──
                    if vnode.type == "bottom_bar":
                        self._render_bottom_bar(vnode)
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
                        # user_messages 被清除后重建时（如 editmsg 截断），
                        # _last_user_messages_count 残留旧值，需重置计数器
                        if new_count < self._last_user_messages_count:
                            self._last_user_messages_count = 0
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
                            if outputs:
                                self._last_tool_output_text = outputs[-1][1]
                        elif new_len == old_len and old_len > 0:
                            # 条目数不变但内容变了（reducer 拼接文本到最后一个条目）
                            # ★ 增量渲染：仅输出新增部分（类似 answer_block 的 delta 逻辑），
                            #   避免每次 UPDATE 都重新渲染整个累积文本导致内容重复显示。
                            if outputs[-1] != self._last_tool_outputs[-1]:
                                _, text = outputs[-1]
                                delta = text[len(self._last_tool_output_text):]
                                if delta:
                                    self._renderer.render(CmdToolOutput(text=delta))
                                self._last_tool_output_text = text
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

                # ── 选择渲染回调 ──
                if self._use_layered and self._layer_manager is not None:
                    _render_node = self._render_node_to_layer
                else:
                    _render_node = _render_node_direct

                _apply_patches(self._old_vnode, patches, _render_node)

                if self._use_layered and self._layer_manager is not None:
                    # 合并层级 → 增量输出
                    try:
                        buffers = self._layer_manager.get_all_buffers()
                        frame_lines = self._compositor.composite(buffers)

                        if self._layer_renderer is None:
                            from ..layer import IncrementalLayerRenderer
                            self._layer_renderer = IncrementalLayerRenderer(self._output_adapter)

                        self._layer_renderer.render(frame_lines)
                        self._layer_manager.clear_all()
                    except Exception:
                        _logger.warning("分层渲染异常，回退", exc_info=True)

                _logger.debug("VNode diff 检测到 %d 个 patches，已触发增量渲染", len(patches))

            # ── 路由 CmdPhaseDone 到 TuiRenderer ──
            # VNode 路径下 cmd 仅 dispatch 到 Store，CmdPhaseDone 还需路由到
            # TuiRenderer 以触发 IncrementalRenderer.close() → parser flush，
            # 确保缓冲区中无换行结尾的末尾 token 得以输出到终端。
            for cmd in commands:
                if isinstance(cmd, CmdPhaseDone):
                    try:
                        self._renderer.render(cmd)
                    except Exception:
                        _logger.debug("CmdPhaseDone 渲染异常", exc_info=True)

            # ★ 防御性检测：CmdPhaseDone 处理完后不应再有 CmdContent 在同一帧中
            #   （PhaseDone 已关闭 IncrementalRenderer，后续 CmdContent.write() 会因
            #    self._closed=True 直接 return，导致数据静默丢失）
            #   仅当 CmdPhaseDone 在命令列表中出现在 CmdContent 之后时才告警——
            #   因为 dispatch→VNode→patches 阶段已处理完所有 CmdContent 的渲染，
            #   PhaseDone 在最后出现是正常时序（close 刷出 buffer 残余）。
            phase_done_indices = [
                i for i, c in enumerate(commands) if isinstance(c, CmdPhaseDone)
            ]
            if phase_done_indices:
                content_indices = [
                    i for i, c in enumerate(commands) if isinstance(c, CmdContent)
                ]
                if content_indices and max(phase_done_indices) < min(content_indices):
                    # CmdPhaseDone 在 CmdContent 之前 → 渲染器已关闭，后续内容丢失
                    trailing = [c for c in commands if isinstance(c, CmdContent)]
                    _logger.warning(
                        "CmdPhaseDone 先于 %d 条 CmdContent 到达，"
                        "渲染器已关闭可能导致数据丢失",
                        len(trailing),
                    )

            # 6. 缓存新树
            self._old_vnode = new_vnode

            # ── 7. 始终渲染底部栏（不依赖 has_change）──
            # 底部栏的数据（model_name 等）可能通过 set_model_name 绕过 TuiStore 更新，
            # 因此需要在每帧都触发底部栏渲染以确保状态行显示最新模型名。
            # force_redraw_from_vnode 内部有快速路径（内容不变时跳过终端 I/O）。
            if new_vnode is not None:
                for child in new_vnode.children:
                    if child.type == "bottom_bar":
                        try:
                            self._render_bottom_bar(child)
                        except Exception:
                            _logger.debug("底部栏渲染异常", exc_info=True)
                        break

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
        _legacy_fallback = _read_legacy_fallback()
        if not _legacy_fallback:
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

        # 防御性检查：始终触发底部栏重绘
        # （原通过 isinstance(phase, BottomBarPhase) 检测，phase.py 已删除）
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
