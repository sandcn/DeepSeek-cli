"""会话级 token 统计与速度跟踪 — 重导出层。

实际实现拆分到三个子模块：
- _stats_core.py   — _Stats 类 + 会话级统计
- _token_speed.py  — _TokenSpeedTracker 类 + 全局速率统计
- _stream_lifecycle.py — 流式生命周期回调
"""

from ._stats_core import (
    _Stats,
    get_session_start_time,
    get_token_stats,
    get_last_tool_parse_elapsed,
    get_last_stream_speed,
    get_total_input_tokens,
    get_total_output_tokens,
    get_total_input_cache_hit_tokens,
    get_total_input_cache_miss_tokens,
    reset_stats,
    accumulate_usage,
    set_tool_parse_elapsed,
    set_stream_speed,
)
from ._token_speed import (
    _TokenSpeedTracker,
    add_token_size,
    get_total_tokens,
    get_token_speed,
    get_short_window_speed,
    get_avg_token_speed,
    get_per_second_speed,
    get_token_speed_snapshot,
    reset_token_speed,
)
from ._stream_lifecycle import (
    set_stream_lifecycle_callbacks,
    _notify_stream_started,
    _notify_stream_ended,
    _notify_stream_progress,
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
