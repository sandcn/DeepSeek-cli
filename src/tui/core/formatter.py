"""文本格式化工具函数 — 零依赖核心层，避免循环导入。

从 parallel/_text_formatter.py 的 TextFormatter 类提取纯格式化函数，
下沉到 core 层以消除循环依赖（cost → parallel → frame → components → cost）。

所有函数为纯函数，无副作用，无 I/O，不依赖任何外部模块。
"""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """格式化持续时间为可读格式。

    < 60s → "Xs"
    < 3600s → "XmYs"
    >= 3600s → "XhYm"

    Args:
        seconds: 秒数。

    Returns:
        格式化后的时间字符串。
    """
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours = minutes // 60
    minutes %= 60
    return f"{hours}h{minutes}m"


def format_token_count(tokens: int) -> str:
    """格式化 token 数为可读格式（含 k 后缀）。

    < 1000 → 整数显示
    >= 1000 → "X.Xk"（一位小数）

    Args:
        tokens: token 数量。

    Returns:
        格式化后的 token 数字符串。
    """
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


def format_compact_speed(speed: float) -> str:
    """格式化紧凑速度，始终使用 /s。

    Args:
        speed: 速率（个/秒）。

    Returns:
        格式化后的速率字符串（如 "15.3/s"）。
    """
    if speed <= 0:
        return "0/s"
    if speed >= 0.1:
        value = f"{speed:.1f}"
    else:
        value = f"{speed:.2f}"
    value = value.rstrip("0").rstrip(".")
    return f"{value}/s"


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
    "format_duration",
    "format_token_count",
    "format_compact_speed",
    "format_elapsed",
    "format_speed",
]
