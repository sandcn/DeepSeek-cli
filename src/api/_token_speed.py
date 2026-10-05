"""Token 速率统计器 — 兼容 re-export 层。

实现已下沉核心层 ``core.stats._token_speed``（消除核心层对 api 的反向
依赖）；本模块保留旧路径 ``src.api._token_speed`` 兼容既有调用方/测试
（含 monkeypatch），新代码请使用 ``src.core.stats``。

真源唯一（禁止双份实现漂移）：本模块不再自带 ``_TokenSpeedTracker`` 副本，
全部符号转发核心层实现。
"""

from __future__ import annotations

from ..core.stats._token_speed import (  # noqa: F401
    _TokenSpeedTracker,
    _token_speed,
    add_token_size,
    adjust_token_size,
    get_avg_token_speed,
    get_per_second_speed,
    get_short_window_speed,
    get_token_speed,
    get_token_speed_snapshot,
    get_total_tokens,
    reset_token_speed,
)

__all__ = [
    "_TokenSpeedTracker",
    "_token_speed",
    "add_token_size",
    "adjust_token_size",
    "get_total_tokens",
    "get_token_speed",
    "get_short_window_speed",
    "get_avg_token_speed",
    "get_per_second_speed",
    "get_token_speed_snapshot",
    "reset_token_speed",
]
