"""会话级 token 统计与速度跟踪 — 兼容 re-export 层。

实现已下沉核心层 ``core.stats``（消除核心层对 api 的反向依赖）。本模块
保留旧路径 ``src.api.stats`` 兼容既有调用方（含适配器与测试 monkeypatch）；
新代码请使用 ``src.core.stats``。
"""

from __future__ import annotations

from ..core.stats import (  # noqa: F401
    _Stats,
    _TokenSpeedTracker,
    _notify_stream_ended,
    _notify_stream_progress,
    _notify_stream_started,
    accumulate_usage,
    add_token_size,
    get_avg_token_speed,
    get_last_stream_speed,
    get_last_tool_parse_elapsed,
    get_per_second_speed,
    get_session_start_time,
    get_short_window_speed,
    get_token_speed,
    get_token_speed_snapshot,
    get_token_stats,
    get_total_input_cache_hit_tokens,
    get_total_input_cache_miss_tokens,
    get_total_input_tokens,
    get_total_output_tokens,
    get_total_tokens,
    reset_stats,
    reset_token_speed,
    set_stream_lifecycle_callbacks,
    set_stream_speed,
    set_tool_parse_elapsed,
)

__all__ = [
    "_Stats",
    "_TokenSpeedTracker",
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
    "add_token_size",
    "get_total_tokens",
    "get_token_speed",
    "get_short_window_speed",
    "get_avg_token_speed",
    "get_per_second_speed",
    "get_token_speed_snapshot",
    "reset_token_speed",
    "set_stream_lifecycle_callbacks",
    "_notify_stream_started",
    "_notify_stream_ended",
    "_notify_stream_progress",
]
