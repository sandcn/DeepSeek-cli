"""时间/速率格式化工具 — 消除 TUI 中重复的时间/速率格式化逻辑

消除 status_bar.py:render_streaming_line() 中重复的 elapsed time 和 tok/s 格式化实现。

用法：
    from ._time_format import format_elapsed, format_speed
    dur_str = format_elapsed(3.5)    # → "3.5s"
    dur_str = format_elapsed(125)    # → "2:05"
    speed_str = format_speed(120.0)  # → "120"
    speed_str = format_speed(0.75)   # → "0.75"
"""

from __future__ import annotations


def format_elapsed(seconds: float) -> str:
    """格式化运行时间（秒）为人类可读字符串。

    < 60 秒 → "3.5s"（一位小数）
    >= 60 秒 → "2:05"（分:秒，秒补零）
    >= 3600 秒 → "1:02:34"（时:分:秒）

    Args:
        seconds: 运行时间（秒）。

    Returns:
        格式化后的时间字符串。
    """
    if seconds < 0:
        return "0.0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}:{secs:02d}"
    hours = mins // 60
    mins %= 60
    return f"{hours}:{mins:02d}:{secs:02d}"


def format_speed(tok_per_sec: float) -> str:
    """格式化 token 速率为人类可读字符串。

    >= 10 → "120"（整数，无小数）
    1 ~ 10 → "5.3"（一位小数）
    < 1 → "0.75"（两位小数）
    < 0 → "0.0"

    Args:
        tok_per_sec: token 速率（个/秒）。

    Returns:
        格式化后的速率字符串。
    """
    if tok_per_sec < 0:
        return "0.0"
    if tok_per_sec >= 10:
        return f"{tok_per_sec:.0f}"
    if tok_per_sec >= 1:
        return f"{tok_per_sec:.1f}"
    return f"{tok_per_sec:.2f}"


__all__ = [
    "format_elapsed",
    "format_speed",
]
