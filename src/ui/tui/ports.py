"""TUI 端口接口 — 六边形架构端口定义

精简记录（2026-05-24 v7）：
  - 移除 IBottomGeometry / IStatusBar / IBottomPanel（仅文档引用，无实际 isinstance 检查）

v8 重构（2026-05-25）：
  - 从 app.py 迁入 ITUIInteraction 作为 Protocol

v11 重构（2026-05-26）：
  - 移除 ISplitScreen / IRenderer 端口
  - 移除 ITUIInteraction.split_enabled
  - 移除 TickParams 导入

v12 清理（2026-05-26）：
  - 移除 ITUIInteraction / TUIInteractionNull / _NullStatusBar / _tui_null_noop
    （死代码，从未接入 app_loop.py）

v13 精简（2026-05-28）：
  - ILockedTerminal 定义已迁至 _terminal.py（就近原则），ports.py 仅保留重导出
"""

from __future__ import annotations

from ._terminal import ILockedTerminal  # noqa: F401


__all__ = [
    "ILockedTerminal",
]
