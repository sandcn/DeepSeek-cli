"""_render_phase — 可插拔渲染管线 Phase 实现。

定义 4 个默认 Phase，按序执行：
  PreUpdatePhase → ContentRenderPhase → BottomBarPhase → CursorPhase

每个 Phase 实现 RenderPhase Protocol，可由环境变量 CHAT_UI_RENDER_PHASES 控制启用。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.engine import TuiEngine
    from ..state.store import TuiState
    from ..infrastructure.protocol import RenderPhase

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 默认 Phase 实现
# ═══════════════════════════════════════════════════════════

class PreUpdatePhase:
    """阶段 1: 面板预更新。

    委托 engine._phase_pre_update_panels()，刷新 SubAgent 面板等。
    """

    def execute(
        self,
        engine: "TuiEngine",
        commands: list,
        state: "TuiState | None",
    ) -> bool:
        engine._phase_pre_update_panels()
        return False  # 面板预更新自身无终端输出


class ContentRenderPhase:
    """阶段 2: 内容渲染。

    直接使用 TuiRenderer 逐条渲染命令（避免通过 _phase_render 循环回策略）。
    """

    def __init__(self, renderer):
        self._renderer = renderer

    def execute(
        self,
        engine: "TuiEngine",
        commands: list,
        state: "TuiState | None",
    ) -> bool:
        if commands:
            from ..commands.types import CmdError
            for cmd in commands:
                try:
                    self._renderer.render(cmd)
                except Exception:
                    _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
            return True
        return False


class BottomBarPhase:
    """阶段 3: 底部栏重绘。

    委托 engine._phase_redraw_bottom(has_commands)。
    当 VNode 路径启用且有 patches 变更时触发重绘。
    
    VNode 路径优化：未来可仅 diff bottom_bar 子树避免冗余重绘，
    当前简化策略为「有命令处理 = 可能变更 = 触发重绘」。
    """

    def execute(
        self,
        engine: "TuiEngine",
        commands: list,
        state: "TuiState | None",
    ) -> bool:
        has_commands = bool(commands)

        # VNode 路径：检查底部栏是否需要重绘
        # （当前简化策略：有命令处理 = 可能变更 = 重绘）
        # 未来可优化为仅 diff bottom_bar 子树
        if getattr(engine, '_use_vnode', False) and getattr(engine, '_old_vnode', None) is not None:
            # 占位：后续可在此处添加 bottom_bar 子树 diff 检查，
            # 仅当子树有变更时才触发重绘
            pass

        engine._phase_redraw_bottom(has_commands)
        return has_commands


class CursorPhase:
    """阶段 4: 光标定位。

    在底部栏重绘后定位光标到输入位置。
    """

    def execute(
        self,
        engine: "TuiEngine",
        commands: list,
        state: "TuiState | None",
    ) -> bool:
        try:
            engine._position_cursor()
        except Exception:
            _logger.debug("CursorPhase 异常", exc_info=True)
        return bool(commands)  # 光标定位本身无内容输出，返回是否有命令
