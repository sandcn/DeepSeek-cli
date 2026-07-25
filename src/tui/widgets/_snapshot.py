"""Token 速度快照惰性加载共享模块 — 零依赖。

消除 bottom_bar/status.py 和 status_bar_widget.py 中的重复实现。
本模块不依赖 src/tui/widgets/bottom_bar/ 下的任何模块，避免循环导入。
"""

from __future__ import annotations

from typing import Optional

# ── 模块级缓存 ──────────────────────────────────────────
_TOKEN_SPEED_SNAPSHOT: Optional[callable] = None  # 也可赋值为 False（标记不可用）


def _get_snapshot():
    """获取 get_token_speed_snapshot 函数引用（惰性加载，异常静默）。"""
    global _TOKEN_SPEED_SNAPSHOT
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            from ...api.stats import get_token_speed_snapshot
            _TOKEN_SPEED_SNAPSHOT = get_token_speed_snapshot
        except ImportError:
            _TOKEN_SPEED_SNAPSHOT = False  # 标记不可用
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None
