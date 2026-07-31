"""Token 速度快照惰性加载共享模块 — 零依赖。

消除 bottom_bar/status.py 和 status_bar_widget.py 中的重复实现。
本模块不依赖 src/tui/widgets/bottom_bar/ 下的任何模块，避免循环导入。

迁移说明（2026-07-29 TUI 重构）：
  - 从 src/tui/widgets/_snapshot.py 迁移至 TUI 根层级
  - 导入路径更新为 ..api.stats

消费方说明（2026-07-31 方向F）：本模块仅含私有函数 ``_get_snapshot``，
**非死代码**——被 ``src/tui/_bottom_bar/_bar.py`` 的 ``get_status_elapsed()`` 与
``_format_status()`` 消费（惰性加载 ``get_token_speed_snapshot``，异常静默）。
**保留不删**；恢复 Token 速度展示或迁移至统一快照源时须同步上述消费方。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# ── 模块级缓存 ──────────────────────────────────────────
# P3-18：Optional[callable] → Optional[Callable[[], Any]]（from typing 导入）
_TOKEN_SPEED_SNAPSHOT: Optional[Callable[[], Any]] = None  # 也可赋值为 False（标记不可用）


def _get_snapshot():
    """获取 get_token_speed_snapshot 函数引用（惰性加载，异常静默）。"""
    global _TOKEN_SPEED_SNAPSHOT
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            from ..api.stats import get_token_speed_snapshot
            _TOKEN_SPEED_SNAPSHOT = get_token_speed_snapshot
        except ImportError:
            _TOKEN_SPEED_SNAPSHOT = False  # 标记不可用
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None
