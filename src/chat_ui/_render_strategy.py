"""chat_ui 渲染策略模块 — RenderStrategy Protocol + 两种策略实现。

从 _engine.py 拆分，将 5 种渲染策略从 if/else 分支重构为策略类。
TuiEngine.__init__ 一次性选择策略，_drain_queue() 不再含每帧策略分支。

策略：
  - DirectRenderStrategy：默认策略，直接渲染命令
  - VNodeRenderStrategy：VNode Diff 增量渲染策略
"""

from __future__ import annotations

import logging
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ._engine import TuiEngine
    from ._vnode import VNode

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# RenderStrategy Protocol
# ═══════════════════════════════════════════════════════════

class RenderStrategy(Protocol):
    """渲染策略协议：定义内容渲染接口，不含帧调度。

    所有渲染策略必须实现 render_commands() 方法。
    start() / stop() 为可选生命周期方法。
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
# DirectRenderStrategy — 直接渲染
# ═══════════════════════════════════════════════════════════

class DirectRenderStrategy:
    """默认策略：逐条命令通过 TuiRenderer 直接渲染。"""

    def __init__(self, renderer):
        self._renderer = renderer

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """启动策略（无操作）。"""
        pass

    def stop(self) -> None:
        """停止策略（无操作）。"""
        pass

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
        return self._render_direct(engine, commands)

    def _render_direct(self, engine: "TuiEngine", commands: list) -> bool:
        """默认路径：逐条命令通过 TuiRenderer 直接渲染。"""
        from ._cmd import CmdError
        for cmd in commands:
            try:
                self._renderer.render(cmd)
            except Exception:
                _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                engine.push_cmd(CmdError(message=f"渲染命令 {type(cmd).__name__} 失败"))
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
            return False
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
            from ._vnode import diff as _vnode_diff, apply_patches as _apply_patches, PatchKind
            patches = _vnode_diff(self._old_vnode, new_vnode)

            # 5. 检测是否有实质性变更
            has_change = any(p.kind != PatchKind.NOOP for p in patches)

            if has_change:
                # 渲染回调：将 VNode 渲染为终端输出
                def _render_node(vnode: "VNode") -> None:
                    if vnode.type == "thinking_block":
                        text = vnode.props.get("text", "")
                        if text and self._output:
                            self._output(f"\n  {text}")
                    elif vnode.type == "answer_block":
                        text = vnode.props.get("text", "")
                        if text and self._output:
                            self._output(text)
                    elif vnode.type == "user_messages":
                        for msg in vnode.props.get("messages", ()):
                            if self._output:
                                self._output(f"\n  > {msg}")
                    elif vnode.type == "tool_outputs":
                        for output in vnode.props.get("outputs", ()):
                            if self._output:
                                self._output(f"   {output}")
                    elif vnode.type == "notifications":
                        for item in vnode.props.get("items", ()):
                            if self._output:
                                self._output(f"\n  · {item}")
                    elif vnode.type == "errors":
                        for item in vnode.props.get("items", ()):
                            if self._output:
                                self._output(f"\n  ! {item}")
                    elif vnode.type == "write_lines":
                        for line in vnode.props.get("lines", ()):
                            if self._output:
                                self._output(f"{line}\n")
                    # input_bar / status_line / completion_popup 由底部栏管理，不在此渲染
                    # subagent_frames 由面板回调管理

                _apply_patches(self._old_vnode, patches, _render_node)
                _logger.debug("VNode diff 检测到 %d 个 patches，已触发增量渲染", len(patches))

            # 6. 缓存新树
            self._old_vnode = new_vnode

            return has_change

        except Exception:
            _logger.warning("VNode Diff 渲染异常，回退到直接渲染", exc_info=True)
            # 回退：逐条直接渲染
            from ._cmd import CmdError
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
    """可插拔 Phase 管线渲染策略。

    将渲染流程拆分为独立的 Phase（PreUpdate → ContentRender → BottomBar → Cursor），
    每个 Phase 实现 RenderPhase Protocol。
    """

    def __init__(self, renderer, phases: list, store=None):
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
        from ._render_phase import BottomBarPhase
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
        from ._const import _FIXED_FRAME_INTERVAL
        FRAME_INTERVAL = _FIXED_FRAME_INTERVAL  # 0.016 (~62.5fps)
        while self._get_running():
            frame_start = _time.monotonic()
            self._drain()
            elapsed = _time.monotonic() - frame_start
            if elapsed < FRAME_INTERVAL:
                _time.sleep(FRAME_INTERVAL - elapsed)

    def _run_adaptive(self) -> None:
        """自适应渲染循环（有内容时 5ms，空闲时逐渐增加到 100ms）"""
        from ._const import _ACTIVE_RENDER_INTERVAL, _IDLE_DRAIN_THRESHOLD, _RENDER_INTERVAL
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
