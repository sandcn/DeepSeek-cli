"""会话级 token 统计核心 — 兼容 re-export 层。

实现已下沉核心层 ``core.stats._stats_core``（消除核心层对 api 的反向
依赖）；本模块保留旧路径 ``src.api._stats_core`` 兼容既有调用方/测试
（含 monkeypatch），新代码请使用 ``src.core.stats``。

真源唯一（禁止双份实现漂移）：本模块不再自带 ``_Stats`` 副本，全部符号
转发核心层实现。
"""

from __future__ import annotations

from ..core.stats._stats_core import (  # noqa: F401
    _Stats,
    _stats,
    accumulate_usage,
    get_last_stream_speed,
    get_last_tool_parse_elapsed,
    get_session_start_time,
    get_token_stats,
    get_total_input_cache_hit_tokens,
    get_total_input_cache_miss_tokens,
    get_total_input_tokens,
    get_total_output_tokens,
    reset_stats,
    set_stream_speed,
    set_tool_parse_elapsed,
)

__all__ = [
    "_Stats",
    "_stats",
    "get_session_start_time",
    "get_token_stats",
    "get_last_tool_parse_elapsed",
    "get_last_stream_speed",
    "get_total_input_tokens",
    "get_total_output_tokens",
    "get_total_input_cache_hit_tokens",
    "get_total_input_cache_miss_tokens",
    "reset_stats",
    "accumulate_usage",
    "set_tool_parse_elapsed",
    "set_stream_speed",
]
